"""Shared test fixtures."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make ``app`` importable when pytest is launched from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 测试永远不碰真实数据库。开发机的 .env 一旦配了 QP_DATABASE_URL，
# 涉及 create_app() 的用例就会去连那台 PG：本机没起库时卡在连接超时，
# 起了库则污染真实数据。这里在 import app 之前把地址清空，
# 需要库的用例自己调 configure_database("sqlite://") 显式指定。
os.environ["QP_DATABASE_URL"] = ""

BASE_SPEC = {
    "spec_version": "0.1",
    "meta": {
        "name": "沪深300动量Top20月度调仓",
        "intent": "沪深300里动量最强的20只股票，每月调仓",
        "tags": ["momentum"],
    },
    "universe": {"type": "builtin", "name": "csi300"},
    "date_range": {"start": "2020-01-01", "end": "2024-12-31"},
    "rebalance": {"freq": "month"},
    "signal": {
        "type": "expression",
        "expression": "$close / Ref($close, 20) - 1",
        "direction": "desc",
    },
    "portfolio": {"selection": "topk", "topk": 20, "weighting": "equal"},
    "benchmark": "csi300",
}


@pytest.fixture
def base_spec() -> dict:
    """A fresh copy of the baseline Spec dict on every test."""
    import copy

    return copy.deepcopy(BASE_SPEC)
