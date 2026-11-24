# 契约速查：StrategySpec · Result · API

> 版本：v1.0　状态：**唯一契约文档**　更新：2026-11-24
>
> 本文是写代码时的查表手册。**字段的唯一真相是代码**：
> `app/schemas/strategy_spec.py`（Pydantic，`extra="forbid"`）、`app/api/app.py`（路由）、
> `prompts/operators.yaml`（表达式白名单）。文档与代码冲突时以代码为准，并回来改文档。
>
> 用户怎么用（不涉及字段）见 `USER-GUIDE.md`，怎么部署见 `DEPLOYMENT.md`。

---

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 协议 | HTTP/1.1，JSON，UTF-8 |
| 认证 | Phase 1 匿名 + 设备 ID；后续 Bearer Token |
| 错误结构 | `{"code": "...", "message": "...", "detail": {...}}` |
| 分页 | `?page=1&page_size=20` |
| 限流 | 匿名每设备每日 10 次回测（**契约已定，代码未实现**） |
| 异步 | `POST /strategies` 返回 202 + `task_id`，客户端轮询 `GET /tasks/{id}` |

---

## 2. StrategySpec

### 2.1 顶层结构

```jsonc
{
  "spec_version": "0.1",      // 必填，固定值
  "meta":       { "name": "...", "intent": "...", "tags": [] },   // 必填
  "universe":   { "type": "builtin", "name": "csi300" },           // 必填
  "date_range": { "start": "...", "end": "...", "in_sample": {}, "out_sample": {} },
  "rebalance":  { "freq": "month" },                               // 必填
  "signal":     { "type": "expression", "expression": "...", "direction": "desc" },
  "portfolio":  { "selection": "topk", "topk": 20, "weighting": "equal" },
  "filters":    [],            // 可选，标的过滤
  "risk":       {},            // 可选，风控
  "cost":       {},            // 可选，交易成本
  "benchmark":  "csi300",      // 可选
  "account":    {}             // 可选，初始资金
}
```

### 2.2 字段与枚举

| 字段 | 类型 / 枚举 | 约束 |
|---|---|---|
| `universe.type` | `builtin` \| `custom` | `builtin` ⇒ `name` 必填；`custom` ⇒ `custom_symbols` 非空 |
| `universe.name` | `csi300` \| `csi500` \| `csi1000` \| `all` | — |
| `universe.custom_symbols` | `["SH600000", ...]` | 每项须匹配 `^(SH\|SZ)\d{6}$` |
| `date_range.start/end` | `YYYY-MM-DD` | `start ≤ end` |
| `date_range.in_sample/out_sample` | `{start, end}` | 落在区间内、互不重叠 |
| `rebalance.freq` | `day` \| `week` \| `month` | — |
| `signal.type` | `expression` \| `model` | `model` ⇒ `model` 必填 |
| `signal.expression` | qlib 表达式字符串 | 标识符须在白名单（§2.4） |
| `signal.direction` | `desc` \| `asc` | 模型恒为 `desc`（预测未来收益） |
| `model.type` | `lgbm` \| `linear` \| `mlp` \| `xgboost` \| `catboost` \| `double_ensemble` 等 | — |
| `model.features` | `alpha158` \| `custom` | `custom` ⇒ `signals[]` 必有 |
| `model.segments.train/test` | `{start, end}` | 严格递增、不重叠、在 `date_range` 内 |
| `portfolio.selection` | `topk` | 当前只支持这一种 |
| `portfolio.topk` | int | `1 ≤ topk ≤ 500`，且 ≤ 池子容量（csi300=300） |
| `portfolio.n_drop` | int | `0 ≤ n_drop ≤ topk` |
| `portfolio.weighting` | `equal` \| `score` | — |
| `portfolio.max_single_weight` | float | 与 `risk.max_single_weight` 同时存在时取较小值 |
| `filters[].op` | `gt` \| `gte` \| `lt` \| `lte` \| `eq` \| `ne` | `field` 在白名单内 |
| `cost.open_cost` / `close_cost` | float | `[0, 1)`，默认 0.001 |
| `cost.min_cost` | float | `≥ 0`，默认 5.0 |
| `benchmark` | `csi300` \| `csi500` \| `csi1000` \| `none` | 默认 `csi300` |
| `account.initial_capital` | float | `> 0`，默认 1e8 |

### 2.3 跨字段校验（11 条）

失败统一返回 `SPEC_VALIDATION_ERROR`。

1. `spec_version` 必须为受支持版本
2. `universe.type=builtin ⇒ name` 必填；`custom ⇒ custom_symbols` 非空
3. `custom_symbols` 每项匹配 `^(SH|SZ)\d{6}$`
4. `start ≤ end`；`in_sample`/`out_sample` 在区间内且不重叠
5. `1 ≤ topk ≤ 500`；`0 ≤ n_drop ≤ topk`；`topk ≤ 池子容量`
6. `direction=asc` 且标签含 `momentum` → 警告（不阻断）
7. `portfolio.max_single_weight` 与 `risk.max_single_weight` 取较小值并归一
8. 表达式与 `filters[].field` 的标识符须在白名单；`features=custom` 时 `signals[]` 同校验
9. `open_cost`/`close_cost` ∈ [0,1)，`min_cost ≥ 0`
10. `signal.type=model ⇒ model` 必填；`features=custom ⇒ signals` 必填；`segments` 严格递增不重叠
11. `model.segments` 各区间须落在 `date_range` 内；模型 `direction` 恒 `desc`

### 2.4 qlib 表达式白名单

单一数据源：`prompts/operators.yaml`（校验与 prompt 共用）。

**数据字段**：`$open` `$close` `$high` `$low` `$volume` `$vwap` `$amount` `$factor`

| 算子 | 含义 | 示例 |
|---|---|---|
| `Ref(x, n)` | n 期前的值 | `Ref($close, 20)` |
| `Mean(x, n)` / `Sum(x, n)` / `Std(x, n)` | 滚动统计 | `Mean($volume, 5)` |
| `Max(x, n)` / `Min(x, n)` | 滚动极值 | `Max($high, 20)` |
| `Delta(x, n)` | 与 n 期前的差 | `Delta($close, 5)` |
| `Rank(x)` / `TsRank(x, n)` | 横截面 / 时序排名 | `Rank($volume)` |
| `Correlation(x, y, n)` | 时序相关 | `Correlation($close, $volume, 20)` |
| `Abs` `Log` `Sign` `Power` | 数学变换 | `Log($volume)` |
| `+ - * /` | 算术 | `$close / Ref($close, 20) - 1` |

> 白名单外一律拒绝（尤其含 `import`、`__`、`eval`、`open(`），不进入代码生成。

---

## 3. Result（沙箱标准输出）

沙箱 stdout 打印 `QUANTPLOT_RESULT:{json}`，执行器解析并**强制覆盖** `task_id` 与 `logs_ref`（防伪造）。

### 3.1 顶层

```jsonc
{
  "task_id": "...",            // 执行器注入
  "status": "done",            // done | error
  "strategy_type": "expression", // expression | model
  "spec": { ... },             // 完整 Spec，由 TaskService 落库时补全
  "metrics": { ... },
  "series": { ... },
  "contributions": [ ... ],
  "holdings": [ ... ],
  "split": { ... },
  "logs_ref": "minio://logs/task-xxx"
}
```

### 3.2 字段

| 块 | 字段 |
|---|---|
| `metrics` | `annual_return` / `sharpe` / `max_drawdown`(负) / `win_rate` / `turnover` / `calmar` / `in_sample` / `out_sample` |
| `series` | `nav` / `benchmark_nav` / `drawdown`，各为 `[{date, value}]`，**降采样 ≤ 500 点** |
| `contributions` | `[{name, ret, amount}]`，按 `ret` 降序 |
| `holdings` | `[{name, weight, ret}]`，前 min(topk, 6)，按 `weight` 降序 |
| `split` | `{in_sample: {...}, out_sample: {...}}` |

**真实区间参考**（H5 mock 按此校准）：年化 11–15%、夏普 1.5–2、回撤 5–9%。

前端字段映射：`r.metrics.*`→`metrics`、`r.series.nav`→`series.nav`、`r.kind`→`strategy_type`。

---

## 4. API

### 4.1 端点总表

**策略与任务**

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/intents/parse` | 一句话/选项 → 复述 + 问题 + 中文参数卡（不含表达式） |
| POST | `/api/v1/strategies` | 提交 `intent` 或 `frame` → 202 + task_id |
| GET | `/api/v1/strategies` | 历史策略列表 |
| POST | `/api/v1/strategies/{id}/favorite` | 收藏/取消 |
| GET | `/api/v1/tasks/{task_id}` | 任务状态 + 分阶段进度 |
| GET | `/api/v1/tasks/{task_id}/result` | 结果（未完成 409） |

**观察期 Track**

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/tracks` | 开一个观察（spec/rules 冻结） |
| GET | `/api/v1/tracks` | 观察列表（首页卡片流） |
| GET | `/api/v1/tracks/{id}` | 详情（进度/证据等级/检查点） |
| GET | `/api/v1/tracks/{id}/journal` | 时间轴日志 |
| GET | `/api/v1/tracks/{id}/nav` | 净值序列 vs 基准 |
| GET | `/api/v1/tracks/{id}/positions` | 最近一日纸面持仓 |
| POST | `/api/v1/tracks/{id}/run` | 手动"跑今天"（P1.6 无调度时的入口） |
| POST | `/api/v1/tracks/{id}/pause` \| `resume` \| `abandon` \| `archive` \| `restore` | 生命周期操作 |

**资产目录**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/factors` / `datasets` / `models` | 目录列表，支持 `?tag=&sort=&order=` |

**数据导入审核**

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/v1/data/imports` | multipart 上传 CSV，建批次 |
| GET | `/api/v1/data/imports` / `/{batch_id}` | 列表 / 详情 |
| GET | `/api/v1/data/imports/{batch_id}/preview` | 标准化预览（前 20 行） |
| POST | `/api/v1/data/imports/{batch_id}/submit` \| `review` \| `publish` | 提交 / 审核 / 发布 |

**系统**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |

### 4.2 关键请求形状

**`POST /intents/parse`**

```jsonc
// 请求（二选一）
{ "intent": "又便宜又稳的，中证500，每月换" }
{ "frame": { "universe": "csi500", "styles": ["price_low"], "confidence": 1.0 } }

// 响应 200
{
  "restated": "在中证500里，挑价格处于相对低位、走势平稳的 20 只，每月换一次",
  "ready": true,
  "questions": [{ "key": "universe", "title": "...", "blocking": false, "options": [] }],
  "unsupported": [],
  "card": { "标的": "中证500", "挑什么": "...", "买多少": "20 只", "多久换": "每月" },
  "assumptions": ["你没说买多少只，我按 20 只"],
  "caveats": ["这里用的是「价格位置」近似，不是市盈率"]
}
```

- `ready=false` → `questions` 里有必答项
- `unsupported` 非空 → 用户诉求当前数据做不到，`restated` 必须直说"看不了"
- `assumptions` 是后台替用户做的决定，**前端必须展示**

**`POST /tracks`**

```jsonc
{
  "spec": { "spec_version": "0.1", "...": "完整 Spec" },   // 与 strategy_id 二选一
  "owner": "local",
  "horizon_days": 20,          // 缺省按调仓周期：日 20 / 周 30 / 月 90
  "rules": { "fail_excess": -0.08 },   // 创建后只读
  "start_date": "2026-11-24",
  "backtest": { "metrics": { "annual_return": 0.12 } }  // 给了就先过门槛
}
```

- 门槛未过（年化 ≤ 0 或成交 < 5 笔）→ `state=rejected`，不占额度
- 并发观察数 > `QP_TRACK_ACTIVE_LIMIT`（默认 3）→ 409

**`POST /tracks/{id}/run`**（body 可省，默认今天）

```jsonc
// 成功
{ "ok": true, "run_date": "2026-11-24", "trading_days": 3, "nav": 1.0123,
  "excess": 0.004, "positions_count": 5, "rebuild": false, "pending_targets": 0,
  "fills": [{ "symbol": "SH600000", "action": "buy", "qty": 500, "price": 10.02, "fee": 5.01 }],
  "evidence_level": "L1", "state": "observing" }

// 行情不可用（HTTP 仍 200，ok=false，已写 gap 事件）
{ "ok": false, "reason": "日更行情管道还没接上…", "consecutive_gaps": 1 }
```

| 约定 | 说明 |
|---|---|
| 幂等 | 同 `trade_date` 重跑覆盖，`trading_days` 不二次累加 |
| 升序补跑 | `trade_date < last_run_date` → 409 |
| 不跑未来 | `trade_date > today` → 409 |
| 断档留痕 | 取不到行情写 `gap` 事件；连续 3 天 → `auto_paused` |
| 不静默 | 失败必返 `ok=false` + `reason`，绝不回退假数据 |

### 4.3 错误码

| 码 | 场景 |
|---|---|
| 400 | 参数非法 / 质量门禁失败 |
| 404 | 资源不存在 |
| 409 | 状态不允许 / 额度超限 / 结果未就绪 |
| 422 | Spec 校验失败 |
| 429 | 超额（限流，代码待实现） |
| 503 | 队列满 |

---

## 5. 状态机

### 5.1 回测任务

```
PENDING → QUEUED → RUNNING ─┬→ SUCCEEDED → ARCHIVED
                            ├→ REPAIRING → RUNNING / FAILED
                            └→ FAILED → ARCHIVED
```

`stage` 与 H5 进度页对应：`intent` / `spec` / `code` / `backtest` / `result`。

### 5.2 观察期 Track

```
draft → observing ──暂停──> paused ──恢复──> observing
            │  └─连续断档─> auto_paused ──恢复──> observing
            ├─到窗口────> concluded ──resume(extend_days)──> observing
            ├─用户放弃──> abandoned ──90天内──> archived / observing
            └─用户归档──> archived ──restore──> observing
rejected（回测门槛未过，仅可归档）
```

### 5.3 CSV 导入批次

```
uploaded ─submit─> pending_review ─approve─> approved ─publish─> published
    ▲                    │
    └───resubmit─────────┴─reject─> rejected
```

`review` 请求体：`{"decision": "approve"|"reject", "reviewer": "...", "note": "..."}`。
只有 `approved` 可 `publish`，成功返回 `published_asset_id`。

---

## 6. 改动规则

1. **先改代码再改本文**：Pydantic 加字段 → 立刻更新 §2.2 表
2. **加端点必须登记**到 §4.1，并在 `app/api/app.py` 里有对应测试
3. **加算子必须同时改** `prompts/operators.yaml` 与 §2.4
4. 破坏性变更（删字段、改语义）要升 `spec_version`，不能静默改
