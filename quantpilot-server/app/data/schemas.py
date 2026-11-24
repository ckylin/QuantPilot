"""Data asset contracts — single source of truth for the DataFoundry.

Every factor / dataset / universe / datasource is a versioned asset with an
identity, lineage and (for factors) performance metadata. These Pydantic models
mirror ``docs/design-baseline.md`` and are consumed by the catalog, resolver and
LLM prompt layer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AssetType = Literal["datasource", "universe", "factor", "dataset"]
AssetStatus = Literal["draft", "validating", "active", "deprecated"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

class SourceKind(str, Enum):
    QLIB_PUBLIC = "qlib_public"       # qlib 内置公共行情
    CSV_UPLOAD = "csv_upload"         # 用户上传
    API_EXTERNAL = "api_external"     # 外部行情 API
    LLM_DERIVED = "llm_derived"       # LLM 从文本抽取的另类数据
    DERIVED = "derived"               # 由其他资产派生计算


class DataSource(_StrictModel):
    """一份原始数据的来源描述。PIT 属性决定是否可直接用于回测。"""

    asset_id: str = Field(pattern=r"^ds:[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    kind: SourceKind
    description: str = Field(default="", max_length=500)
    # 覆盖的股票代码（glob 或 explicit），如 "SH*" 或具体列表
    symbols: list[str] = Field(default_factory=list)
    coverage_start: date | None = None
    coverage_end: date | None = None
    # Point-in-Time：财务类数据必须有 available_date 字段，否则回测会前视
    point_in_time: bool = True
    # 该数据是否含有"公告可用日"字段（PIT 对齐的依据）
    available_date_field: str | None = None
    fields: list[str] = Field(default_factory=list)  # 提供的字段名
    # 更新频率
    refresh: Literal["realtime", "daily", "weekly", "monthly", "static"] = "daily"
    owner: str = "system"
    tags: list[str] = Field(default_factory=list)
    status: AssetStatus = "active"
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _pit_consistency(self) -> DataSource:
        # 非行情类（财务/另类）若声称 PIT，必须有可用日字段
        if self.kind in (SourceKind.LLM_DERIVED, SourceKind.API_EXTERNAL) and self.point_in_time:
            if not self.available_date_field:
                raise ValueError("另类/API 数据源声称 PIT 时必须提供 available_date_field")
        return self


# ---------------------------------------------------------------------------
# 股票池
# ---------------------------------------------------------------------------

class UniverseAsset(_StrictModel):
    asset_id: str = Field(pattern=r"^univ:[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    # builtin 引用 qlib 指数成分；explicit 用明确代码列表；rule 用筛选规则
    mode: Literal["builtin", "explicit", "rule"]
    builtin_name: Literal["csi300", "csi500", "csi1000", "all"] | None = None
    symbols: list[str] = Field(default_factory=list)
    # rule 模式：形如 {"min_market_cap": 5e9, "exclude_st": true}
    rule: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)  # 依赖的数据源 asset_id
    version: str = "1.0"
    owner: str = "system"
    tags: list[str] = Field(default_factory=list)
    status: AssetStatus = "active"

    @model_validator(mode="after")
    def _mode_consistency(self) -> UniverseAsset:
        if self.mode == "builtin" and not self.builtin_name:
            raise ValueError("mode=builtin 时 builtin_name 必填")
        if self.mode == "explicit" and not self.symbols:
            raise ValueError("mode=explicit 时 symbols 必填")
        if self.mode == "rule" and not self.rule:
            raise ValueError("mode=rule 时 rule 必填")
        return self


# ---------------------------------------------------------------------------
# 因子 + 绩效元数据（平台知识的核心沉淀）
# ---------------------------------------------------------------------------

class FactorPerformance(_StrictModel):
    """因子在历史数据上的绩效画像 —— 让平台知道"什么因子在什么市场有效"。"""

    ic_mean: float | None = Field(default=None, ge=-1, le=1)      # 信息系数均值
    ic_std: float | None = Field(default=None, ge=0)              # IC 波动
    ir: float | None = Field(default=None)                        # ICIR = ic_mean/ic_std
    rank_ic_mean: float | None = Field(default=None, ge=-1, le=1)
    turnover: float | None = Field(default=None, ge=0)            # 换手率
    long_short_return: float | None = None                        # 多空年化收益
    decay: dict[str, float] = Field(default_factory=dict)         # {"d1": 0.03, "d5": 0.02, ...}
    evaluated_on: str | None = None                               # 评测区间/数据集引用
    evaluated_at: datetime | None = None

    @model_validator(mode="after")
    def _derive_ir(self) -> FactorPerformance:
        if self.ir is None and self.ic_mean is not None and self.ic_std:
            self.ir = self.ic_mean / self.ic_std
        return self


class Factor(_StrictModel):
    """一个可复用因子。表达式因子走 qlib Expression；表因子直接引用数据列。"""

    asset_id: str = Field(pattern=r"^factor:[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    # QLIB_EXPR: qlib 表达式; TABLE_COL: 数据表中的列; LLM_DERIVED: LLM 语义打分
    kind: Literal["QLIB_EXPR", "TABLE_COL", "LLM_DERIVED"]
    expression: str | None = None       # kind=QLIB_EXPR 时的 qlib 表达式
    table_ref: str | None = None        # kind=TABLE_COL 时的 datasource asset_id
    column: str | None = None           # kind=TABLE_COL 时的列名
    direction: Literal["desc", "asc"] = "desc"
    version: str = "1.0"
    source_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    performance: FactorPerformance = Field(default_factory=FactorPerformance)
    owner: str = "system"
    status: AssetStatus = "active"
    created_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _kind_consistency(self) -> Factor:
        if self.kind == "QLIB_EXPR" and not self.expression:
            raise ValueError("kind=QLIB_EXPR 时 expression 必填")
        if self.kind == "TABLE_COL" and not (self.table_ref and self.column):
            raise ValueError("kind=TABLE_COL 时 table_ref 与 column 必填")
        if self.kind == "LLM_DERIVED" and not self.table_ref:
            raise ValueError("kind=LLM_DERIVED 时 table_ref（来源数据表）必填")
        return self

    @property
    def reference(self) -> str:
        """LLM/策略中使用的引用字符串，如 @factor:momentum_20d@1.0。"""
        return f"@{self.asset_id}@{self.version}"


# ---------------------------------------------------------------------------
# 数据集（可直接喂给 qlib workflow）
# ---------------------------------------------------------------------------

class DataSegment(_StrictModel):
    train: tuple[date, date]
    valid: tuple[date, date] | None = None
    test: tuple[date, date]

    @model_validator(mode="after")
    def _order(self) -> DataSegment:
        spans = [("train", self.train[0], self.train[1])]
        if self.valid:
            spans.append(("valid", self.valid[0], self.valid[1]))
        spans.append(("test", self.test[0], self.test[1]))
        if any(s > e for _, s, e in spans):
            raise ValueError("区间 start 不得晚于 end")
        for (n1, _, e1), (n2, s2, _) in zip(spans, spans[1:]):
            if s2 <= e1:
                raise ValueError(f"{n1} 与 {n2} 须严格递增（不重叠）")
        return self


class DatasetDef(_StrictModel):
    """一个具名数据集 —— 因子集合 + 标签 + 切分 + 股票池，可复用。"""

    asset_id: str = Field(pattern=r"^dataset:[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    universe_ref: str                       # universe asset_id
    factor_refs: list[str]                  # factor asset_id 列表（或 @factor:xxx@ver）
    label: str = "Ref($close,-2)/Ref($close,-1)-1"
    segments: DataSegment
    # 处理器：alpha158 走 qlib 内置；custom 用 factor_refs 组装
    handler: Literal["alpha158", "custom"] = "custom"
    version: str = "1.0"
    tags: list[str] = Field(default_factory=list)
    owner: str = "system"
    status: AssetStatus = "active"

    @field_validator("asset_id")
    @classmethod
    def _check_prefix(cls, v: str) -> str:
        if not v.startswith("dataset:"):
            raise ValueError("数据集 asset_id 须以 dataset: 开头")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> DatasetDef:
        if self.handler == "custom" and not self.factor_refs:
            raise ValueError("handler=custom 时 factor_refs 必填")
        return self


# ---------------------------------------------------------------------------
# 质量报告 + 血缘
# ---------------------------------------------------------------------------

class QualityCheck(_StrictModel):
    name: str
    passed: bool
    detail: str = ""
    severity: Literal["info", "warning", "error"] = "error"


class QualityReport(_StrictModel):
    asset_id: str
    checks: list[QualityCheck] = Field(default_factory=list)
    passed: bool = True
    checked_at: datetime = Field(default_factory=datetime.now)

    @model_validator(mode="after")
    def _aggregate(self) -> QualityReport:
        self.passed = all(c.passed or c.severity != "error" for c in self.checks)
        return self


class LineageEdge(_StrictModel):
    """血缘边：from_asset 产出了 to_asset。"""

    from_asset: str
    to_asset: str
    relation: Literal["ingested", "derived", "composed", "consumed"] = "derived"
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


# 统一资产类型
AnyAsset = DataSource | UniverseAsset | Factor | DatasetDef
