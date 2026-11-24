"""Data source connectors — pluggable adapters for each upstream.

Each connector yields a raw pandas DataFrame with at least ``symbol``/``date``
columns.  qlib_public reads from a local qlib binary dir; CsvSource reads a
user upload; ApiSource/LLMSource are stubs with a clear contract so real
implementations can be dropped in without changing the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

from app.data.schemas import DataSource


class SourceConnector(ABC):
    def __init__(self, descriptor: DataSource):
        self.descriptor = descriptor

    @abstractmethod
    def fetch(self, fields: list[str] | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        """返回长表：至少含 symbol, date 列，其余为数据字段。"""
        ...


class CsvSourceConnector(SourceConnector):
    """用户上传的 CSV —— 必须有 symbol,date 列。"""

    def __init__(self, descriptor: DataSource, path: str | Path):
        super().__init__(descriptor)
        self.path = Path(path)

    def fetch(self, fields: list[str] | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        df = pd.read_csv(self.path)
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        if fields:
            cols = ["symbol", "date"] + [c for c in fields if c in df.columns]
            df = df[[c for c in cols if c in df.columns]]
        return df


class QlibPublicConnector(SourceConnector):
    """从本地 qlib binary 目录读取公共行情。延迟 import qlib，保持模块轻量。"""

    def fetch(self, fields: list[str] | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        try:
            import qlib
            from qlib.data import D
        except ImportError as e:
            raise RuntimeError("qlib 未安装，无法读取公共行情") from e

        qlib.init(provider_uri=self.descriptor.fields[0] if self.descriptor.fields else "/data/qlib")
        instruments = self.descriptor.symbols or ["all"]
        fields = fields or self.descriptor.fields or ["$close"]
        df = D.features(instruments, fields, start or "2000-01-01", end or "2030-01-01", freq="day")
        # qlib 返回 MultiIndex(instrument, datetime)
        out = df.reset_index()
        out.columns = [("instrument" if c == "instrument" else "datetime" if c == "datetime" else c)
                       for c in out.columns]
        return out.rename(columns={"instrument": "symbol", "datetime": "date"})


class ApiSourceConnector(SourceConnector):
    """外部行情 API 占位 —— 子类实现 fetch 接入具体供应商。"""

    def fetch(self, fields: list[str] | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError("ApiSourceConnector 需由具体供应商子类实现 fetch()")


class LLMDerivedConnector(SourceConnector):
    """LLM 另类数据占位 —— 从文本抽取结构化信号后，产出 (symbol,date,score)。"""

    def fetch(self, fields: list[str] | None = None,
              start: str | None = None, end: str | None = None) -> pd.DataFrame:
        raise NotImplementedError("LLMDerivedConnector 需实现文本→结构化信号的抽取逻辑")


def connector_for(descriptor: DataSource, **kwargs) -> SourceConnector:
    factories = {
        "csv_upload": lambda: CsvSourceConnector(descriptor, kwargs["path"]),
        "qlib_public": lambda: QlibPublicConnector(descriptor),
        "api_external": lambda: ApiSourceConnector(descriptor),
        "llm_derived": lambda: LLMDerivedConnector(descriptor),
        "derived": lambda: ApiSourceConnector(descriptor),
    }
    if descriptor.kind.value not in factories:
        raise ValueError(f"未知数据源类型: {descriptor.kind}")
    return factories[descriptor.kind.value]()
