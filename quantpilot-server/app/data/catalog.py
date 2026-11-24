"""Asset catalog — the searchable registry of all data assets.

Backed by an in-memory dict with optional JSON-file persistence. The interface
is deliberately storage-agnostic so it can be swapped for Postgres/Redis later
without touching callers (see docs/design-baseline.md §1.2 资产域).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

from app.data.schemas import (
    AnyAsset,
    AssetType,
    DataSource,
    DatasetDef,
    Factor,
    LineageEdge,
    QualityReport,
    UniverseAsset,
)

_PREFIX_TO_TYPE: dict[str, AssetType] = {
    "ds:": "datasource",
    "univ:": "universe",
    "factor:": "factor",
    "dataset:": "dataset",
}


def asset_type_of(asset_id: str) -> AssetType | None:
    for prefix, typ in _PREFIX_TO_TYPE.items():
        if asset_id.startswith(prefix):
            return typ
    return None


class AssetCatalog:
    """Thread-safe in-memory catalog with optional JSON persistence.

    Version semantics: an asset is keyed by its ``asset_id``. Registering the
    same id again overwrites (use a new id/version for a genuinely new version).
    """

    def __init__(self, persist_path: Path | None = None):
        self._assets: dict[str, AnyAsset] = {}
        self._quality: dict[str, QualityReport] = {}
        self._lineage: list[LineageEdge] = []
        self._lock = threading.RLock()
        self._persist_path = persist_path
        if persist_path and persist_path.exists():
            self._load(persist_path)

    # ---- register / get ---------------------------------------------------

    def register(self, asset: AnyAsset) -> str:
        with self._lock:
            self._assets[asset.asset_id] = asset
            self._persist()
        return asset.asset_id

    def get(self, asset_id: str) -> AnyAsset | None:
        with self._lock:
            return self._assets.get(asset_id)

    def require(self, asset_id: str) -> AnyAsset:
        asset = self.get(asset_id)
        if asset is None:
            raise KeyError(f"资产不存在: {asset_id}")
        return asset

    def unregister(self, asset_id: str) -> bool:
        with self._lock:
            existed = self._assets.pop(asset_id, None) is not None
            self._lineage = [e for e in self._lineage
                             if e.from_asset != asset_id and e.to_asset != asset_id]
            self._persist()
            return existed

    def list(self, asset_type: AssetType | None = None) -> list[AnyAsset]:
        with self._lock:
            items = list(self._assets.values())
        if asset_type is not None:
            items = [a for a in items if asset_type_of(a.asset_id) == asset_type]
        return items

    # ---- search -----------------------------------------------------------

    def search(self, query: str, asset_type: AssetType | None = None,
               tags: Iterable[str] | None = None, limit: int = 20) -> list[AnyAsset]:
        """Lightweight substring search over name / description / tags / id.

        This is the hook the LLM layer calls to discover reusable assets before
        generating a strategy ("查目录"). A vector index can be layered on later.
        """
        q = query.strip().lower()
        tag_set = set(tags or [])
        results: list[AnyAsset] = []
        for a in self.list(asset_type):
            if tag_set and not tag_set.issubset(set(getattr(a, "tags", []))):
                continue
            if q:
                haystack = " ".join([
                    a.asset_id, a.name, getattr(a, "description", ""),
                    " ".join(getattr(a, "tags", [])),
                ]).lower()
                if q not in haystack:
                    continue
            results.append(a)
        return results[:limit]

    # ---- quality ----------------------------------------------------------

    def set_quality(self, asset_id: str, report: QualityReport) -> None:
        with self._lock:
            self._quality[asset_id] = report
            self._persist()

    def quality_of(self, asset_id: str) -> QualityReport | None:
        with self._lock:
            return self._quality.get(asset_id)

    # ---- lineage ----------------------------------------------------------

    def add_lineage(self, edge: LineageEdge) -> None:
        with self._lock:
            self._lineage.append(edge)
            self._persist()

    def lineage_of(self, asset_id: str) -> list[LineageEdge]:
        with self._lock:
            return [e for e in self._lineage
                    if e.from_asset == asset_id or e.to_asset == asset_id]

    def upstream(self, asset_id: str) -> list[str]:
        """递归找出某资产的全部上游来源（用于追溯前视偏差/数据问题）。"""
        seen: set[str] = set()
        stack = [asset_id]
        while stack:
            cur = stack.pop()
            for e in self._lineage:
                if e.to_asset == cur and e.from_asset not in seen:
                    seen.add(e.from_asset)
                    stack.append(e.from_asset)
        return sorted(seen)

    # ---- typed helpers ----------------------------------------------------

    def factors(self) -> list[Factor]:
        return [a for a in self.list("factor") if isinstance(a, Factor)]

    def datasets(self) -> list[DatasetDef]:
        return [a for a in self.list("dataset") if isinstance(a, DatasetDef)]

    def universes(self) -> list[UniverseAsset]:
        return [a for a in self.list("universe") if isinstance(a, UniverseAsset)]

    def datasources(self) -> list[DataSource]:
        return [a for a in self.list("datasource") if isinstance(a, DataSource)]

    def clear(self) -> None:
        with self._lock:
            self._assets.clear()
            self._quality.clear()
            self._lineage.clear()
            self._persist()

    # ---- persistence ------------------------------------------------------

    def _persist(self) -> None:
        if not self._persist_path:
            return
        payload = {
            "assets": {k: v.model_dump(mode="json") for k, v in self._assets.items()},
            "quality": {k: v.model_dump(mode="json") for k, v in self._quality.items()},
            "lineage": [e.model_dump(mode="json") for e in self._lineage],
        }
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def _load(self, path: Path) -> None:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        factories = {
            "datasource": DataSource, "universe": UniverseAsset,
            "factor": Factor, "dataset": DatasetDef,
        }
        for key, raw in payload.get("assets", {}).items():
            typ = asset_type_of(key)
            if typ and typ in factories:
                try:
                    self._assets[key] = factories[typ].model_validate(raw)
                except Exception:
                    pass  # 跳过损坏条目，不阻断启动
        for key, raw in payload.get("quality", {}).items():
            try:
                self._quality[key] = QualityReport.model_validate(raw)
            except Exception:
                pass
        for raw in payload.get("lineage", []):
            try:
                self._lineage.append(LineageEdge.model_validate(raw))
            except Exception:
                pass


# 模块级单例（生产环境可由 FastAPI dependency 注入替换）
_catalog: AssetCatalog | None = None


def get_catalog() -> AssetCatalog:
    global _catalog
    if _catalog is None:
        _catalog = AssetCatalog()
    return _catalog


def set_catalog(catalog: AssetCatalog) -> None:
    global _catalog
    _catalog = catalog
