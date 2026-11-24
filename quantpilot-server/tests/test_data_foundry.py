"""Tests for the DataFoundry module — contracts, catalog, PIT, quality, resolver."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.catalog import AssetCatalog
from app.data.factors import FactorStore
from app.data.normalize import normalize_frame, normalize_symbol, pit_align, winsorize
from app.data.quality import run_quality_checks
from app.data.resolver import DatasetResolver
from app.data.schemas import (
    DataSegment,
    DataSource,
    DatasetDef,
    Factor,
    FactorPerformance,
    LineageEdge,
    QualityCheck,
    QualityReport,
    SourceKind,
    UniverseAsset,
)
from app.data.seed import seed_catalog
from app.data.service import DataService


@pytest.fixture()
def svc() -> DataService:
    cat = AssetCatalog()
    fs = FactorStore(cat)
    service = DataService(cat, fs)
    seed_catalog(service)
    return service


# ---- 契约校验 -----------------------------------------------------------

def test_datasource_pit_requires_available_date_field():
    with pytest.raises(ValueError):
        DataSource(asset_id="ds:bad", name="bad", kind=SourceKind.LLM_DERIVED,
                   point_in_time=True, available_date_field=None)


def test_factor_kind_consistency():
    with pytest.raises(ValueError):
        Factor(asset_id="factor:bad", name="bad", kind="QLIB_EXPR", expression=None)
    with pytest.raises(ValueError):
        Factor(asset_id="factor:bad2", name="bad2", kind="TABLE_COL", table_ref="ds:x", column=None)


def test_factor_reference_format():
    f = Factor(asset_id="factor:momentum_20d", name="m", kind="QLIB_EXPR",
               expression="$close", version="2.1")
    assert f.reference == "@factor:momentum_20d@2.1"


def test_ir_auto_derived():
    p = FactorPerformance(ic_mean=0.04, ic_std=0.08)
    assert p.ir == pytest.approx(0.5)


def test_dataset_segments_must_be_increasing():
    with pytest.raises(ValueError):
        DataSegment(train=(date(2020, 1, 1), date(2021, 12, 31)),
                    test=(date(2021, 6, 1), date(2022, 12, 31)))


def test_dataset_custom_requires_factors():
    with pytest.raises(ValueError):
        DatasetDef(asset_id="dataset:bad", name="bad", universe_ref="univ:csi300",
                   factor_refs=[], handler="custom",
                   segments=DataSegment(train=(date(2020, 1, 1), date(2021, 1, 1)),
                                        test=(date(2022, 1, 1), date(2023, 1, 1))))


# ---- 标准化 / PIT --------------------------------------------------------

def test_normalize_symbol_variants():
    assert normalize_symbol("600000.SH") == "SH600000"
    assert normalize_symbol("sz000001") == "SZ000001"
    assert normalize_symbol("000001") == "SZ000001"
    assert normalize_symbol("601318") == "SH601318"


def test_pit_align_prevents_lookahead():
    # 财报：报告期 2020-12-31，但 2021-04-30 才公布
    df = pd.DataFrame({
        "symbol": ["SH600000", "SH600000"],
        "date": ["2020-12-31", "2021-03-31"],
        "ann_date": ["2021-04-30", "2021-04-30"],
        "eps": [1.5, 1.6],
    })
    out = pit_align(df, "date", "ann_date")
    # 两条记录的生效日都不应早于公布日 2021-04-30
    assert (pd.to_datetime(out["date"]) >= pd.Timestamp("2021-04-30")).all()
    assert "_raw_ann_date" in out.columns


def test_normalize_frame_dedup_and_sort():
    df = pd.DataFrame({
        "symbol": ["000001", "600000.SH", "000001"],
        "date": ["2021-01-02", "2021-01-01", "2021-01-02"],
        "close": [10.0, 20.0, 10.5],
    })
    out = normalize_frame(df)
    assert len(out) == 2
    assert list(out["symbol"]) == ["SH600000", "SZ000001"]  # 按代码排序
    assert out.iloc[1]["close"] == 10.5  # 重复取最后


def test_winsorize_clips_extremes():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 1000.0])
    out = winsorize(s, lower=0.1, upper=0.9)
    assert out.max() < 1000.0


# ---- 质量门禁 ------------------------------------------------------------

def test_quality_gate_blocks_bad_symbols():
    df = pd.DataFrame({"symbol": ["SH600000", "BAD"], "date": ["2021-01-01", "2021-01-02"]})
    report = run_quality_checks(df)
    assert not report.passed
    assert any(c.name == "symbol_format" and not c.passed for c in report.checks)


def test_quality_report_aggregation():
    report = QualityReport(asset_id="x", checks=[
        QualityCheck(name="a", passed=True),
        QualityCheck(name="b", passed=False, severity="warning"),
    ])
    assert report.passed  # warning 不阻断
    report2 = QualityReport(asset_id="y", checks=[
        QualityCheck(name="c", passed=False, severity="error"),
    ])
    assert not report2.passed


def test_ingest_produces_report(svc):
    df = pd.DataFrame({"symbol": ["600000"], "date": ["2021-01-01"], "close": [10.0]})
    src = DataSource(asset_id="ds:test", name="test", kind=SourceKind.CSV_UPLOAD)
    normalized, report = svc.ingest(df, src)
    assert normalized.iloc[0]["symbol"] == "SH600000"
    assert report.asset_id == "ds:test"


# ---- 目录 / 检索 / 血缘 ---------------------------------------------------

def test_seed_catalog_populated(svc):
    assert len(svc.catalog.datasources()) == 1
    assert len(svc.catalog.universes()) == 3
    assert len(svc.catalog.factors()) == 6
    assert len(svc.catalog.datasets()) == 2


def test_search_by_name_and_tag(svc):
    hits = svc.discover("动量", asset_type="factor")
    assert any("momentum" in a.asset_id for a in hits)
    tagged = svc.discover("", asset_type="factor", tags=["低波"])
    assert any(a.asset_id == "factor:volatility_20d" for a in tagged)


def test_lineage_upstream_traced(svc):
    upstream = svc.upstream("dataset:momentum_cn")
    assert "factor:momentum_20d" in upstream
    assert "univ:csi300" in upstream
    assert "ds:qlib_cn" in upstream


def test_catalog_persistence(tmp_path, svc):
    path = tmp_path / "catalog.json"
    cat2 = AssetCatalog(persist_path=path)
    for a in svc.catalog.list():
        cat2.register(a)
    cat3 = AssetCatalog(persist_path=path)
    assert len(cat3.list()) == len(svc.catalog.list())
    assert cat3.get("factor:momentum_20d") is not None


# ---- 因子引用 / 解析器 ----------------------------------------------------

def test_factor_reference_parsing():
    fs = FactorStore()
    assert fs.parse_reference("@factor:momentum_20d@1.0") == ("factor:momentum_20d", "1.0")
    assert fs.parse_reference("@factor:momentum_20d") == ("factor:momentum_20d", None)
    assert fs.parse_reference("momentum_20d") is None


def test_resolve_factor_ref(svc):
    f = svc.factors.resolve("@factor:momentum_20d@1.0")
    assert f.name == "20日动量"
    with pytest.raises(ValueError):
        svc.factors.resolve("@factor:momentum_20d@9.9")  # 版本不匹配


def test_resolve_dataset_expressions(svc):
    cfg = svc.resolve_dataset("dataset://momentum_cn")
    assert cfg["handler"] == "custom"
    assert "$close/Ref($close,20)-1" in cfg["feature_exprs"]
    assert cfg["feature_names"] == ["momentum_20d", "reversal_5d", "volume_ratio"]
    assert "train" in cfg["segments"] and "test" in cfg["segments"]


def test_resolve_alpha158_dataset(svc):
    cfg = svc.resolve_dataset("dataset://alpha158_cn")
    assert cfg["handler"] == "alpha158"
    assert cfg["feature_exprs"] == []


def test_dataset_ref_patch_merges_into_spec(svc):
    from app.schemas import StrategySpec
    patch = svc.dataset_spec_patch("dataset://momentum_cn")
    assert patch["dataset_ref"] == "dataset://momentum_cn"
    assert patch["features"] == "custom"
    # 构造完整 spec，验证 dataset_ref 字段被接受
    spec = StrategySpec.model_validate({
        "spec_version": "0.1",
        "meta": {"name": "动量实验", "intent": "测试数据集引用"},
        "universe": {"type": "builtin", "name": "csi300"},
        "date_range": {"start": "2015-01-01", "end": "2024-12-31"},
        "rebalance": {"freq": "month"},
        "signal": {"type": "model", "model": patch},
        "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
    })
    assert spec.signal.model.dataset_ref == "dataset://momentum_cn"
    assert len(spec.signal.model.signals) == 3


def test_quality_gate_blocks_dataset_resolution(svc):
    # 给动量数据集打上失败的质量报告
    failing = QualityReport(asset_id="dataset:momentum_cn", checks=[
        QualityCheck(name="coverage", passed=False, severity="error"),
    ])
    svc.catalog.set_quality("dataset:momentum_cn", failing)
    with pytest.raises(ValueError, match="质量门禁"):
        svc.resolve_dataset("dataset://momentum_cn")


def test_prompt_catalog_ranks_by_ir(svc):
    text = svc.factor_catalog_prompt()
    lines = [l for l in text.splitlines() if l.startswith("- ")]
    # 第一行应是 ICIR 最高的 momentum_20d (0.50)
    assert "momentum_20d" in lines[0]
