"""Factor store — registry + reference parsing + performance-based ranking.

The store makes factors first-class, reusable assets. The LLM generation layer
can discover factors (via catalog.search) and reference them with the compact
``@factor:<id>@<version>`` syntax instead of re-inventing expressions.
"""

from __future__ import annotations

import re
from typing import Iterable

from app.data.catalog import AssetCatalog, get_catalog
from app.data.schemas import Factor

# @factor:momentum_20d@1.0  /  @factor:momentum_20d (implicit latest version)
_REF_RE = re.compile(r"^@factor:([a-z0-9_\-]+)(?:@([0-9.]+))?$")


class FactorStore:
    def __init__(self, catalog: AssetCatalog | None = None):
        self.catalog = catalog or get_catalog()

    @staticmethod
    def parse_reference(ref: str) -> tuple[str, str | None] | None:
        """解析因子引用，返回 (asset_id, version)。version 为 None 表示取最新。"""
        m = _REF_RE.match(ref.strip())
        if not m:
            return None
        return f"factor:{m.group(1)}", m.group(2)

    def register(self, factor: Factor, derive_from: list[str] | None = None) -> str:
        aid = self.catalog.register(factor)
        for src in derive_from or factor.source_refs:
            from app.data.schemas import LineageEdge
            self.catalog.add_lineage(LineageEdge(from_asset=src, to_asset=aid, relation="derived"))
        return aid

    def resolve(self, ref: str) -> Factor:
        """把引用解析为 Factor 实体。支持裸 asset_id、@引用、带版本。"""
        parsed = self.parse_reference(ref)
        if parsed:
            asset_id, version = parsed
        elif ref.startswith("factor:"):
            asset_id, version = ref, None
        else:
            raise ValueError(f"非法因子引用: {ref}")

        factor = self.catalog.get(asset_id)
        if not isinstance(factor, Factor):
            raise KeyError(f"因子不存在: {asset_id}")
        if version is not None and factor.version != version:
            raise ValueError(f"因子版本不匹配: 期望 {version}, 实际 {factor.version}")
        if factor.status != "active":
            raise ValueError(f"因子不可用于生成: {asset_id} 状态={factor.status}")
        return factor

    def expressions(self, refs: Iterable[str]) -> list[str]:
        """把因子引用列表解析为 qlib 表达式列表（供 DatasetResolver 用）。"""
        out: list[str] = []
        for ref in refs:
            f = self.resolve(ref)
            if f.kind == "QLIB_EXPR":
                out.append(f.expression or "")
            elif f.kind in ("TABLE_COL", "LLM_DERIVED"):
                # 表列/LLM 因子需要先物化成 qlib 字段，用 $ 字段引用
                out.append(f"${f.column}")
        return [e for e in out if e]

    def rank(self, tags: Iterable[str] | None = None, min_ic: float | None = None,
             top: int = 10) -> list[Factor]:
        """按 ICIR 排序返回优质因子 —— 可注入 LLM prompt 作为"推荐因子"。"""
        tag_set = set(tags or [])
        cands = [f for f in self.catalog.factors() if f.status == "active"]
        if tag_set:
            cands = [f for f in cands if tag_set & set(f.tags)]
        if min_ic is not None:
            cands = [f for f in cands
                     if f.performance.ic_mean is not None and f.performance.ic_mean >= min_ic]
        cands.sort(key=lambda f: (
            f.performance.ir if f.performance.ir is not None else
            (f.performance.ic_mean or 0.0)
        ), reverse=True)
        return cands[:top]

    def prompt_catalog(self, top: int = 15) -> str:
        """生成可注入 Stage A system prompt 的因子目录文本（因子知识 → LLM）。"""
        lines = ["可用因子目录（可直接在 signal.expression 或 model.signals 中引用）："]
        for f in self.rank(top=top):
            perf = f.performance
            ic = f"{perf.ic_mean:+.3f}" if perf.ic_mean is not None else "n/a"
            ir = f"{perf.ir:.2f}" if perf.ir is not None else "n/a"
            expr = f.expression or (f"${f.column}" if f.column else "")
            lines.append(
                f"- {f.reference} [{f.name}] IC={ic} ICIR={ir} "
                f"方向={f.direction} :: {expr}"
            )
        return "\n".join(lines)


_store: FactorStore | None = None


def get_factor_store() -> FactorStore:
    global _store
    if _store is None:
        _store = FactorStore()
    return _store


def set_factor_store(store: FactorStore) -> None:
    global _store
    _store = store
