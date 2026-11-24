"""Model asset contracts — a prediction model is a first-class, versioned asset.

Mirrors the DataFoundry design (registry + performance metadata + lineage), so
factors and models are managed with the same machinery. See docs/design-baseline.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ModelCategory = Literal["gbdt", "linear", "ensemble", "deep_learning", "custom"]
ModelStatus = Literal["beta", "active", "deprecated"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ModelPerformance(_StrictModel):
    """模型在统一基准上的绩效画像 —— 判断"好模型"的客观依据。"""

    ic_mean: float | None = Field(default=None, ge=-1, le=1)
    ic_std: float | None = Field(default=None, ge=0)
    ir: float | None = None
    rank_ic_mean: float | None = Field(default=None, ge=-1, le=1)
    annual_return: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    fit_seconds: float | None = Field(default=None, ge=0)  # 训练耗时（资源成本）
    benchmark: str | None = None      # 评测用的数据集引用
    evaluated_at: datetime | None = None

    @model_validator(mode="after")
    def _derive_ir(self) -> ModelPerformance:
        if self.ir is None and self.ic_mean is not None and self.ic_std:
            self.ir = self.ic_mean / self.ic_std
        return self


class ModelSpec(_StrictModel):
    """一个模型的完整声明。注册一条声明即可接入，不改模板/守卫/镜像。"""

    model_id: str = Field(pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    category: ModelCategory
    status: ModelStatus = "beta"
    description: str = Field(default="", max_length=500)

    # ---- 接入声明（参数化的核心）----
    # import 语句（可多条），注入到生成区；AST 白名单从此动态派生
    imports: list[str] = Field(min_length=1)
    # 构造表达式，用 {params} 占位超参字典，如 "LGBModel(**{params})"
    constructor: str
    # 默认超参
    default_params: dict[str, Any] = Field(default_factory=dict)
    # 超参白名单：仅允许这些 key 透传，防止注入
    allowed_param_keys: set[str] = Field(default_factory=set)

    # ---- 运行约束 ----
    requires_gpu: bool = False
    pip_packages: list[str] = Field(default_factory=list)  # 沙箱镜像需要额外装的包
    min_dataset_rows: int = Field(default=500, ge=1)
    # 适合的场景标签（供推荐器匹配）
    suitable_for: list[str] = Field(default_factory=list)  # e.g. ["small_data", "tabular", "fast"]

    performance: ModelPerformance = Field(default_factory=ModelPerformance)
    owner: str = "system"
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    @field_validator("imports")
    @classmethod
    def _validate_imports(cls, values: list[str]) -> list[str]:
        for stmt in values:
            s = stmt.strip()
            if not (s.startswith("import ") or s.startswith("from ")):
                raise ValueError(f"imports 项须是合法 import 语句: {stmt}")
            if any(bad in s for bad in (";", "__import__", "exec", "eval")):
                raise ValueError(f"imports 项含禁止内容: {stmt}")
        return values

    @model_validator(mode="after")
    def _validate_constructor(self) -> ModelSpec:
        c = self.constructor.strip()
        if "{params}" not in c:
            raise ValueError("constructor 须包含 {params} 占位符")
        if any(bad in c for bad in (";", "__import__", "exec(", "eval(", "open(")):
            raise ValueError("constructor 含禁止内容")
        # 未显式给 allowed_param_keys 时，从 default_params 推导
        if not self.allowed_param_keys:
            self.allowed_param_keys = set(self.default_params.keys())
        return self

    def build_constructor(self, user_params: dict[str, Any] | None = None) -> str:
        """生成构造代码：只允许白名单超参，其余忽略。"""
        params = dict(self.default_params)
        if user_params:
            for k, v in user_params.items():
                if k in self.allowed_param_keys:
                    params[k] = v
        # pformat 风格：用 repr 生成合法 Python 字面量
        params_repr = repr(params)
        return self.constructor.replace("{params}", params_repr)

    @property
    def import_modules(self) -> list[str]:
        """从 import 语句提取模块名，供 AST 白名单派生。"""
        mods: list[str] = []
        for stmt in self.imports:
            s = stmt.strip()
            if s.startswith("from "):
                # from qlib.contrib.model.gbdt import LGBModel
                mods.append(s.split()[1])
            else:
                # import qlib.contrib.model.gbdt  (可能带 as)
                mods.append(s.split()[1].split(" as ")[0])
        return mods
