"""QuantPilot DataFoundry — 数据资产模块.

把原始数据（公共行情、用户上传、外部 API、LLM 抽取）冶炼成
可复用、可追溯、带绩效元数据的数据资产，供 qlib 引擎与 LLM 生成层消费。

设计要点：
- 数据是一等资产：每个因子/数据集/股票池都有唯一 ID、版本、血缘、绩效元数据。
- 引用式生成：LLM 生成策略时引用 @factor:xxx / dataset://xxx，而非现写表达式。
- PIT 时点对齐：财务数据按"公告可用日"对齐，杜绝前视偏差。
- 与 qlib 解耦：自研资产目录，产物落成 qlib 可消费的配置/binary。
"""

from app.data.catalog import AssetCatalog, get_catalog
from app.data.factors import FactorStore, get_factor_store
from app.data.imports import ImportService, get_import_service, set_import_service
from app.data.resolver import DatasetResolver
from app.data.service import DataService, get_data_service
from app.data.seed import seed_catalog

__all__ = [
    "AssetCatalog",
    "get_catalog",
    "FactorStore",
    "get_factor_store",
    "DatasetResolver",
    "DataService",
    "get_data_service",
    "ImportService",
    "get_import_service",
    "set_import_service",
    "seed_catalog",
]
