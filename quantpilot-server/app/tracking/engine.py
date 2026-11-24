"""日更打分引擎的接口与默认实现。

职责边界（重要）：
- **引擎只负责两件事**：给当天的行情上下文，和给每个标的打分；
- **组合构造、撮合、记账、判定都不在引擎里**（见 ``rules.build_targets`` 与 ``TrackService``）。
  这样换成 qlib / 自研因子库 / 回放数据源，都不用改记账与判定逻辑。

P1.6 的现实：日更行情管道还没接（需 qlib 增量数据 + 交易日历/停牌/涨跌停表）。
所以默认引擎是 ``UnavailableEngine``——**明确报不可用，绝不造一份假行情出来**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable


class EngineUnavailable(RuntimeError):
    """行情或打分能力当前不可用。调用方必须把原因写进 journal，不能静默。"""


@dataclass
class Bar:
    """单只标的的当日行情。

    ``suspended=True`` 时仍需给出 ``close``（停牌前收盘价），否则净值会跳空。
    """

    symbol: str
    open: float
    close: float
    prev_close: float
    suspended: bool = False
    limit_up: float | None = None
    limit_down: float | None = None


@dataclass
class DayContext:
    """一天的行情上下文。"""

    trade_date: date
    bars: dict[str, Bar] = field(default_factory=dict)
    bench_close: float | None = None
    data_snapshot_id: str = "local"

    def bar(self, symbol: str) -> Bar | None:
        return self.bars.get(symbol)


@runtime_checkable
class SignalEngine(Protocol):
    name: str

    def load_day(self, track: dict[str, Any], trade_date: date) -> DayContext: ...

    def score(self, track: dict[str, Any], ctx: DayContext) -> dict[str, float]: ...


class UnavailableEngine:
    """占位引擎：任何调用都抛 ``EngineUnavailable``，把"没接上"变成显式失败。"""

    name = "unavailable"

    def __init__(self, reason: str):
        self.reason = reason

    def load_day(self, track: dict[str, Any], trade_date: date) -> DayContext:
        raise EngineUnavailable(self.reason)

    def score(self, track: dict[str, Any], ctx: DayContext) -> dict[str, float]:
        raise EngineUnavailable(self.reason)


#: P1.6 的默认原因，直接展示给用户（人话，不是堆栈）
DEFAULT_UNAVAILABLE_REASON = (
    "日更行情管道还没接上（缺每日增量行情 + 交易日历/停牌表），"
    "所以今天这一跑没有真实数据可用。不会用假数据糊弄你。"
)


def get_signal_engine() -> SignalEngine:
    """引擎工厂。P2.0 接入增量行情后，这里换成 qlib 实现即可。"""
    return UnavailableEngine(DEFAULT_UNAVAILABLE_REASON)
