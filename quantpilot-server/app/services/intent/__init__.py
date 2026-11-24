"""零代码意图层：中文想法 → IntentFrame → StrategySpec。

用户不写代码、不看表达式；表达式只存在于 ``recipes.py``。
"""

from app.services.intent.clarifier import Question, is_ready, questions_for
from app.services.intent.compiler import CompiledStrategy, FrameCompileError, FrameCompiler
from app.services.intent.schemas import IntentFrame
from app.services.intent.service import IntentService

__all__ = [
    "CompiledStrategy",
    "FrameCompileError",
    "FrameCompiler",
    "IntentFrame",
    "IntentService",
    "Question",
    "is_ready",
    "questions_for",
]
