"""零代码意图层测试：规则解析 / 配方编译 / 澄清 / 接口契约。

核心断言是"用户侧看不到表达式与代码"——这是产品定位，不是实现细节。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import create_app
from app.api import deps as api_deps
from app.orchestration import TaskService
from app.services.executor.sandbox import RESULT_MARKER, SandboxExecutor
from app.services.intent import IntentFrame, IntentService
from app.services.intent.clarifier import is_ready, questions_for
from app.services.intent.compiler import FrameCompiler
from app.services.intent.rules import heuristic_frame
from app.services.llm.provider import MockProvider, OfflineProvider


# ---------- 规则解析 ----------

def test_rules_extract_core_slots():
    frame = heuristic_frame("沪深300里涨得最猛的20只，每周换")
    assert frame.universe == "csi300"
    assert frame.styles == ["momentum"]
    assert frame.topk == 20
    assert frame.rebalance == "week"
    assert frame.confidence >= 0.7


def test_negation_is_respected():
    frame = heuristic_frame("不要涨得太猛的，帮我挑点稳的，10只")
    assert "momentum" not in frame.styles
    assert "low_volatility" in frame.styles


def test_unsupported_terms_are_detected():
    frame = heuristic_frame("找ROE高、市盈率低的公司")
    assert frame.styles == []
    assert not is_ready(frame)


def test_vague_input_asks_for_style():
    frame = heuristic_frame("随便")
    assert frame.styles == []
    questions = questions_for(frame)
    assert questions[0].key == "styles"
    assert questions[0].blocking


def test_universe_question_is_not_blocking():
    frame = heuristic_frame("涨得最猛的20只")
    assert frame.universe is None
    questions = {q.key: q for q in questions_for(frame)}
    assert "universe" in questions
    assert questions["universe"].blocking is False
    assert is_ready(frame)


# ---------- 编译 ----------

def test_compiler_builds_valid_spec():
    frame = heuristic_frame("沪深300里涨得最猛的20只，每周换")
    compiled = FrameCompiler().compile(frame, intent="test")
    spec = compiled.spec
    assert spec.signal.type == "expression"
    assert spec.portfolio.topk == 20
    assert spec.rebalance.freq == "week"
    assert spec.meta.tags == ["momentum"]
    # 必须记录替用户做的默认决定
    assert any("回测" in a for a in compiled.assumptions)
    assert compiled.card["标的"] == "沪深300"
    assert compiled.card["多久换"] == "每周"


def test_multi_style_combines_with_rank():
    frame = heuristic_frame("又便宜又稳的，每月换")
    compiled = FrameCompiler().compile(frame)
    expression = compiled.spec.signal.expression
    assert "Rank(" in expression
    assert compiled.spec.signal.direction == "desc"
    assert compiled.spec.meta.tags == ["multi_factor"]
    assert compiled.caveats  # price_low 的代理说明必须带上


def test_model_frame_builds_model_signal():
    frame = heuristic_frame("让AI自己学规律选股，买30只")
    compiled = FrameCompiler().compile(frame)
    assert compiled.spec.signal.type == "model"
    assert compiled.spec.signal.model is not None
    assert compiled.spec.portfolio.topk == 30


def test_compiled_spec_passes_ast_guard():
    from app.services.llm.ast_guard import check_code
    from app.services.llm.codegen_service import CodeGenerator
    from app.services.llm.prompt_loader import PromptLoader

    prompts = PromptLoader()
    spec = FrameCompiler().compile(heuristic_frame("沪深300涨得最猛的20只")).spec
    code = CodeGenerator(OfflineProvider(), prompts).render_deterministic(spec)
    passed, errors = check_code(code, prompts.code_template(spec.signal.type))
    assert passed, errors


# ---------- 服务层 ----------

def test_parse_response_contains_no_expression():
    svc = IntentService(provider=OfflineProvider())
    payload = svc.parse("沪深300里涨得最猛的20只，每月换")
    blob = json.dumps(payload, ensure_ascii=False)
    assert "$close" not in blob
    assert "Ref(" not in blob
    assert payload["ready"] is True
    assert payload["card"]["挑什么"]


def test_parse_is_honest_about_missing_data():
    svc = IntentService(provider=OfflineProvider())
    payload = svc.parse("帮我找ROE高、市盈率低的优质公司")
    assert payload["understood"] is False
    assert "净资产收益率" in payload["unsupported"]
    assert "看不了" in payload["restated"]


def test_llm_frame_is_merged_with_rules():
    # LLM 只给了风格，池子与频率由规则补齐
    provider = MockProvider(frame_response={
        "universe": None, "styles": ["reversal"], "topk": None,
        "rebalance": None, "risk_level": None, "need_model": False,
        "restated": "在中证500里挑超跌股", "confidence": 0.9, "missing": [],
    })
    svc = IntentService(provider=provider)
    payload = svc.parse("中证500里跌得多的，每周换")
    assert payload["frame"]["styles"] == ["reversal"]
    assert payload["frame"]["universe"] == "csi500"
    assert payload["frame"]["rebalance"] == "week"


def test_guided_frame_skips_llm():
    class BoomProvider:
        name = "boom"

        def generate(self, **kwargs):
            raise AssertionError("引导式入口不应调用 LLM")

    svc = IntentService(provider=BoomProvider())
    payload = svc.parse(
        "",
        frame={"universe": "csi500", "styles": ["low_volatility"], "topk": 20,
               "rebalance": "month", "confidence": 1.0},
    )
    assert payload["ready"] is True
    assert payload["card"]["标的"] == "中证500"


# ---------- API ----------

def fake_runner(script_path, task_id, timeout):
    result = {
        "task_id": task_id, "status": "done",
        "metrics": {"annual_return": 0.12, "sharpe": 1.6, "max_drawdown": -0.07,
                    "win_rate": 0.55, "turnover": 0.3, "calmar": 1.7},
        "series": {"nav": [], "benchmark_nav": [], "drawdown": []},
        "contributions": [], "holdings": [], "logs_ref": "mem://log",
    }
    return RESULT_MARKER + json.dumps(result) + "\n", "", 0, False


@pytest.fixture()
def client() -> TestClient:
    api_deps.set_task_service(TaskService(
        provider=OfflineProvider(),
        executor=SandboxExecutor(runner="fake", fake_runner=fake_runner),
    ))
    api_deps.set_intent_service(IntentService(provider=OfflineProvider()))
    return TestClient(create_app())


def test_parse_endpoint(client):
    r = client.post("/api/v1/intents/parse", json={"intent": "沪深300涨得最猛的20只，每月换"})
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["questions"] == []
    assert "$close" not in json.dumps(body, ensure_ascii=False)


def test_parse_endpoint_asks_when_vague(client):
    r = client.post("/api/v1/intents/parse", json={"intent": "随便"})
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is False
    assert body["questions"][0]["key"] == "styles"
    assert len(body["questions"][0]["options"]) == 4


def test_submit_with_frame_runs_pipeline(client):
    r = client.post("/api/v1/strategies", json={
        "intent": "",
        "frame": {"universe": "csi300", "styles": ["momentum"], "topk": 20,
                  "rebalance": "month", "confidence": 1.0},
    })
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    result = client.get(f"/api/v1/tasks/{task_id}/result")
    assert result.status_code == 200, result.text
    plan = result.json()["plan"]
    assert plan["card"]["多久换"] == "每月"
    assert plan["assumptions"]


def test_submit_requires_intent_or_frame(client):
    r = client.post("/api/v1/strategies", json={"intent": "  "})
    assert r.status_code == 422
