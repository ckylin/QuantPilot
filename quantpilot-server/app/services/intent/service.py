"""IntentService — 零代码入口的唯一编排点。

一句话 / 向导选择 / 模板卡，三种输入都收敛成 IntentFrame，
再交给 FrameCompiler 产出 Spec。LLM 只做理解，不做生成。
"""

from __future__ import annotations

import json
from typing import Any

from app.services.intent.clarifier import Question, is_ready, questions_for
from app.services.intent.compiler import CompiledStrategy, FrameCompiler
from app.services.intent.rules import detect_unsupported, heuristic_frame, restate
from app.services.intent.schemas import IntentFrame
from app.services.llm.prompt_loader import PromptLoader, strip_markdown_fence


class IntentService:
    def __init__(self, provider: Any | None = None, prompts: PromptLoader | None = None):
        self._provider = provider
        self.prompts = prompts or PromptLoader()
        self.compiler = FrameCompiler()

    @property
    def provider(self) -> Any:
        if self._provider is None:
            from app.services.llm.provider_factory import get_provider
            self._provider = get_provider()
        return self._provider

    # ---- 对外 -----------------------------------------------------------

    def parse(
        self,
        intent: str,
        structured_params: dict[str, Any] | None = None,
        frame: IntentFrame | dict[str, Any] | None = None,
        *,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        """把用户输入变成「理解结果 + 待确认问题 + 中文参数卡」。

        返回值里**不含表达式与代码**：用户只应看到人话。
        """
        parsed = self._frame(intent, structured_params, frame, use_llm=use_llm)
        questions = questions_for(parsed)
        unsupported = detect_unsupported(intent)

        compiled: CompiledStrategy | None = None
        if parsed.styles:
            try:
                compiled = self.compiler.compile(parsed, intent=intent)
            except Exception:  # 语义层异常不阻断对话，交给用户重新描述
                compiled = None

        restated = parsed.restated
        if unsupported and not parsed.styles:
            # 做不到就直说，不用相近的东西糊弄
            restated = (
                f"你提到{'、'.join(unsupported)}，这需要财报/资金类数据，"
                "我暂时还看不了。换个说法我再试？"
            )

        return {
            "intent": intent,
            "frame": parsed.model_dump(mode="json"),
            "restated": restated,
            "understood": bool(parsed.styles),
            "ready": is_ready(parsed),
            "questions": [q.to_dict() for q in questions],
            "unsupported": detect_unsupported(intent),
            "card": compiled.card if compiled else None,
            "assumptions": compiled.assumptions if compiled else [],
            "caveats": compiled.caveats if compiled else [],
        }

    def compile(self, frame: IntentFrame, intent: str = "") -> CompiledStrategy:
        return self.compiler.compile(frame, intent=intent)

    # ---- 内部 -----------------------------------------------------------

    def _frame(
        self,
        intent: str,
        structured_params: dict[str, Any] | None,
        frame: IntentFrame | dict[str, Any] | None,
        *,
        use_llm: bool,
    ) -> IntentFrame:
        # 引导式入口：前端已经带着结构化选择来了，直接编译，不再消耗 LLM
        if frame is not None:
            parsed = frame if isinstance(frame, IntentFrame) else IntentFrame.model_validate(frame)
            if not parsed.restated:
                parsed.restated = restate(parsed)
            return parsed

        fallback = heuristic_frame(intent, structured_params)
        if not use_llm:
            return fallback

        llm_frame = self._llm_frame(intent, structured_params)
        return llm_frame.filled(fallback) if llm_frame else fallback

    def _llm_frame(
        self,
        intent: str,
        structured_params: dict[str, Any] | None,
    ) -> IntentFrame | None:
        """LLM 只抽槽；输出不合法就放弃，由规则兜底，绝不把异常抛给用户。"""
        try:
            system = self.prompts.system(
                "intent2frame",
                structured_params=json.dumps(structured_params or {}, ensure_ascii=False),
            )
            raw = self.provider.generate(
                system=system,
                user=intent,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            data = json.loads(strip_markdown_fence(raw))
            return IntentFrame.model_validate(data)
        except Exception:
            return None
