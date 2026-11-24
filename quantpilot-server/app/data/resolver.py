"""DatasetResolver — turn a dataset asset reference into a runnable qlib config.

This is the bridge between the DataFoundry and the existing qlib engine: the
model_workflow_template consumes exactly the dict produced here, so strategies
can say ``dataset://alpha158_cn`` instead of hand-writing handler details.
"""

from __future__ import annotations

import re
from typing import Any

from app.data.catalog import AssetCatalog, get_catalog
from app.data.factors import FactorStore, get_factor_store
from app.data.schemas import DatasetDef, Factor

_DATASET_REF_RE = re.compile(r"^dataset://([a-z0-9_\-]+)(?:@([0-9.]+))?$")


class DatasetResolver:
    def __init__(self, catalog: AssetCatalog | None = None,
                 factors: FactorStore | None = None):
        self.catalog = catalog or get_catalog()
        self.factors = factors or get_factor_store()

    def resolve_dataset(self, ref: str) -> DatasetDef:
        """解析 dataset://xxx 引用或 dataset:xxx asset_id。"""
        m = _DATASET_REF_RE.match(ref.strip())
        if m:
            asset_id = f"dataset:{m.group(1)}"
            version = m.group(2)
        elif ref.startswith("dataset:"):
            asset_id, version = ref, None
        else:
            raise ValueError(f"非法数据集引用: {ref}")
        ds = self.catalog.get(asset_id)
        if not isinstance(ds, DatasetDef):
            raise KeyError(f"数据集不存在: {asset_id}")
        if version is not None and ds.version != version:
            raise ValueError(f"数据集版本不匹配: 期望 {version}, 实际 {ds.version}")
        return ds

    def resolve(self, ref: str) -> dict[str, Any]:
        """产出可直接喂给 model_workflow_template._build_dataset 的配置。"""
        ds = self.resolve_dataset(ref)

        # 校验质量门禁：数据集自身 + 所有依赖因子
        reports = [self.catalog.quality_of(ds.asset_id)]
        for fref in ds.factor_refs:
            try:
                f = self.factors.resolve(fref)
                reports.append(self.catalog.quality_of(f.asset_id))
            except (KeyError, ValueError):
                continue
        failed = [r for r in reports if r is not None and not r.passed]
        if failed:
            raise ValueError(
                "数据集未通过质量门禁，禁止用于回测: "
                + ", ".join(r.asset_id for r in failed)
            )

        # 股票池
        universe = self.catalog.require(ds.universe_ref)
        if universe.mode == "builtin":
            instruments = universe.builtin_name
        elif universe.mode == "explicit":
            instruments = universe.symbols
        else:
            instruments = universe.builtin_name or "all"

        # 因子 → qlib 表达式 + 名称
        if ds.handler == "alpha158":
            feature_exprs: list[str] = []
            feature_names: list[str] = []
        else:
            feature_exprs, feature_names = [], []
            for fref in ds.factor_refs:
                f: Factor = self.factors.resolve(fref)
                expr = self._factor_expression(f)
                feature_exprs.append(expr)
                # 用因子 id 尾段做字段名，保证稳定可读
                feature_names.append(f.asset_id.split(":", 1)[1])

        seg = ds.segments
        segments: dict[str, list[str]] = {
            "train": [str(seg.train[0]), str(seg.train[1])],
            "test": [str(seg.test[0]), str(seg.test[1])],
        }
        if seg.valid:
            segments["valid"] = [str(seg.valid[0]), str(seg.valid[1])]

        return {
            "handler": ds.handler,
            "instruments": instruments,
            "segments": segments,
            "feature_exprs": feature_exprs,
            "feature_names": feature_names,
            "label": ds.label,
            "dataset_ref": ref,
            "dataset_asset_id": ds.asset_id,
        }

    @staticmethod
    def _factor_expression(f: Factor) -> str:
        if f.kind == "QLIB_EXPR":
            return f.expression or ""
        if f.kind in ("TABLE_COL", "LLM_DERIVED"):
            return f"${f.column}"
        raise ValueError(f"未知因子类型: {f.kind}")

    def to_strategy_spec_patch(self, ref: str) -> dict[str, Any]:
        """生成可合并进 StrategySpec 的 model 配置片段。

        让 spec.signal.model 从"写表达式"升级为"引用数据集"：
          signal.type = "model", model.features = "custom",
          model.signals = [解析出的表达式], segments = 数据集切分。
        """
        cfg = self.resolve(ref)
        seg = cfg["segments"]
        # 只产出 ModelConfig 结构（不含 signal.type）；调用方放进 signal.model。
        return {
            "type": "lgbm",
            "features": "custom",
            "signals": cfg["feature_exprs"],
            "label": cfg["label"],
            "segments": {
                "train": tuple(seg["train"]),
                "test": tuple(seg["test"]),
                **({"valid": tuple(seg["valid"])} if "valid" in seg else {}),
            },
            "dataset_ref": ref,
        }


_resolver: DatasetResolver | None = None


def get_resolver() -> DatasetResolver:
    global _resolver
    if _resolver is None:
        _resolver = DatasetResolver()
    return _resolver
