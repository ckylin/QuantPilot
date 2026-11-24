"""Task state machine — explicit, validated transitions.

Mirrors docs/contracts.md §5.1.

    PENDING -> QUEUED -> RUNNING -> SUCCEEDED -> ARCHIVED
                              |-> REPAIRING -> RUNNING
                              |-> FAILED -> ARCHIVED
"""

from __future__ import annotations

from typing import Literal

TaskStatus = Literal[
    "pending", "queued", "running", "repairing",
    "succeeded", "failed", "archived",
]

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    "pending": frozenset({"queued"}),
    "queued": frozenset({"running"}),
    "running": frozenset({"succeeded", "repairing", "failed"}),
    "repairing": frozenset({"running", "failed"}),
    "succeeded": frozenset({"archived"}),
    "failed": frozenset({"archived"}),
    "archived": frozenset(),
}

# 前端进度页 stage 映射（api-contract.md §3.2）
STAGE_LABELS = {
    "intent": "理解你的想法",
    "spec": "生成策略规格",
    "code": "生成并校验代码",
    "backtest": "回测计算",
    "result": "整理结果",
}
STAGES = list(STAGE_LABELS)


class InvalidTransition(ValueError):
    pass


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def transition(current: TaskStatus, target: TaskStatus) -> TaskStatus:
    if not can_transition(current, target):
        raise InvalidTransition(f"非法状态转移: {current} -> {target}")
    return target


def progress_for(stage: str | None) -> int:
    """粗粒度进度百分比（供前端进度环）。"""
    if not stage or stage not in STAGES:
        return 0
    return int((STAGES.index(stage) + 1) / len(STAGES) * 100)
