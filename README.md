# 量航 QuantPilot

**一句话愿景：让不懂量化的人，也能用一句话把想法变成可验证的策略。**

> 状态：Phase 1.6 策略观察期（后端骨架完成，160 个测试通过）　更新：2026-11-24
> 模拟盘项目，仅做回测与研究，**不涉及实盘、不构成投资建议**。

## 项目结构

```
QuantPilot/
├── quantpilot-server/       # 服务端
│   ├── app/
│   │   ├── schemas/         # StrategySpec 契约（核心）
│   │   ├── core/            # 配置、算子白名单
│   │   ├── services/llm/    # AI 生成层 A/B/C + AST 守卫
│   │   ├── services/intent/ # 零代码意图层：中文想法 → IntentFrame → Spec
│   │   ├── data/            # 数据工坊 DataFoundry
│   │   ├── models/          # 模型工坊 Model Zoo
│   │   ├── tracking/        # 策略观察期 Track（日更信号 + 纸面记账）
│   │   └── services/executor/ # 沙箱执行器
│   ├── prompts/             # Prompt 模板 + few-shot + 代码模板
│   ├── sandbox/             # Containerfile + podman 脚本 + 数据
│   ├── scripts/             # pg.sh 等本地运维脚本
│   └── tests/               # 160 个测试
├── quantpilot-h5/           # H5 唯一客户端（原生 JS，离线可用，Phase 3 转 PWA）
│   └── js/intent.js         # 前端同构语义层：模板/向导/一句话 → IntentFrame
└── docs/                    # 只有三份：设计基线 / 操作手册 / 契约
```

## 文档索引

只有四份，按读者分：

| 文档 | 读者 | 说明 |
|------|------|------|
| [**设计与决策基线**](docs/design-baseline.md) | 做决策的人 | **唯一核心文档**：产品定位、系统架构、零代码交互、策略观察期、存储与数据构建、完成度与缺口 |
| [使用手册](docs/USER-GUIDE.md) | **用产品的人** | 三个入口怎么用、结果页每块怎么读、怎么判断结果可不可信、能接住什么 |
| [部署手册](docs/DEPLOYMENT.md) | 装环境的人 | 环境、配置、启动、沙箱、本地 PG、14 条排错、部署验收 |
| [契约速查](docs/contracts.md) | 写代码的人 | StrategySpec 字段、Result 结构、API 端点、状态机 |

> 旧文档已合并删除，留档在 `.workbuddy/archive/2026-11-24-docs-consolidation/`。

## 快速开始

### 本地验证（无需 podman/网络）
```bash
cd quantpilot-server
python -m pytest tests/ -q        # 160 个测试
```

### H5 demo
直接在浏览器打开 `quantpilot-h5/index.html`，或用预览面板查看。
纯静态、离线可用、数据为前端 mock。

### 沙箱（需在自己终端，且已装 podman）
```powershell
cd quantpilot-server
powershell -ExecutionPolicy Bypass -File sandbox\bootstrap_windows.ps1   # 构建镜像 + 冒烟
powershell -ExecutionPolicy Bypass -File sandbox\run_acceptance.ps1      # 10 条验收
```

## 当前进度

- ✅ 服务端骨架、契约、AI 生成、AST 守卫、沙箱执行器
- ✅ 数据工坊、模型工坊（130 测试通过）
- ✅ 零代码意图层（LLM 只理解不写代码，`POST /intents/parse`）
- ✅ 数据平台后端：PostgreSQL 持久化 + CSV 导入审核 API
- ✅ H5 demo（含因子中心、预测模型、真实 API 接入）
- ✅ H5 三入口：模板卡 / 三问向导 / 「我理解成了这样」确认卡，统一收敛到 IntentFrame
- ✅ 可加到手机主屏（PWA manifest + 图标 + standalone 适配）；**不做原生 App，H5 即客户端**
- ✅ 失败不静默：mock 结果一律挂「不是真实回测」横幅并说明原因
- ⏳ Phase 0 遗留：10 条验收样例真实成功率验证
- ⏳ Phase 1.5：Admin 后台、因子在线评测回填、资产权限

详细见 [设计基线 §1.4 阶段排期](docs/design-baseline.md)。

## 核心理念

1. 契约先行：`StrategySpec` 是三方唯一契约
2. 文档先行：只维护三份（设计基线 / 操作手册 / 契约），不堆积过程文档
3. 资产化：数据与模型都是可复用资产
4. 引擎解耦：编排自研，回测用 qlib
5. 安全内建：断网沙箱 + AST 白名单
6. 模拟优先：不碰实盘
