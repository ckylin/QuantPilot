# 部署手册

> 版本：v1.1　读者：**把系统装起来跑的人**（开发 / 运维）
> 更新：2026-11-24
>
> 管四件事：**装起来、跑起来、跑真回测、出问题怎么办**。
> 与另三份的分工：`USER-GUIDE.md` 讲用户怎么用、`design-baseline.md` 讲为什么这么设计、
> `contracts.md` 讲字段与接口长什么样。本文只讲操作。
>
> **不需要代码能力的人只用看 `USER-GUIDE.md`**——本文涉及镜像、容器、环境变量。

---

## 0. 三条命令跑起来（最快路径）

```bash
cd quantpilot-server
python -m pip install -r requirements.txt
cp .env.example .env            # 不填 key 也能跑（离线演示模式）
uvicorn app.api.app:app --host 0.0.0.0 --port 8000
```

验证：

```bash
curl http://localhost:8000/api/v1/health    # {"status":"ok"}
```

此时是**离线演示模式**：无 LLM key → OfflineProvider 生成 Spec，`demo` runner 产出模拟结果，
端到端可跑但不真实。要真实结果，见 §4（沙箱）与 §2.2。

---

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| OS | Linux（Ubuntu 22.04+ / Kylin）或 Windows + WSL2 | podman rootless，无 daemon |
| Python | 3.10+ | 服务端；沙箱镜像内是 3.10 + pyqlib 0.9.7 |
| podman | 4.0+ | 仅真实回测需要 |
| 磁盘 | ≥ 15 GB | 镜像 1.5 GB + 行情数据 5–10 GB |
| 内存 | ≥ 4 GB | 容器默认限 2 GB |
| 网络 | 构建与备数据阶段需要；**执行阶段容器无网络** | — |

### Linux 安装 podman

```bash
sudo apt-get update && sudo apt-get install -y podman
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$USER"
podman system migrate
```

### Windows（WSL 后端）

```powershell
winget install RedHat.Podman
# podman.exe 通常在 C:\Users\<用户>\AppData\Local\Programs\Podman\podman.exe
podman machine init      # 首次：下载 WSL 镜像，耗时长
podman machine start
podman machine list      # 确认 LAST UP 非空
```

**Windows 四条硬约束**：

1. **machine 不会开机自启**——电脑重启后容器全部不可用，表现为所有命令退出码 **125**。先 `podman machine start`
2. 仓库必须位于 `C:\Users\...` 下（WSL machine 默认只挂载用户主目录）
3. 脚本在 Git Bash 中运行；`run_sandbox.sh` 已内置 `/c/...` → `C:\...` 路径转换与 podman 探测
4. PATH 未刷新时设 `QP_PODMAN_BIN` 指向 `podman.exe` 绝对路径

---

## 2. 配置

所有变量以 `QP_` 开头，由 `app/core/config.py` 从 `.env` 读取。**改完必须重启 uvicorn**。

### 2.1 全量变量

| 变量 | 默认 | 说明 |
|---|---|---|
| **LLM** | | |
| `QP_LLM_PROVIDER` | `deepseek` | `deepseek` \| `openai` \| `qwen` \| `custom` |
| `QP_LLM_MODEL` | `deepseek-flash` | 可换 `deepseek-chat` / `deepseek-reasoner` |
| `QP_LLM_API_KEY` | 空 | **留空则离线演示** |
| `QP_LLM_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容地址 |
| `QP_REQUEST_TIMEOUT` | `120` | 秒 |
| **生成控制** | | |
| `QP_SPEC_MAX_RETRIES` | `1` | Stage A 校验失败后的额外尝试 |
| `QP_CODE_MAX_RETRIES` | `1` | Stage B AST 拒绝后的额外尝试 |
| `QP_REPAIR_MAX_ROUNDS` | `2` | Stage C 执行失败后的修复轮数 |
| **服务端** | | |
| `QP_HOST` / `QP_PORT` | `0.0.0.0` / `8000` | — |
| `QP_CORS_ORIGINS` | `*` | Comma 分隔；H5 本地预览 |
| **沙箱** | | |
| `QP_SANDBOX_RUNNER` | `podman` | `podman` \| `local` \| `demo` |
| `QP_SANDBOX_IMAGE` | `localhost/quantpilot/sandbox:0.1` | **必须带 `localhost/` 前缀** |
| `QP_QLIB_PROVIDER_URI` | `/data/qlib` | 容器内挂载点；Windows 填宿主机原生路径 |
| `QP_PODMAN_BIN` | 空 | podman.exe 绝对路径 |
| **存储** | | |
| `QP_DATABASE_URL` | 空 | 空 = 内存（重启即丢）；见 §5 |
| `QP_DB_ECHO` | `false` | 打印 SQL |
| `QP_IMPORT_MAX_BYTES` | `10485760` | CSV 上传上限（10 MB） |
| **观察期** | | |
| `QP_TRACK_ACTIVE_LIMIT` | `3` | 同时观察上限（注意力约束，不是算力） |

### 2.2 三种典型组合

| 场景 | 配置 |
|---|---|
| **离线演示**（看 UI、调前端） | `QP_LLM_API_KEY=` 留空 + `QP_SANDBOX_RUNNER=demo` |
| **真实回测**（生产链路） | 填 key + `QP_SANDBOX_RUNNER=podman` + 镜像与数据就绪 |
| **只调后端逻辑** | 填 key + `QP_SANDBOX_RUNNER=local`（**无隔离，禁止跑不可信代码**） |

### 2.3 切换 LLM 供应商

全部走 OpenAI 兼容 `/chat/completions`，改 `QP_LLM_PROVIDER` + `QP_LLM_BASE_URL` 即可：

| provider | base_url | 默认模型 |
|---|---|---|
| deepseek | `https://api.deepseek.com/v1` | deepseek-flash |
| openai | `https://api.openai.com/v1` | gpt-4o-mini |
| qwen | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen-turbo |
| custom | 由 `QP_LLM_BASE_URL` 指定 | 由 `QP_LLM_MODEL` 指定 |

代码里新增供应商只需在 `app/services/llm/provider_factory.py` 的 `PROVIDER_PRESETS` 注册一条，
Stage A/B 逻辑不用动。

> **关于 deepseek-flash**：它是推理模型，思考占 token，偶发空 `content`——Stage A 的
> `attempts=2` 重试能兜住。要稳定可换 `deepseek-chat`。

---

## 3. 启动与对接

### 3.1 长跑用后台方式

§0 的前台命令适合调试（日志直接刷屏）。要一直开着，用后台并把日志落文件：

```bash
uvicorn app.api.app:app --host 0.0.0.0 --port 8000 --log-level info > uvicorn.out.log 2>&1 &
tail -f uvicorn.out.log          # 跟日志
```

**没有热加载**：改了 `.env` 必须重启进程，改 Python 代码同理（开发时加 `--reload`）。

### 3.2 H5 对接

`quantpilot-h5/index.html` 顶部：

```html
<script>window.QP_CONFIG = { apiBase: 'http://localhost:8000', deviceId: 'h5-demo' };</script>
```

| apiBase | 行为 |
|---|---|
| 空 | 纯前端 mock（双击 index.html 即用） |
| 填地址 | 提交/轮询/结果/因子目录走真实 API |

网络失败**不白屏**，自动回退 mock，但结果页顶部必挂横幅说明原因。

> 改了 JS/CSS 一定要改版本号：`app.js?v=2`、`style.css?v=4`。否则浏览器缓存会让你以为"改了不生效"。

---

## 4. 沙箱（真实回测的前提）

### 4.1 构建镜像

```bash
podman build -f sandbox/Containerfile -t quantpilot/sandbox:0.1 sandbox/
podman images | grep quantpilot/sandbox
```

镜像内含 Python 3.10 + **pyqlib 0.9.7**（锁版本，与代码模板 API 对齐）。
注意：PyPI 上 `qlib` 是另一个包，**必须装 `pyqlib`**。

### 4.2 准备行情数据

qlib 官方 Azure 包已下线（HTTP 409），改用社区 `chenditc/investment_data` 每日发布包。

```bash
bash sandbox/prepare_data.sh                       # 默认落 sandbox/data/cn_data
bash sandbox/prepare_data.sh /data/qlib            # 指定目录
QP_DATA_ARCHIVE=/path/qlib_bin.tar.gz bash sandbox/prepare_data.sh   # 复用已下载包
QP_DATA_URL=https://自有源/qlib_bin.tar.gz bash sandbox/prepare_data.sh
```

脚本自动校验 `calendars/` `instruments/` `features/` 是否齐全。
下载源回退链：`QP_DATA_URL` → 官方 GitHub → `ghfast.top` 镜像。

体量参考：**6177 只股票 × 10 字段 ≈ 6.2 万个 `.bin` 文件**，约 546 MB 压缩包。

### 4.3 冒烟验证

```bash
cat > /tmp/smoke.py <<'PY'
import qlib, json
from qlib.constant import REG_CN
qlib.init(provider_uri="/data/qlib", region=REG_CN)
from qlib.data import D
cal = D.calendar()
print("QUANTPLOT_SMOKE:" + json.dumps({"calendar_len": len(cal), "last": str(cal[-1])}))
PY

bash sandbox/run_sandbox.sh /tmp/smoke.py smoke 60
```

看到 `QUANTPLOT_SMOKE:{"calendar_len": ...}` 即说明镜像 + 数据 + 安全参数全部就绪。

### 4.4 一键跑真实策略（Windows）

```powershell
cd quantpilot-server
powershell -ExecutionPolicy Bypass -File sandbox\run_real_strategy.ps1
```

自动完成：确保 machine 运行 → 确保镜像存在（daocloud / 1ms 双源兜底）→ 挂载数据与生成代码 →
执行 → 输出 `QUANTPLOT_RESULT:{...}`。看到该前缀即表示真实链路跑通。
Linux 直接用 `bash sandbox/run_sandbox.sh <solution.py> <task_id> <timeout>`。

### 4.5 安全参数（每项都对应一个攻击面）

| 参数 | 防护目标 |
|---|---|
| `--network=none` | 生成代码无法外联（防外泄、防下载载荷） |
| `--read-only` + `--tmpfs /tmp:noexec,nosuid` | 根文件系统只读，/tmp 可写但不可执行 |
| `-v data:/data/qlib:ro` | 行情只读，代码无法篡改 |
| `-v workdir:/work:ro` | 生成代码只读挂载 |
| `--cap-drop=ALL` + `--security-opt no-new-privileges` | 丢弃全部 capabilities，禁提权 |
| `--userns=keep-id` | 以当前用户身份跑，不获得 root |
| `--memory` / `--cpus` / `--pids-limit` / `--ulimit` | 防内存耗尽与 fork 炸弹 |
| `entrypoint.sh` 内 `timeout` | 超时强杀（TERM → 5s → KILL） |
| `--rm` | 退出即销毁，不残留状态 |

**外加一层静态防护**：代码进沙箱前必须过 `app/services/llm/ast_guard.py`
（import/调用白名单 + 固定 runner 区逐字节比对）。

### 4.6 资源限额

| 变量 | 默认 |
|---|---|
| `QP_MEM_LIMIT` | `2g` |
| `QP_CPU_LIMIT` | `2` |
| `QP_PIDS_LIMIT` | `128` |
| `QP_TIMEOUT_SEC` | `120`（脚本参数） |

---

## 5. 本地 PostgreSQL（podman）

数据工坊资产、导入审核、观察期账本都要落库。`QP_DATABASE_URL` 留空时走内存，重启即丢。

```bash
cd quantpilot-server
./scripts/pg.sh up            # 起库（幂等），默认 127.0.0.1:5432
./scripts/pg.sh status        # 容器 / 健康 / 表数量
./scripts/pg.sh psql          # 交互终端
./scripts/pg.sh down          # 停库，数据卷保留
./scripts/pg.sh reset --yes   # 删容器 + 删数据卷（不可恢复）
```

连接串写进 `.env`：

```env
QP_DATABASE_URL=postgresql+psycopg://quantpilot:quantpilot_dev@127.0.0.1:5432/quantpilot
```

建表（幂等，含种子数据）：

```bash
python -m app.db.cli init-db --seed
```

**三个设计取舍**：

1. 用**命名卷**不绑 Windows 目录——PG 初始化要 `chmod 700`，virtiofs 不认 POSIX 权限，绑过去必失败
2. locale 固定 `UTF8` + `C`——跨机器 collation 一致，避免 `ORDER BY` 与索引行为漂移
3. 脚本不自动 init machine——共享资源且要拉几百 MB 系统镜像，只检查连通性并给出修复指引

可调参数：`QP_PG_PORT`（5432）、`QP_PG_USER` / `QP_PG_PASSWORD`（quantpilot / quantpilot_dev）、
`QP_PG_DB`（quantpilot）、`QP_PG_VOLUME`（quantpilot-pgdata）、`QP_PG_IMAGE`（postgres:16-alpine）。

---

## 6. 排错手册

| 现象 | 原因 | 处理 |
|---|---|---|
| **podman 退出码 125** | machine 没运行（Windows 重启后不自启） | `podman info` 确认 → `podman machine start` |
| `failed to start command wsl.exe: Access is denied` | `wsl.exe` 被安全策略/杀软拦截 | 从拦截名单移除，或在**自己的终端**手动跑 `podman machine start` |
| `machine init` 卡在 `oci-put-blob` 0 字节 | 网络抖动导致下载未完成 | 停掉任务，清 `~/.local/share/containers/podman/machine/wsl/cache/` 下 0 字节文件与 `*.lock`，重试 |
| 拉镜像超时 | docker.io 国内不稳 | 换镜像源，或 `QP_PG_IMAGE` / `QP_SANDBOX_IMAGE` 指国内源 |
| **端口 5432/8000 被占用** | 已有 PG / 另一个 uvicorn | `QP_PG_PORT=5433 ./scripts/pg.sh up`（同步改连接串）；uvicorn 换 `--port` |
| 容器启动即退码 3 | `entrypoint.sh` 检测到 `/data/qlib` 为空 | 检查数据挂载路径与 `QP_QLIB_PROVIDER_URI` |
| 挂载 `permission denied` | rootless podman 读权限 / SELinux | 脚本已自动加 `:Z`；确认数据目录对当前用户可读 |
| `import qlib` 报路径错误 | 在 qlib 源码目录内 import | 换工作目录（镜像内 `WORKDIR=/work` 已规避） |
| 数据目录校验失败 | 未跑 `prepare_data.sh`，或 tar 层级不对 | 解压必须 `--strip-components=1` |
| 执行超时 | 表达式过于复杂 | 增大 `QP_TIMEOUT_SEC`；查 `signal.expression` 嵌套深度 |
| 改了 JS/CSS 不生效 | 浏览器缓存 | 加/改 `?v=N` 版本号 |
| **测试卡死不动** | 开发机 `.env` 配了 `QP_DATABASE_URL`，测试去连库 | 已修：`tests/conftest.py` 强制清空该变量。若仍卡，查是否有测试绕过 conftest |
| 结果页出现"本地演示数据"横幅 | 后端没连上或沙箱不可用 | 按横幅提示排查；**这不是 bug，是故意不静默** |
| 观察期"跑今天"返回 `ok=false` | 日更行情管道未接入 | 预期行为（P1.6 只有骨架），不是故障 |

> 看容器日志：`podman logs -f quantpilot-pg`；沙箱执行日志在 `sandbox/work/<task_id>/`。

---

## 7. 部署验收

装完之后按这节确认"装对了"。**不是功能验收，是链路验收**——证明 Spec 生成、代码执行、
结果回传三段都通。

### 7.1 单条样例算"成功"的四个条件（必须同时满足）

| # | 判定项 | 通过条件 |
|---|---|---|
| 1 | Spec 合法 | Stage A 产出通过全部校验，无 `SPEC_VALIDATION_ERROR` |
| 2 | 关键字段匹配 | 期望字段一致（等价表达式算通过） |
| 3 | 代码执行 | 过 AST 检查，沙箱内无报错跑完（允许 ≤2 轮修复） |
| 4 | 结果合法 | `metrics.annual_return/sharpe/max_drawdown` 为有限数值，`series.nav` 非空 |

**总标准：10 条中 ≥ 6 条成功。**

### 7.2 十条样例

| 编号 | 场景 | 期望 |
|---|---|---|
| C01 | 动量（基线） | `$close/Ref($close,20)-1`，desc，topk 20，month |
| C02 | 均值回归 / 反转 | 5 日反转，asc 或取负 |
| C03 | 多因子合成 | 动量 + 量比组合表达式 |
| C04 | 低波动 | `Std($close,20)` 升序（越低越好） |
| C05 | 成交量 / 放量 | 量比 `$volume/Mean($volume,5)` |
| C06 | 自定义标的池 | `universe.type=custom` + 合法 symbol 列表 |
| C07 | 风控约束 | `risk.max_single_weight` 生效，持仓不超限 |
| C08 | 交易成本与样本外 | `cost` 生效；`split` 有 in/out sample |
| C09 | 行业轮动（近似） | 降级为板块内动量，并在 caveats 说明 |
| C10 | 复合自然语言 | 多诉求时给澄清问题或明确降级，不瞎猜 |

**等价表达式判定**：`$close/Ref($close,20)-1` 配 desc ≡ `Ref($close,20)/$close` 配 asc；
`-(...)` 配 desc ≡ `(...)` 配 asc；`monthday` 缺省视为 1。

### 7.3 回归

```bash
# 服务端（在 quantpilot-server/ 下）
python -m pytest -q                              # 全量（当前 160 个）
python -m pytest tests/test_tracking.py -q       # 观察期记账
python -m pytest tests/test_ast_guard.py -q      # 沙箱静态防护

# 前端（在 quantpilot-h5/ 下，零依赖）
node --test tests/storage.test.js                # 本地历史持久化（10 个）
```

---

## 8. 日常运维

| 事项 | 做法 |
|---|---|
| 改配置 | 改 `.env` → **重启 uvicorn**（无热加载） |
| 改前端 | 改 JS/CSS 的 `?v=N` → 刷新 |
| 更新行情 | 重跑 `prepare_data.sh`（同名覆盖）。**注意：当前是全量替换，不是增量** |
| 备份 | PG 数据在 podman 卷 `quantpilot-pgdata`；`podman volume export` 导出。行情数据在 `sandbox/data/cn_data` |
| 看任务 | `GET /api/v1/tasks/{id}`；观察期看 `/tracks/{id}/journal` |
| 重启后 | `podman machine start` → `./scripts/pg.sh up` → 起 uvicorn |
| 清缓存 | 无共享缓存；qlib 的 DatasetCache 是进程内，重启即清 |

> **待补的运维能力**：结构化日志、任务追踪、成本看板、失败归因面板（Phase 2）。
> 现在只有 uvicorn 标准输出与 `sandbox/work/` 下的执行产物。

---

## 相关文档

| 文档 | 什么时候看 |
|---|---|
| `USER-GUIDE.md` | 装完了要给用户讲怎么用，或自己想把功能走一遍 |
| `design-baseline.md` | 想改某个设计（比如换 runner、换存储），先看当初为什么这么定 |
| `contracts.md` | 要对接口写客户端、或改 API 字段 |
