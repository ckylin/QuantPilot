"""TrackService —— 策略观察期的编排核心。

一句话职责：**把"一次性回测"变成"每天跑一次、按事前锁定的规则积累证据"的对象。**

单日流程（收盘后）：

    load_day → 执行昨日 pending 目标（今日开盘价）→ 按今日收盘记净值
             → 若是调仓日：打分 → 目标组合（成为新的 pending）
             → 评估：证据等级 / 失效线 / 检查点

三条不可妥协的纪律（对应设计文档 §4.2）：
1. **幂等**：同一天重跑覆盖同一天结果，``trading_days`` 不会二次累加；
2. **断档可见**：取不到行情 → 写 ``gap`` 事件并返回失败原因，**绝不静默跳过**；
3. **冻结**：``spec`` 与 ``rules`` 创建后只读，改参数 = 新建 Track（fork）。
"""

from __future__ import annotations

import statistics
import uuid
from datetime import date, datetime
from typing import Any

from app.core.config import settings
from app.schemas.strategy_spec import StrategySpec
from app.tracking.engine import (
    Bar,
    DayContext,
    EngineUnavailable,
    SignalEngine,
    get_signal_engine,
)
from app.tracking.repository import TrackRepository
from app.tracking.rules import (
    EVIDENCE_TEXT,
    build_targets,
    checkpoint_text,
    default_rules,
    estimated_verdict_date,
    evidence_level,
    fail_reason,
    is_rebalance_day,
    target_key,
)

# ---------------------------------------------------------------------------
# 状态机与异常
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"observing", "rejected", "archived"}),
    "rejected": frozenset({"archived"}),
    "shadow": frozenset({"observing", "archived", "abandoned"}),
    "observing": frozenset({"paused", "auto_paused", "concluded", "abandoned", "archived"}),
    "paused": frozenset({"observing", "abandoned", "archived"}),
    "auto_paused": frozenset({"observing", "paused", "abandoned", "archived"}),
    "concluded": frozenset({"observing", "archived", "abandoned"}),
    "abandoned": frozenset({"archived", "observing"}),
    "archived": frozenset({"observing"}),
}

RUNNABLE_STATES = frozenset({"observing"})


class TrackError(Exception):
    """业务错误基类；API 层按 ``status_code`` 映射。"""

    status_code = 400


class TrackNotFound(TrackError):
    status_code = 404


class TrackStateError(TrackError):
    status_code = 409


class TrackQuotaExceeded(TrackError):
    status_code = 409


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TrackService:
    def __init__(
        self,
        repo: TrackRepository,
        engine: SignalEngine | None = None,
        active_limit: int | None = None,
    ):
        self.repo = repo
        self.engine = engine or get_signal_engine()
        self.active_limit = settings.track_active_limit if active_limit is None else active_limit

    # ---- 创建 -------------------------------------------------------------

    def create_track(
        self,
        spec: StrategySpec | dict[str, Any],
        *,
        owner: str = "local",
        strategy_id: str | None = None,
        name: str | None = None,
        horizon_days: int | None = None,
        rules: dict[str, Any] | None = None,
        start_date: date | None = None,
        backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建一条观察。``spec`` 与 ``rules`` 从此冻结。

        ``backtest`` 给首次回测结果时先过一道门槛：年化 ≤ 0 或没交易直接 rejected，
        不让用户白等 20 天。
        """
        parsed = (
            spec if isinstance(spec, StrategySpec) else StrategySpec.model_validate(spec)
        )
        spec_dict = parsed.model_dump(mode="json")

        if self.repo.count_active(owner) >= self.active_limit:
            raise TrackQuotaExceeded(
                f"同时在观察的策略最多 {self.active_limit} 个。"
                "先归档一个再开新的——同时盯太多等于没盯。"
            )

        freq = parsed.rebalance.freq
        locked_rules = dict(default_rules(freq, horizon_days))
        locked_rules.update(rules or {})
        if horizon_days:
            locked_rules["horizon_days"] = horizon_days
        horizon = int(locked_rules["horizon_days"])
        locked_rules["horizon_days"] = horizon

        begin = start_date or date.today()
        track_id = f"trk-{begin.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        initial = float(parsed.account.initial_capital)

        gate = self._backtest_gate(backtest)
        state = "rejected" if gate else "observing"

        track = self.repo.create({
            "track_id": track_id,
            "strategy_id": strategy_id or f"STR-{track_id[-6:]}",
            "owner": owner,
            "name": name or parsed.meta.name,
            "spec": spec_dict,
            "rules": locked_rules,
            "state": state,
            "start_date": begin,
            "horizon_days": horizon,
            "rebalance_freq": freq,
            "initial_capital": initial,
            "cash": initial,
            "shares": {},
            "avg_cost": {},
            "pending_targets": None,
            "bench_base": None,
            "bench_last": None,
            "last_run_date": None,
            "trading_days": 0,
            "consecutive_gaps": 0,
            "flat_days": 0,
            "stale_days": 0,
            "last_target_key": "",
            "evidence_level": "L0",
            "verdict": None,
            "alerted": False,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "archived_at": None,
        })

        if gate:
            self._event(track_id, "rejected", f"这个想法先别上线：{gate}")
        else:
            self._event(track_id, "created", self._intro_text(parsed, horizon, begin, locked_rules))
        return self.get(track_id)

    def _backtest_gate(self, backtest: dict[str, Any] | None) -> str | None:
        if not backtest:
            return None
        metrics = backtest.get("metrics") or {}
        annual = metrics.get("annual_return")
        if isinstance(annual, (int, float)) and annual <= 0:
            return "回测年化为负，说明这套规则在历史上就不赚钱，先别浪费 20 天。"
        trades = backtest.get("trade_count")
        if isinstance(trades, int) and trades < 5:
            return "回测里几乎没成交，先确认选股条件是不是太严了。"
        return None

    def _intro_text(
        self, spec: StrategySpec, horizon: int, begin: date, rules: dict[str, Any]
    ) -> str:
        end = estimated_verdict_date(begin, horizon)
        freq = {"day": "每天", "week": "每周", "month": "每月"}[spec.rebalance.freq]
        return (
            f"已上线观察。{freq}调仓，每天收盘后跑一次，"
            f"满 {horizon} 个交易日（大概 {end:%Y-%m-%d}）给你第一个结论。"
            f"中间随时可以喊停。"
        )

    # ---- 查询 -------------------------------------------------------------

    def get(self, track_id: str) -> dict[str, Any]:
        track = self._require(track_id)
        navs = self.repo.navs(track_id)
        latest = navs[-1] if navs else None
        days = track["trading_days"]
        horizon = track["horizon_days"]
        end = estimated_verdict_date(track["start_date"], horizon)
        return {
            **track,
            "progress": {
                "trading_days": days,
                "horizon_days": horizon,
                "days_left": max(0, horizon - days),
                "percent": min(100, round(days / horizon * 100)) if horizon else 0,
                "estimated_verdict_date": end.isoformat(),
                "note": "按工作日估算，未扣法定节假日",
            },
            "latest": latest,
            "checkpoints": self.repo.checkpoints(track_id),
        }

    def list_tracks(
        self, owner: str | None = None, state: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        items = self.repo.list(owner=owner, state=state, limit=limit)
        result = []
        for item in items:
            navs = self.repo.navs(item["track_id"], limit=1)
            result.append({
                "track_id": item["track_id"],
                "name": item["name"],
                "state": item["state"],
                "start_date": item["start_date"],
                "trading_days": item["trading_days"],
                "horizon_days": item["horizon_days"],
                "evidence_level": item["evidence_level"],
                "evidence_text": EVIDENCE_TEXT.get(item["evidence_level"], ""),
                "verdict": item["verdict"],
                "latest": navs[-1] if navs else None,
                "last_run_date": item["last_run_date"],
            })
        return result

    def journal(self, track_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self._require(track_id)
        return self.repo.events(track_id, limit=limit)

    def navs(self, track_id: str) -> list[dict[str, Any]]:
        self._require(track_id)
        return [
            {
                "run_date": n["run_date"].isoformat()
                if isinstance(n["run_date"], date) else n["run_date"],
                "nav": round(n["nav"], 6),
                "bench_nav": round(n["bench_nav"], 6) if n["bench_nav"] is not None else None,
                "excess": round(n["excess"], 6) if n["excess"] is not None else None,
                "drawdown": round(n["drawdown"], 6),
                "positions_count": n["positions_count"],
            }
            for n in self.repo.navs(track_id)
        ]

    def positions(self, track_id: str, run_date: date | None = None) -> list[dict[str, Any]]:
        track = self._require(track_id)
        target = run_date or track["last_run_date"]
        if target is None:
            return []
        return self.repo.positions_of(track_id, target)

    # ---- 单日执行（"跑今天"） ---------------------------------------------

    def run_day(
        self,
        track_id: str,
        trade_date: date | None = None,
        engine: SignalEngine | None = None,
    ) -> dict[str, Any]:
        track = self._require(track_id)
        if track["state"] not in RUNNABLE_STATES:
            raise TrackStateError(
                f"当前状态 {track['state']} 不能跑。"
                + ("已暂停，先恢复观察。" if track["state"] == "paused" else "")
                + ("已有结论，继续观察请调 /resume 延长窗口。" if track["state"] == "concluded" else "")
            )

        run_date = trade_date or date.today()
        if run_date > date.today():
            raise TrackStateError("不能跑未来日期——那不是验证，是想象。")
        last = track["last_run_date"]
        if last and run_date < last:
            raise TrackStateError(
                f"补跑必须按日期升序：上次跑到 {last}，不能回退到 {run_date}。"
            )

        eng = engine or self.engine
        try:
            ctx = eng.load_day(track, run_date)
        except EngineUnavailable as exc:
            return self._record_gap(track, run_date, str(exc))

        # 1) 执行昨日信号（今日开盘成交）。
        #    pending_targets 为 None 表示"没有待执行"，绝不能被当成"清仓到 0"。
        held_shares = {s: int(q) for s, q in (track["shares"] or {}).items() if int(q) > 0}
        held_cost = {s: float(c) for s, c in (track["avg_cost"] or {}).items()}
        pending_in = track["pending_targets"]
        if pending_in is None:
            shares, avg_cost, cash = held_shares, held_cost, float(track["cash"])
            fills, fee, turnover = [], 0.0, 0.0
        else:
            shares, avg_cost, cash, fills, fee, turnover = self._execute(track, ctx, pending_in)

        # 2) 按今日收盘记净值
        nav_row, bench_base, bench_last = self._mark(
            track, ctx, shares, avg_cost, cash, fee, turnover
        )
        self.repo.save_nav(nav_row)
        self._save_positions(track, run_date, shares, avg_cost, ctx, nav_row["equity"])
        navs = self.repo.navs(track_id)
        days = len(navs)

        # 3) 调仓日出新信号（今天收盘出，明天开盘执行）
        rebalance = is_rebalance_day(track["rebalance_freq"], run_date, last)
        pending: list[dict[str, Any]] | None = None
        if rebalance:
            try:
                scores = eng.score(track, ctx)
            except EngineUnavailable as exc:
                self._event(track_id, "gap", f"{run_date} 打分失败：{exc}", {"run_date": run_date})
                scores = {}
            if scores:
                pending = build_targets(track["spec"], scores)
                self.repo.save_signal({
                    "track_id": track_id,
                    "run_date": run_date,
                    "targets": pending,
                    "scores": {
                        k: round(float(v), 6)
                        for k, v in scores.items() if v is not None
                    },
                    "data_snapshot_id": ctx.data_snapshot_id,
                })

        # 4) 评估：证据等级 / 失效线 / 检查点
        metrics = self._metrics(navs)
        rules = track["rules"]
        deviation_ok = self._deviation_ok(track, metrics)
        level = evidence_level(days, rules, deviation_ok=deviation_ok)

        flat_days = track["flat_days"] + 1 if nav_row["positions_count"] == 0 else 0
        stale_days = track["stale_days"]
        if pending:
            key = target_key(pending)
            stale_days = stale_days + 1 if key == track["last_target_key"] else 0
            last_key = key
        else:
            last_key = track["last_target_key"]

        patch: dict[str, Any] = {
            "cash": round(cash, 2),
            "shares": shares,
            "avg_cost": {k: round(v, 4) for k, v in avg_cost.items()},
            "pending_targets": pending,
            "bench_base": bench_base,
            "bench_last": bench_last,
            "last_run_date": run_date,
            "trading_days": days,
            "consecutive_gaps": 0,
            "flat_days": flat_days,
            "stale_days": stale_days,
            "last_target_key": last_key,
            "evidence_level": level,
        }

        verdict = self._fail_check(track, navs, flat_days, stale_days, rules)
        if verdict:
            patch["verdict"] = verdict
            patch["alerted"] = True
            self._event(track_id, "fail_line", verdict, {"days": days})

        if days >= track["horizon_days"] and track["state"] == "observing":
            patch["state"] = "concluded"
            patch.setdefault("verdict", self._conclusion_text(days, metrics, level))
            self._event(track_id, "concluded", patch["verdict"] or "", {"days": days})

        updated = self.repo.update(track_id, patch) or track
        self._write_checkpoints(track_id, updated, navs, metrics, level, run_date)
        self._daily_event(track_id, run_date, nav_row, fills, pending, days, level)

        return {
            "ok": True,
            "track_id": track_id,
            "run_date": run_date.isoformat(),
            "trading_days": days,
            "nav": round(nav_row["nav"], 6),
            "excess": round(nav_row["excess"], 6) if nav_row["excess"] is not None else None,
            "positions_count": nav_row["positions_count"],
            "rebuild": rebalance,
            "pending_targets": len(pending or []),
            "has_pending": pending is not None,
            "fills": fills,
            "evidence_level": level,
            "state": updated["state"],
        }

    # ---- 生命周期 ---------------------------------------------------------

    def pause(self, track_id: str, note: str = "") -> dict[str, Any]:
        return self._move(track_id, "paused", note or "已暂停，净值停在原地。恢复后从当天接着跑。")

    def resume(self, track_id: str, extend_days: int = 0) -> dict[str, Any]:
        track = self._require(track_id)
        patch: dict[str, Any] = {}
        if extend_days > 0:
            patch["horizon_days"] = track["horizon_days"] + extend_days
        moved = self._move(track_id, "observing", "恢复观察。")
        if patch:
            moved = self.repo.update(track_id, patch) or moved
        return self.get(moved["track_id"])

    def abandon(self, track_id: str, note: str = "") -> dict[str, Any]:
        return self._move(
            track_id, "abandoned",
            note or "你放弃了这个观察。数据保留 90 天，想捡回来随时说。",
        )

    def archive(self, track_id: str, note: str = "") -> dict[str, Any]:
        track = self._move(track_id, "archived", note or "已归档，停止日更。")
        self.repo.update(track_id, {"archived_at": datetime.now()})
        return self.get(track_id)

    def restore(self, track_id: str) -> dict[str, Any]:
        return self.get(self._move(track_id, "observing", "已复活，从今天接着跑。")["track_id"])

    def _move(self, track_id: str, target: str, message: str) -> dict[str, Any]:
        track = self._require(track_id)
        current = track["state"]
        if target == current:
            return track
        if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
            raise TrackStateError(f"状态 {current} 不能直接变到 {target}")
        updated = self.repo.update(track_id, {"state": target}) or track
        self._event(track_id, f"state:{target}", message, {"from": current, "to": target})
        return updated

    # ---- 记账 -------------------------------------------------------------

    def _execute(
        self, track: dict[str, Any], ctx: DayContext, targets: list[dict[str, Any]]
    ) -> tuple[dict[str, int], dict[str, float], float, list[dict[str, Any]], float, float]:
        """把待执行目标按**今日开盘价**撮合。返回账本更新与成交流水。

        ``targets=[]`` 是"明确清仓"；调用方不要用空列表表达"今天没信号"。
        """
        spec = track["spec"]
        rules = track["rules"]
        cost = spec.get("cost") or {}
        rate_buy = float(cost.get("open_cost", 0.001))
        rate_sell = float(cost.get("close_cost", 0.001))
        min_cost = float(cost.get("min_cost", 5.0))
        lot = int(rules.get("lot_size", 100))
        slip = float(rules.get("slippage_bps", 10.0)) / 10000.0
        min_change = float(rules.get("min_weight_change", 0.01))

        shares = {s: int(q) for s, q in (track["shares"] or {}).items() if int(q) > 0}
        avg_cost = {s: float(c) for s, c in (track["avg_cost"] or {}).items()}
        cash = float(track["cash"])

        equity_prev = cash + sum(
            qty * self._ref_price(ctx, sym) for sym, qty in shares.items()
        )
        desired = {t["symbol"]: float(t["weight"]) for t in targets}

        orders: list[tuple[str, int, Bar]] = []
        fills: list[dict[str, Any]] = []
        for symbol in sorted(set(shares) | set(desired)):
            bar = ctx.bar(symbol)
            if bar is None:
                if shares.get(symbol, 0) or desired.get(symbol, 0) > 0:
                    fills.append({"symbol": symbol, "action": "skip", "reason": "no_bar"})
                continue
            price = bar.open if bar.open > 0 else bar.close
            target_value = desired.get(symbol, 0.0) * equity_prev
            want = int(target_value // (price * lot) * lot) if price > 0 else 0
            delta = want - shares.get(symbol, 0)
            if delta == 0:
                continue
            if abs(delta) * price / max(equity_prev, 1.0) < min_change:
                continue
            orders.append((symbol, delta, bar))

        traded = 0.0
        fee_total = 0.0

        # 先卖：腾出现金，也避免"钱不够买"的假失败
        for symbol, delta, bar in sorted(orders, key=lambda o: o[1]):
            if delta >= 0:
                continue
            if bar.suspended:
                fills.append({"symbol": symbol, "action": "sell", "qty": 0,
                              "reason": "suspended"})
                continue
            if bar.limit_down and bar.open <= bar.limit_down * 1.0001:
                fills.append({"symbol": symbol, "action": "sell", "qty": 0,
                              "reason": "limit_down"})
                continue
            qty = min(-delta, shares.get(symbol, 0))
            if qty <= 0:
                continue
            price = bar.open * (1 - slip)
            notional = qty * price
            fee = max(notional * rate_sell, min_cost)
            cash += notional - fee
            shares[symbol] -= qty
            if shares[symbol] == 0:
                shares.pop(symbol, None)
                avg_cost.pop(symbol, None)
            traded += notional
            fee_total += fee
            fills.append({"symbol": symbol, "action": "sell", "qty": qty,
                          "price": round(price, 4), "fee": round(fee, 2)})

        # 再买：现金不够就按整手降档，绝不允许负现金
        for symbol, delta, bar in orders:
            if delta <= 0:
                continue
            if bar.suspended:
                fills.append({"symbol": symbol, "action": "buy", "qty": 0,
                              "reason": "suspended"})
                continue
            if bar.limit_up and bar.open >= bar.limit_up * 0.9999:
                fills.append({"symbol": symbol, "action": "buy", "qty": 0,
                              "reason": "limit_up"})
                continue
            price = bar.open * (1 + slip)
            affordable = int(cash // (price * (1 + rate_buy)))
            qty = min(delta, (affordable // lot) * lot)
            while qty > 0:
                notional = qty * price
                fee = max(notional * rate_buy, min_cost)
                if notional + fee <= cash:
                    break
                qty -= lot
            if qty <= 0:
                fills.append({"symbol": symbol, "action": "buy", "qty": 0,
                              "reason": "insufficient_cash"})
                continue
            notional = qty * price
            fee = max(notional * rate_buy, min_cost)
            cash -= notional + fee
            old_qty = shares.get(symbol, 0)
            old_cost = avg_cost.get(symbol, price)
            shares[symbol] = old_qty + qty
            avg_cost[symbol] = (old_cost * old_qty + notional) / (old_qty + qty)
            traded += notional
            fee_total += fee
            fills.append({"symbol": symbol, "action": "buy", "qty": qty,
                          "price": round(price, 4), "fee": round(fee, 2)})

        turnover = traded / equity_prev if equity_prev > 0 else 0.0
        return shares, avg_cost, cash, fills, fee_total, turnover

    def _mark(
        self,
        track: dict[str, Any],
        ctx: DayContext,
        shares: dict[str, int],
        avg_cost: dict[str, float],
        cash: float,
        fee: float,
        turnover: float,
    ) -> tuple[dict[str, Any], float | None, float | None]:
        """按收盘价记一个净值点。缺行情时用成本价兜底（并会被 gap 机制暴露）。"""
        market_value = sum(
            qty * self._mark_price(ctx, sym, avg_cost.get(sym)) for sym, qty in shares.items()
        )
        equity = cash + market_value
        nav = equity / float(track["initial_capital"])

        bench_base = track.get("bench_base")
        bench_nav = None
        bench_last = track.get("bench_last")
        if ctx.bench_close:
            if not bench_base:
                bench_base = float(ctx.bench_close)
            bench_nav = float(ctx.bench_close) / float(bench_base)
            bench_last = float(ctx.bench_close)

        prev = self.repo.navs(track["track_id"])
        peak = max([n["nav"] for n in prev] + [nav, 1.0])
        drawdown = nav / peak - 1

        row = {
            "track_id": track["track_id"],
            "run_date": ctx.trade_date,
            "nav": nav,
            "bench_nav": bench_nav,
            "excess": (nav - bench_nav) if bench_nav is not None else None,
            "drawdown": drawdown,
            "turnover": turnover,
            "fee": fee,
            "cash": cash,
            "equity": equity,
            "positions_count": len(shares),
            "data_snapshot_id": ctx.data_snapshot_id,
        }
        return row, bench_base, bench_last

    def _save_positions(
        self,
        track: dict[str, Any],
        run_date: date,
        shares: dict[str, int],
        avg_cost: dict[str, float],
        ctx: DayContext,
        equity: float,
    ) -> None:
        rows = []
        for symbol, qty in sorted(shares.items()):
            close = self._mark_price(ctx, symbol, avg_cost.get(symbol))
            value = qty * close
            rows.append({
                "symbol": symbol,
                "qty": qty,
                "avg_cost": round(avg_cost.get(symbol, close), 4),
                "close": round(close, 4),
                "market_value": round(value, 2),
                "weight": round(value / equity, 6) if equity else 0.0,
            })
        self.repo.replace_positions(track["track_id"], run_date, rows)

    @staticmethod
    def _ref_price(ctx: DayContext, symbol: str) -> float:
        bar = ctx.bar(symbol)
        if bar is None:
            return 0.0
        return bar.prev_close or bar.close

    @staticmethod
    def _mark_price(ctx: DayContext, symbol: str, fallback: float | None = None) -> float:
        """估值价：优先收盘价，缺行情退回成本价，避免凭空给出 0 造成净值暴跌。"""
        bar = ctx.bar(symbol)
        if bar is not None:
            return bar.close or bar.prev_close
        return float(fallback or 0.0)

    # ---- 评估 -------------------------------------------------------------

    def _metrics(self, navs: list[dict[str, Any]]) -> dict[str, Any]:
        if not navs:
            return {}
        last = navs[-1]
        cum = last["nav"] - 1
        bench_cum = (last["bench_nav"] - 1) if last["bench_nav"] is not None else None
        peak, drawdown = 1.0, 0.0
        for item in navs:
            peak = max(peak, item["nav"])
            drawdown = min(drawdown, item["nav"] / peak - 1)

        daily_excess = []
        for prev, curr in zip(navs, navs[1:]):
            port = curr["nav"] / prev["nav"] - 1 if prev["nav"] else 0.0
            if curr["bench_nav"] is not None and prev["bench_nav"]:
                bench = curr["bench_nav"] / prev["bench_nav"] - 1
                daily_excess.append(port - bench)

        return {
            "days": len(navs),
            "cum_return": cum,
            "bench_return": bench_cum,
            "excess": last["excess"],
            "max_drawdown": drawdown,
            "win_rate": (
                sum(1 for x in daily_excess if x > 0) / len(daily_excess)
                if daily_excess else None
            ),
            "avg_turnover": statistics.fmean(n["turnover"] for n in navs),
            "avg_positions": statistics.fmean(n["positions_count"] for n in navs),
            "total_fee": sum(n["fee"] for n in navs),
        }

    def _window_excess(self, navs: list[dict[str, Any]], window: int = 20) -> float | None:
        if len(navs) < window + 1:
            return None
        head, tail = navs[-window - 1], navs[-1]
        if head["nav"] <= 0:
            return None
        port = tail["nav"] / head["nav"] - 1
        if head["bench_nav"] in (None, 0) or tail["bench_nav"] is None:
            return None
        return port - (tail["bench_nav"] / head["bench_nav"] - 1)

    def _deviation_ok(self, track: dict[str, Any], metrics: dict[str, Any]) -> bool:
        """与回测预期的偏差是否在容忍范围内。没有回测预期时不假装比过。"""
        expected = (track.get("spec") or {}).get("meta", {}).get("expected_excess")
        if not isinstance(expected, (int, float)) or metrics.get("excess") is None:
            return True
        tolerance = float(track["rules"].get("deviation_tolerance", 0.05))
        return abs(metrics["excess"] - float(expected)) <= tolerance

    def _fail_check(
        self,
        track: dict[str, Any],
        navs: list[dict[str, Any]],
        flat_days: int,
        stale_days: int,
        rules: dict[str, Any],
    ) -> str | None:
        """只在**事前锁定的线**上报警，不做事后调参。"""
        if track["alerted"]:
            return None
        if flat_days >= int(rules.get("fail_flat_days", 10)):
            return fail_reason("flat", flat_days, rules)
        if stale_days >= int(rules.get("fail_stale_days", 5)):
            return fail_reason("stale", stale_days, rules)
        window = self._window_excess(navs, 20)
        if window is not None and window <= float(rules.get("fail_excess", -0.08)):
            return fail_reason("excess", window, rules)
        return None

    def _conclusion_text(self, days: int, metrics: dict[str, Any], level: str) -> str:
        excess = metrics.get("excess")
        head = f"满 {days} 个交易日，第一份结论："
        if metrics.get("avg_positions", 0) < 1 and days >= 5:
            return head + "一直没建仓，问题多半在数据或选股条件，不是策略本身。"
        if excess is None:
            return head + "有真实交易记录，但没有基准可比，暂时只能说它按设计在跑。"
        if excess >= 0:
            return head + f"方向对得上，累计超额 {excess * 100:+.2f}%。证据等级 {level}。"
        return head + (f"跑输基准 {abs(excess) * 100:.2f}%，"
                       f"但样本还小，先别急着下判断。证据等级 {level}。")

    def _write_checkpoints(
        self,
        track_id: str,
        track: dict[str, Any],
        navs: list[dict[str, Any]],
        metrics: dict[str, Any],
        level: str,
        run_date: date,
    ) -> None:
        days = len(navs)
        for point in track["rules"].get("checkpoints", []):
            point = int(point)
            if days < point or self.repo.get_checkpoint(track_id, point):
                continue
            text = checkpoint_text(point, metrics, track["horizon_days"])
            self.repo.save_checkpoint({
                "track_id": track_id,
                "day_n": point,
                "run_date": run_date,
                "metrics": {k: (round(v, 6) if isinstance(v, float) else v)
                            for k, v in metrics.items()},
                "evidence_level": level,
                "verdict": text,
                "created_at": datetime.now(),
            })
            self._event(track_id, f"checkpoint:{point}", text, {"day_n": point})

    def _daily_event(
        self,
        track_id: str,
        run_date: date,
        nav_row: dict[str, Any],
        fills: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        days: int,
        level: str,
    ) -> None:
        bought = [f for f in fills if f["action"] == "buy" and f["qty"] > 0]
        blocked = [f for f in fills if f.get("reason")]
        nav = nav_row["nav"]
        excess = nav_row["excess"]
        parts = [f"{run_date} 第 {days} 天：净值 {nav:.4f}"]
        if excess is not None:
            parts.append(f"超额 {excess * 100:+.2f}%")
        if bought:
            parts.append(f"买入 {len(bought)} 只")
        if blocked:
            reasons = {f["reason"] for f in blocked}
            parts.append("有单没成交（" + "、".join(sorted(reasons)) + "）")
        if pending:
            parts.append(f"新目标 {len(pending)} 只，明天开盘执行")
        self._event(track_id, "day", "；".join(parts) + f"。证据 {level}。", {
            "run_date": run_date.isoformat(),
            "nav": round(nav, 6),
            "fills": len(fills),
            "blocked": len(blocked),
        })

    def _record_gap(self, track: dict[str, Any], run_date: date, reason: str) -> dict[str, Any]:
        """没跑成也要留痕。连续多天取不到行情 → 自动暂停，不装作在跑。"""
        track_id = track["track_id"]
        gaps = int(track["consecutive_gaps"]) + 1
        patch: dict[str, Any] = {"consecutive_gaps": gaps}
        paused = gaps >= int(track["rules"].get("max_gap_days", 3))
        if paused and track["state"] == "observing":
            patch["state"] = "auto_paused"
        self.repo.update(track_id, patch)
        message = f"{run_date} 没能跑成：{reason}"
        if paused:
            message += f"（连续 {gaps} 天，已自动暂停，避免攒出一段假净值）"
        self._event(track_id, "gap", message, {
            "run_date": run_date.isoformat(), "consecutive_gaps": gaps,
        })
        return {
            "ok": False,
            "track_id": track_id,
            "run_date": run_date.isoformat(),
            "reason": reason,
            "consecutive_gaps": gaps,
            "state": patch.get("state", track["state"]),
        }

    # ---- 工具 -------------------------------------------------------------

    def _require(self, track_id: str) -> dict[str, Any]:
        track = self.repo.get(track_id)
        if not track:
            raise TrackNotFound(f"观察不存在: {track_id}")
        return track

    def _event(self, track_id: str, kind: str, message: str,
               payload: dict[str, Any] | None = None) -> None:
        self.repo.add_event({
            "track_id": track_id,
            "kind": kind,
            "message": message,
            "payload": payload or {},
            "created_at": datetime.now(),
        })
