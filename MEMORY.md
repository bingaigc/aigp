# MEMORY — Sentinel 终极量化交易系统

> 本文件记录系统的完整架构、**所有环境变量**、部署步骤及运维说明。
> 每次对系统做出重大变更时，请同步更新本文件。

---

## 目录

1. [系统架构](#1-系统架构)
2. [环境变量完整列表](#2-环境变量完整列表)
3. [Hugging Face Spaces 部署指南](#3-hugging-face-spaces-部署指南)
4. [服务端口速查](#4-服务端口速查)
5. [AI 员工调度时间表](#5-ai-员工调度时间表)
6. [数据持久化机制](#6-数据持久化机制)
7. [目录结构说明](#7-目录结构说明)
8. [常见问题排查](#8-常见问题排查)
9. [sentinel-alert Skill 安装指南](#9-sentinel-alert-skill-安装指南)
10. [2核16G 低内存部署调优](#10-2核16g-低内存部署调优)

---

## 1. 系统架构

```
                        ┌─────────────────────────────────────────┐
                        │         Nginx :7860  (统一入口)          │
                        │  /      → OpenClaw 前端  :7861           │
                        │  /term  → ttyd Web 终端  :7862           │
                        └──────────────┬──────────────────────────┘
                                       │
                     ┌─────────────────▼──────────────────┐
                     │         OpenClaw 网关  :7861        │
                     │  React 前端 + 对话 UI + 技能面板    │
                     └──────────┬──────────────────────────┘
                                │  Redis Pub/Sub  OPENCLAW_ALERTS
              ┌─────────────────┼─────────────────────────────┐
              │                 │                             │
    ┌─────────▼──────┐ ┌────────▼───────┐ ┌──────────────────▼──┐
    │  Sentinel-A 🦅 │ │  Analyst-B 📊  │ │    Guardian-C 🛡️    │
    │  每日复盘战报  │ │  板块轮动分析  │ │   实时风险评估       │
    └────────────────┘ └────────────────┘ └──────────────────────┘
              │                                   │
    ┌─────────▼──────┐           ┌────────────────▼───────────┐
    │  Scout-D  🔭   │           │  Redis Hash guardian:status │
    │  涨停板侦察    │◄──────────│  (risk_score, board_health) │
    └────────────────┘           └─────────────────────────────┘
              │
    ┌─────────▼──────┐
    │  Redis  :6379  │   (内存总线，所有 Agent 共享)
    └────────────────┘
              │
    ┌─────────▼──────────────────────────────────────────┐
    │           HF Hub Dataset  (可选，持久化)            │
    │  restore.py ← 启动时拉取  /  backup.py → 定时上传  │
    └─────────────────────────────────────────────────────┘
```

**技术栈**

| 层 | 组件 |
|---|---|
| 前端 UI | OpenClaw（`ghcr.io/openclaw/openclaw:main`） |
| AI 大脑 | DeepSeek R1/V3（通过 Volcengine ARK API，兼容 OpenAI SDK） |
| 数据总线 | Redis 6 |
| 行情数据 | akshare（A 股实时 + 历史）|
| Web 终端 | ttyd 1.7.7 |
| 反向代理 | Nginx |
| 持久化 | Hugging Face Hub Dataset（可选）|
| 回测框架 | Backtrader + Matplotlib |

---

## 2. 环境变量完整列表

> **速查表 — 按优先级排列，标★为强烈推荐配置**

| 变量名 | 必填 | 默认值 | 用途 |
|---|---|---|---|
| `ARK_API_KEY` | ★ 推荐 | 无 | DeepSeek AI 分析 Key |
| `GATEWAY_TOKEN` | ★ 推荐 | `OpenClaw_Secure_2026!` | OpenClaw 网关安全令牌 |
| `HF_TOKEN` | 仅HF | 无 | HF Hub 备份令牌 |
| `HF_DATASET` | 仅HF | 无 | HF Hub 备份仓库 ID |
| `ARK_MODEL_ID` | 可选 | `ep-20260312230909-pskjv` | 模型端点 ID |
| `REDIS_URL` | 可选 | `redis://127.0.0.1:6379` | Redis 连接地址 |
| `REPORT_TIME` | 可选 | `16:30` | 每日复盘触发时间 |
| `OPENCLAW_CHANNEL_ID` | 可选 | 无 | OpenClaw 全局推送频道 ID |
| `SENTINEL_A_CHANNEL_ID` | 可选 | 无 | Sentinel-A 专属频道 ID |
| `ANALYST_B_CHANNEL_ID` | 可选 | 无 | Analyst-B 专属频道 ID |
| `GUARDIAN_C_CHANNEL_ID` | 可选 | 无 | Guardian-C 专属频道 ID |
| `SCOUT_D_CHANNEL_ID` | 可选 | 无 | Scout-D 专属频道 ID |
| `HEARTBEAT_TTL` | 可选 | `360` | 心跳 key 存活时间（秒）|
| `NODE_OPTIONS` | 可选 | `--max-old-space-size=512` | Node.js 内存上限 |
| `BACKUP_INTERVAL_MIN` | 可选 | `60` | HF 备份间隔（分钟）|

### 2.1 必填变量（不设则 AI 功能完全离线）

| 变量名 | 示例值 | 说明 | 用于 |
|---|---|---|---|
| `ARK_API_KEY` | `sk-xxxxxxxxxxxxxxxx` | **Volcengine ARK 平台 API Key**，DeepSeek 所有功能的唯一凭证。未设置时系统仍可启动，但所有 AI 分析（复盘战报、板块轮动、风控播报、涨停侦察、AI 回测过滤）将降级为离线模式。 | `config.py` `sentinel_daemon.py` `analyst_daemon.py` `guardian_daemon.py` `scout_daemon.py` `backtest_engine.py` |

> **获取 ARK_API_KEY：**  
> 登录 [Volcengine 控制台](https://console.volcengine.com/ark) → 模型推理 → API 密钥 → 创建。

---

### 2.2 可选变量（均有默认值，按需覆盖）

| 变量名 | 默认值 | 说明 | 用于 |
|---|---|---|---|
| `ARK_MODEL_ID` | `ep-20260312230909-pskjv` | Volcengine ARK 模型推理端点 ID。若您在 ARK 控制台创建了新的推理接入点，请修改此值。 | 所有 Python 守护进程 + 回测引擎 |
| `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | Volcengine ARK API 基础 URL（OpenAI 兼容格式）。一般无需修改，仅在切换区域或私有化部署时调整。 | `backtest_engine.py` |
| `GATEWAY_TOKEN` | `OpenClaw_Secure_2026!` | OpenClaw 网关访问令牌。**强烈建议在生产环境修改为随机强密码。** | `config.py` → `openclaw.json` |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接地址。容器内默认使用本地 Redis，无需修改。若接入外部 Redis 集群，在此填写完整 URL，格式：`redis://[:password@]host:port[/db]`。 | 所有 Python 守护进程 |
| `REPORT_TIME` | `16:30` | Sentinel-A 每日复盘战报的触发时间（24 小时制 `HH:MM`，中国标准时间）。A 股收盘时间为 15:00，建议设置在 16:00 ~ 17:00 之间，等待数据源更新完毕。 | `sentinel_daemon.py` |
| `HEARTBEAT_TTL` | `360` | 心跳 Redis key 的存活时间（秒）。默认 360 秒，正好覆盖 5 分钟更新周期并保留 60 秒容差。调低此值会增加 Redis 写入频率，一般无需修改。 | 所有 4 个守护进程 |
| `NODE_OPTIONS` | `--max-old-space-size=512` | Node.js V8 引擎参数，用于限制老生代堆上限（MB）。默认限制为 512 MB，可按实际内存调大：如 `--max-old-space-size=768`。 | `start_hf.sh` → OpenClaw 网关进程 |

---

### 2.3 HF Hub 持久化变量（仅 Hugging Face Spaces 部署需要）

| 变量名 | 示例值 | 默认值 | 说明 |
|---|---|---|---|
| `HF_DATASET` | `your-username/openclaw-backup` | 无 | Hugging Face Dataset 仓库 ID，用于存储 `/root/.openclaw` 的完整快照。**需提前在 HF Hub 创建一个私有 Dataset 仓库。** 若不设置，启动时跳过数据恢复，关闭时跳过数据备份。 |
| `HF_TOKEN` | `hf_AbCdEfGhIjKlMn` | 无 | Hugging Face 用户访问令牌，**必须拥有对目标 Dataset 仓库的 write 权限**。在 [HF Settings → Access Tokens](https://huggingface.co/settings/tokens) 中创建，选择 `write` 类型。 |
| `BACKUP_INTERVAL_MIN` | `60` | `60` | 定时自动备份间隔（分钟）。每次备份会调用 HF Hub API 上传 `/root/.openclaw`（排除日志和 `node_modules`）。频率过高（< 30 分钟）可能触发 HF Hub 速率限制，**不建议低于 30**。 |

> ⚠️ **安全提醒：** `HF_TOKEN` 和 `ARK_API_KEY` 是高权限凭证，请务必通过 HF Spaces 的加密 **Secrets** 功能配置，**绝对不要**硬编码在代码或 Dockerfile 中。

---

### 2.5 OpenClaw 频道绑定变量（推送至 Agent 专属频道）

> 这是让消息精确推送到 **OpenClaw 中每个 Agent 绑定的专属会话频道** 的关键配置。
> 不配置时，Skill 回退到 `agentId` 路由 + 全局广播（消息会出现在所有打开的会话窗口）。

**如何获取频道 ID：**
1. 在 OpenClaw 前端打开某个 Agent 的对话窗口
2. 查看浏览器地址栏：`https://.../chat?channelId=abc123def456`（`channelId` 参数即为频道 ID）
3. 或调用管理 API：`GET http://127.0.0.1:7861/api/channels?agentId=sentinel-a`

| 变量名 | 默认值 | 说明 | 用于 |
|---|---|---|---|
| `OPENCLAW_CHANNEL_ID` | 无 | **全局覆盖**：所有 Agent 的消息统一推送至此频道 ID。适合单频道模式（一个聊天窗口接收所有 Agent 消息）。优先级最高，会覆盖各 Agent 专属频道。 | `skills/sentinel-alert/index.js` |
| `SENTINEL_A_CHANNEL_ID` | 无 | Sentinel-A 🦅 的专属 OpenClaw 频道 ID。设置后，主控哨兵的每日战报和预警将只推送到此频道。 | `skills/sentinel-alert/index.js` + `config.py` |
| `ANALYST_B_CHANNEL_ID` | 无 | Analyst-B 📊 的专属 OpenClaw 频道 ID。设置后，板块轮动分析报告将只推送到此频道。 | `skills/sentinel-alert/index.js` + `config.py` |
| `GUARDIAN_C_CHANNEL_ID` | 无 | Guardian-C 🛡️ 的专属 OpenClaw 频道 ID。设置后，风险预警将只推送到此频道。 | `skills/sentinel-alert/index.js` + `config.py` |
| `SCOUT_D_CHANNEL_ID` | 无 | Scout-D 🔭 的专属 OpenClaw 频道 ID。设置后，三段侦察快报将只推送到此频道。 | `skills/sentinel-alert/index.js` + `config.py` |

**消息路由优先级（高 → 低）：**

```
1. OPENCLAW_CHANNEL_ID（全局覆盖，最高优先级）
   ↓ 未设置
2. 各 Agent 专属频道（SENTINEL_A_CHANNEL_ID 等）
   ↓ 未设置
3. agentId 路由（OpenClaw 将消息归属到对应 Agent 的绑定会话）
   ↓ 不支持
4. 全局广播（所有活动会话均可接收，兜底）
```

**推荐配置方式（HF Spaces Secrets）：**

```
# 方案一：所有 Agent 推送到同一频道（最简单）
OPENCLAW_CHANNEL_ID = abc123def456

# 方案二：各 Agent 推送到各自专属频道（最精细）
SENTINEL_A_CHANNEL_ID = ch_sentinel_xxxxxxx
ANALYST_B_CHANNEL_ID  = ch_analyst_xxxxxxx
GUARDIAN_C_CHANNEL_ID = ch_guardian_xxxxxxx
SCOUT_D_CHANNEL_ID    = ch_scout_xxxxxxx
```

---

### 2.4 环境变量优先级汇总

```
优先级（高 → 低）：
  1. HF Spaces Secrets / Docker --env 注入的环境变量
  2. 各脚本中的 os.environ.get('VAR', '默认值') 硬编码默认值
```

---

## 3. Hugging Face Spaces 部署指南

### 第一步：Fork 或上传代码到 HF Spaces

1. 在 [huggingface.co/new-space](https://huggingface.co/new-space) 创建新 Space。
2. SDK 选择 `Docker`，硬件选择 `CPU Basic`（免费）或更高配置。
3. 将本仓库代码上传到 Space（或直接 `git push`）。

### 第二步：创建备份用 Dataset 仓库（可选，但推荐）

1. 在 [huggingface.co/new-dataset](https://huggingface.co/new-dataset) 创建新的私有 Dataset 仓库。
2. 仓库 ID 记为 `your-username/openclaw-backup`（任意命名）。

### 第三步：在 Space 中配置 Secrets

进入 Space → **Settings** → **Repository secrets**，添加以下密钥：

| Secret 名称 | 值 | 是否必填 |
|---|---|---|
| `ARK_API_KEY` | 您的 Volcengine ARK API Key | **必填**（AI 功能） |
| `HF_DATASET` | `your-username/openclaw-backup` | 推荐（数据持久化） |
| `HF_TOKEN` | 您的 HF write token | 推荐（数据持久化） |
| `GATEWAY_TOKEN` | 自定义强密码 | 推荐（安全加固） |
| `ARK_MODEL_ID` | 您的模型端点 ID | 可选（如与默认值不同） |
| `BACKUP_INTERVAL_MIN` | `60` | 可选（调整备份频率） |
| `REPORT_TIME` | `16:30` | 可选（调整复盘时间） |

### 第四步：启动 Space

Space 保存配置后会自动构建镜像并启动。首次启动约需 5～10 分钟（安装依赖）。

启动成功后访问 Space URL 即可看到 OpenClaw 前端界面。

---

## 4. 服务端口速查

| 端口 | 服务 | 对外访问路径 |
|---|---|---|
| **7860** | Nginx 统一入口（HF Spaces 唯一开放端口） | Space URL 根地址 |
| 7861 | OpenClaw 网关（Node.js）| 通过 Nginx 代理：`/` |
| 7862 | ttyd Web 终端 | 通过 Nginx 代理：`/term` |
| 6379 | Redis（内部） | 仅容器内访问 |

---

## 5. AI 员工调度时间表

所有时间均为中国标准时间（CST, UTC+8），仅工作日执行。

| Agent | 触发时间 | 任务 |
|---|---|---|
| 🦅 Sentinel-A | `REPORT_TIME`（默认 `16:30`）| 抓取涨停池 + 龙虎榜 → DeepSeek 复盘战报 |
| 📊 Analyst-B | `09:30` `10:30` `11:00` `13:30` `14:30` | 概念/行业板块轮动扫描 → 板块分析报告 |
| 🛡️ Guardian-C | 盘中每 5 分钟（`09:25`~`15:05`）| 大盘风险系数计算 → 超阈值时触发风险播报 |
| 🔭 Scout-D | `09:15`（盘前） `11:30`（上午） `14:30`（尾盘）| 高质量涨停板筛选 → 侦察快报 |
| 💾 HF Backup | 每 `BACKUP_INTERVAL_MIN` 分钟 | 增量上传 `/root/.openclaw` → HF Hub |

**心跳检测**：每个 Agent 每 5 分钟向 Redis 写入一次心跳键（`sentinel:heartbeat`、`analyst:heartbeat`、`guardian:heartbeat`、`scout:heartbeat`），TTL 为 60 秒，前端 Skill 据此判断 Agent 存活状态。

---

## 6. 数据持久化机制

### 6.1 持久化目录

```
/root/.openclaw/
├── openclaw.json          # OpenClaw 主配置（网关、模型、Agent 注册）
├── agents/                # Agent 文档（AGENTS.md, BOOTSTRAP.md 等）
├── personas/              # AI 员工人格定义（*.md）
├── skills/sentinel-alert/ # Redis→前端推送 Skill
└── （运行时产生）
    ├── conversations/     # 对话历史
    ├── memory/            # Agent 长期记忆
    └── ...
```

### 6.2 备份/恢复流程

```
容器启动
  └─► restore.py
        ├─ HF_DATASET 和 HF_TOKEN 均已设置？
        │   ├─ 是 → snapshot_download() → /root/.openclaw
        │   └─ 否 → 跳过（首次部署或未配置，正常行为）
        └─► 继续启动 Redis + OpenClaw + 四大 AI 员工

容器运行中（后台循环）
  └─► 每 BACKUP_INTERVAL_MIN 分钟 → backup.py
        ├─ HF_DATASET 和 HF_TOKEN 均已设置？
        │   ├─ 是 → upload_folder(/root/.openclaw → HF Dataset)
        │   │         排除: *.log, tmp/*, node_modules/**, .git/**
        │   └─ 否 → 跳过

容器关闭（SIGTERM / SIGINT）
  └─► cleanup trap → backup.py（最终备份）→ 停止 Redis → 退出
```

### 6.3 备份排除规则

`backup.py` 的 `IGNORE_PATTERNS`：

```python
['*.log', 'tmp/*', '**/node_modules/**', '**/.git/**']
```

---

## 7. 目录结构说明

```
aigp/
├── Dockerfile              # 镜像构建（linux/amd64，单端口 7860）
├── start_hf.sh             # 容器启动脚本（编排所有服务）
├── config.py               # OpenClaw 配置初始化 + 多 Agent 注册
├── restore.py              # 容器启动时从 HF Hub 恢复数据
├── backup.py               # 定时/关闭时备份数据到 HF Hub
├── sentinel_daemon.py      # Sentinel-A 🦅 每日复盘守护进程
├── analyst_daemon.py       # Analyst-B 📊 板块轮动守护进程
├── guardian_daemon.py      # Guardian-C 🛡️ 风险监控守护进程
├── scout_daemon.py         # Scout-D 🔭 涨停板侦察守护进程
├── backtest_engine.py      # 离线回测引擎（Backtrader + AI 过滤）
├── nginx.conf              # Nginx 反向代理配置
├── agents/                 # OpenClaw Agent 文档
│   ├── AGENTS.md           # 团队协作规范
│   ├── BOOTSTRAP.md        # 系统引导说明
│   ├── HEARTBEAT.md        # 心跳机制说明
│   ├── IDENTITY.md         # 系统身份定义
│   ├── SOUL.md             # AI 员工灵魂文档
│   ├── TOOLS.md            # 工具使用手册
│   └── USER.md             # 用户使用指南
├── personas/               # AI 员工人格定义
│   ├── Sentinel-A.md       # 主控哨兵人格
│   ├── Analyst-B.md        # 量化分析师人格
│   ├── Guardian-C.md       # 风控守卫人格
│   └── Scout-D.md          # 游骑侦察人格
├── skills/sentinel-alert/  # OpenClaw Skill（前端消息路由）
│   ├── index.js            # Skill 主逻辑（Redis 订阅 → 前端推送）
│   ├── manifest.json       # Skill 元数据
│   └── package.json        # npm 依赖
└── MEMORY.md               # ← 本文件
```

---

## 8. 常见问题排查

### Q: Space 启动后 AI 分析不工作，只看到「离线模式」

**原因：** `ARK_API_KEY` 未设置或设置有误。  
**解决：** 进入 Space Settings → Repository secrets，确认 `ARK_API_KEY` 存在且值正确（无多余空格）。

---

### Q: 每次重启 Space 后对话历史消失

**原因：** `HF_DATASET` 或 `HF_TOKEN` 未配置，数据未持久化到 HF Hub。  
**解决：**
1. 在 HF Hub 创建私有 Dataset 仓库。
2. 创建 write 权限 Token：[HF Settings → Tokens](https://huggingface.co/settings/tokens)。
3. 在 Space Secrets 中添加 `HF_DATASET`（仓库 ID）和 `HF_TOKEN`。

---

### Q: 备份失败，日志显示「429 Too Many Requests」

**原因：** `BACKUP_INTERVAL_MIN` 设置过小，触发 HF Hub 速率限制。  
**解决：** 将 `BACKUP_INTERVAL_MIN` 调整为 `60`（默认值）或更大值。

---

### Q: OpenClaw 前端提示「网关连接失败」

**原因：**
1. OpenClaw Node.js 网关未能在 15 秒内启动（镜像首次拉取慢）。
2. `GATEWAY_TOKEN` 与前端配置不匹配。

**解决：**
- 查看 Space 日志中 `[OpenClaw 🌐]` 前缀的行，确认网关是否有报错。
- 确认 `GATEWAY_TOKEN` 配置一致（前后端使用相同值）。

---

### Q: 如何在 Spaces 外（本地/自有服务器）部署？

```bash
docker build -t sentinel-aigp .
docker run -d \
  -p 7860:7860 \
  -e ARK_API_KEY="your_key" \
  -e ARK_MODEL_ID="your_endpoint_id" \
  -e GATEWAY_TOKEN="your_strong_password" \
  -e HF_DATASET="your-user/openclaw-backup" \
  -e HF_TOKEN="hf_your_token" \
  --name sentinel-aigp \
  sentinel-aigp
```

---

### Q: sentinel-alert Skill 没有生效，前端收不到 AI 员工消息

**原因：**
1. `/root/.openclaw/skills/sentinel-alert/node_modules/` 不存在（npm 依赖未安装）。
2. Skill 目录不在 `/root/.openclaw/skills/` 下（路径错误）。
3. OpenClaw 网关重启前加载了旧状态。

**解决（在 ttyd 终端执行）：**

```bash
# 重新安装并让 OpenClaw 自检
cp -r /app/skills/sentinel-alert /root/.openclaw/skills/
npm install --prefix /root/.openclaw/skills/sentinel-alert --omit=dev
node /app/dist/entry.js doctor --fix
```

详细步骤见 **[第 9 节：sentinel-alert Skill 安装指南](#9-sentinel-alert-skill-安装指南)**。

---

### Q: 如何手动触发一次回测？

进入 ttyd Web 终端（访问 `/term`）：

```bash
# 基础回测（平安银行，使用默认参数）
python3 /app/backtest_engine.py

# 指定股票和时间段
python3 /app/backtest_engine.py --symbol 600519 --start 20230101 --end 20241231

# 从 CSV 文件载入数据
python3 /app/backtest_engine.py --csv /path/to/data.csv --output /tmp/result.png
```

回测结果图表保存在执行目录（默认 `backtest_result.png`），可通过 ttyd 终端的文件浏览功能查看。

---

## 9. sentinel-alert Skill 安装指南

`sentinel-alert` 是系统的前端消息路由层：它订阅 Redis `OPENCLAW_ALERTS` 频道，将四大 AI 员工的播报通过 OpenClaw 的 `chat.broadcast` 接口实时推送至前端 UI。

> **工作原理：** OpenClaw 在启动时会自动扫描 `/root/.openclaw/skills/` 目录，凡包含有效 `manifest.json` 的子目录均会被自动加载为 Skill，**无需手动在 UI 中注册**。

---

### 9.1 场景一：Docker 部署（推荐，已自动完成）

Dockerfile 在镜像构建阶段已完成 Skill 的安装和 npm 依赖安装：

```dockerfile
# Dockerfile 第 12 步（已内置，无需手动执行）
COPY skills/sentinel-alert/ /root/.openclaw/skills/sentinel-alert/
RUN npm install --prefix /root/.openclaw/skills/sentinel-alert \
        --omit=dev --silent \
    && npm cache clean --force
```

容器启动时，`start_hf.sh` 还有一个安全兜底检查：

```bash
# 若 node_modules 因某种原因丢失，自动重新安装
SKILL_DIR="/root/.openclaw/skills/sentinel-alert"
if [ -f "$SKILL_DIR/package.json" ] && [ ! -d "$SKILL_DIR/node_modules" ]; then
    npm install --prefix "$SKILL_DIR" --silent --omit=dev
fi
```

**结论：使用 Docker 部署时，无需任何额外操作，Skill 已自动安装。**

---

### 9.2 场景二：在运行中的容器内手动安装（ttyd 终端或 docker exec）

如果 Skill 文件因重启/数据恢复等原因丢失，在 ttyd Web 终端（访问 `/term`）或 `docker exec` 中依次执行：

```bash
# 第一步：将 Skill 源码复制到 OpenClaw skills 目录
cp -r /app/skills/sentinel-alert /root/.openclaw/skills/

# 第二步：安装 npm 依赖（仅 redis 包，约 2 秒）
npm install --prefix /root/.openclaw/skills/sentinel-alert --omit=dev

# 第三步：让 OpenClaw 重新自检并加载新 Skill
node /app/dist/entry.js doctor --fix

# 验证：查看 Skill 目录结构
ls -la /root/.openclaw/skills/sentinel-alert/
# 应包含: index.js  manifest.json  package.json  node_modules/
```

如果 `doctor --fix` 后 Skill 仍未生效，重启 OpenClaw 网关即可（在 ttyd 中）：

```bash
# 找到 OpenClaw 网关进程 PID
pgrep -f "entry.js gateway"

# 终止旧进程并重启
kill <PID>
PORT=7861 node /app/dist/entry.js gateway &
```

---

### 9.3 场景三：本地开发环境安装

在本地机器上开发调试时：

```bash
# 进入 Skill 源码目录，安装依赖
cd /path/to/aigp/skills/sentinel-alert
npm install

# 将 Skill 复制到本地 OpenClaw 目录（路径因系统而异）
cp -r . ~/.openclaw/skills/sentinel-alert/

# 重启本地 OpenClaw
openclaw restart   # 或直接 Ctrl+C 后重新启动
```

---

### 9.4 安装后验证方法

```bash
# 方法一：检查 Skill 目录和依赖是否存在
ls /root/.openclaw/skills/sentinel-alert/node_modules/redis

# 方法二：向 Redis 频道发送测试消息，检查 OpenClaw 前端是否弹出通知
redis-cli PUBLISH OPENCLAW_ALERTS \
  '{"type":"测试","content":"sentinel-alert 安装验证","source":"Sentinel-A","level":"info"}'

# 方法三：查看 OpenClaw 网关日志中是否有 Skill 加载成功信息
docker logs -f sentinel 2>&1 | grep -i "sentinel-alert\|Multi-Agent Hub\|skill"
# 正常输出示例：
# [Multi-Agent Hub] 初始化中... 监控 AI 员工: Sentinel-A, Analyst-B, Guardian-C, Scout-D
# ✅ [Multi-Agent Hub] 已连接至 Redis，监听频道: OPENCLAW_ALERTS

# 方法四：检查 Redis 心跳键（守护进程正常运行时应有值）
redis-cli GET sentinel:heartbeat
redis-cli GET analyst:heartbeat
redis-cli GET guardian:heartbeat
redis-cli GET scout:heartbeat
```

---

### 9.5 Skill 文件说明

| 文件 | 说明 |
|---|---|
| `manifest.json` | Skill 元数据：名称、版本、权限声明（`chat:write`、`system:events`）。OpenClaw 通过此文件发现并加载 Skill。 |
| `index.js` | Skill 主逻辑：连接 Redis → 订阅 `OPENCLAW_ALERTS` → 格式化消息（含 Agent 图标和级别前缀）→ 调用 `app.chat.broadcast()` 推送至前端。 |
| `package.json` | npm 依赖声明，唯一外部依赖为 `redis@^4.6.14`。 |

### 9.6 Skill 使用的环境变量

`index.js` 内部会读取以下环境变量（与 Python 守护进程共享同一 `REDIS_URL` 变量名）：

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接地址，Skill 通过此地址订阅消息总线 |
| `OPENCLAW_CHANNEL_ID` | 无 | 全局覆盖频道 ID：所有 Agent 消息统一推送到此频道 |
| `SENTINEL_A_CHANNEL_ID` | 无 | Sentinel-A 专属 OpenClaw 频道 ID |
| `ANALYST_B_CHANNEL_ID` | 无 | Analyst-B 专属 OpenClaw 频道 ID |
| `GUARDIAN_C_CHANNEL_ID` | 无 | Guardian-C 专属 OpenClaw 频道 ID |
| `SCOUT_D_CHANNEL_ID` | 无 | Scout-D 专属 OpenClaw 频道 ID |

> 频道 ID 获取方式：在 OpenClaw 前端打开 Agent 对话窗口 → 查看 URL 中的 `channelId` 参数。
> 详细说明见 **[第 2.5 节：OpenClaw 频道绑定变量](#25-openclaw-频道绑定变量推送至-agent-专属频道)**。

---

## 10. 2核16G 低内存部署调优

> 本节描述在 **2核16G** 配置（如 Hugging Face Spaces CPU Basic 升级版、中小型云服务器）上
> 降低运行时内存占用的所有调优措施，以及每项改动的预期效益。

### 10.1 各组件内存估算（调优前基准）

| 组件 | 估算峰值内存 | 说明 |
|---|---|---|
| Redis（无上限） | 0 ~ 无限 | 默认无 `maxmemory`，持续写入时可耗尽系统内存 |
| OpenClaw Node.js 网关 | ~400 MB | 默认 V8 老生代上限约 1.4 GB，实际使用约 400 MB |
| Sentinel-A 🦅 Python | ~250 MB | 含 pandas / numpy / akshare 重型依赖 |
| Analyst-B 📊 Python | ~250 MB | 同上 |
| Guardian-C 🛡️ Python | ~220 MB | 同上 |
| Scout-D 🔭 Python | ~220 MB | 同上 |
| Nginx（默认） | ~20 MB × (worker_processes × 2) | 默认 auto 可能产生多于所需的 worker |
| ttyd | ~15 MB | 可忽略 |
| **合计（估算）** | **~1.8 ~ 2.5 GB** | 正常负载下 16 GB 足够；极端情况下 Redis 可无限增长 |

---

### 10.2 已实施的代码级调优（无需手动操作）

以下改动已直接写入代码库，`docker pull` 最新镜像后自动生效：

#### A. Redis 内存上限（`start_hf.sh`）

```bash
redis-cli CONFIG SET maxmemory 1gb           # 内存封顶 1 GB
redis-cli CONFIG SET maxmemory-policy allkeys-lru  # 满后淘汰最久未访问的 key
```

**效益：** 防止 Redis 在历史消息堆积时无限增长，保障其他进程的内存空间。

---

#### B. Node.js 堆上限（`start_hf.sh`）

```bash
export NODE_OPTIONS="--max-old-space-size=512"
```

V8 老生代上限从默认 ~1.4 GB 降为 512 MB，触发更积极的 GC，让 Python 守护进程有更多可用内存。
可通过环境变量覆盖（如需调大）：

```bash
# 设为 768 MB（介于省内存与性能之间）
NODE_OPTIONS=--max-old-space-size=768
```

---

#### C. glibc 内存竞技场限制（`start_hf.sh`）

```bash
export MALLOC_ARENA_MAX=2
```

glibc 默认每线程最多 8 个内存竞技场（arena），在多线程 Python 进程中可导致大量内存碎片。
设为 2 后，4 个 Python 守护进程合计可节省 **~100–300 MB** 碎片内存。

---

#### D. Nginx 工作进程与缓冲区（`nginx.conf`）

```nginx
worker_processes     2;       # 明确指定为 CPU 核心数
worker_rlimit_nofile 512;     # 每 worker 最大文件描述符（默认 1024）

events {
    worker_connections 256;   # 每 worker 并发连接（默认 1024）
    use epoll;
    multi_accept on;
}

proxy_buffer_size      4k;    # 代理响应头缓冲（默认 8k）
proxy_buffers          4 4k;  # 代理响应体缓冲（默认 8×8k）
proxy_busy_buffers_size 8k;
```

**效益：** 单节点内网场景下并发连接远低于 1024；缩减缓冲区可节省 **每连接 ~56 KB** 内存，
100 并发连接下节省约 **5.5 MB**，同时降低 Nginx worker 初始化内存分配。

---

#### E. 心跳 TTL 修正（所有 4 个守护进程）

```python
# 修正前（bug）：TTL=60s，但 5 分钟才更新一次 → key 到期 4 分钟产生假告警
redis_client.setex('xxx:heartbeat', 60, ...)

# 修正后：TTL=360s，覆盖完整的 5 分钟更新周期，保留 60s 容差
redis_client.setex('xxx:heartbeat', 360, ...)
```

**效益：** 消除误报告警；TTL 从 60s 延长后，key 在 Redis 中存活更长时间，减少无效的写入开销。

---

#### F. 守护进程轮询间隔（所有 4 个守护进程）

```python
# 修改前
while True:
    schedule.run_pending()
    time.sleep(15)   # 每 15 秒唤醒一次

# 修改后
while True:
    schedule.run_pending()
    time.sleep(30)   # 每 30 秒唤醒一次
```

**效益：** 4 个守护进程每分钟总唤醒次数从 **16 次** 降为 **8 次**，减少 Python GIL 切换和
内核调度开销；在闲市（非盘中时段）效果最显著，可释放 CPU 时间片用于 GC。

---

### 10.3 可选的额外调优（根据实际负载按需启用）

以下调优未写入代码，可在运行容器时通过**环境变量**或 **Secrets** 覆盖：

#### 1. 进一步限制 Node.js 堆

```bash
NODE_OPTIONS=--max-old-space-size=384   # 更激进（适合极低负载）
```

#### 2. 调小 Redis 上限

```bash
# 若历史消息量极少，可降到 512 MB
redis-cli CONFIG SET maxmemory 512mb
```

在 ttyd 终端（`/term`）执行，立即生效，无需重启。

#### 3. 减少备份频率

```
BACKUP_INTERVAL_MIN=120   # 每 2 小时备份一次（默认 60 分钟）
```

HF Hub 备份期间 `huggingface_hub` 上传文件会额外消耗内存，降低频率可减少高峰。

#### 4. 关闭 AI 功能（纯离线模式）

不设置 `ARK_API_KEY` 时，所有守护进程自动降级为**离线模式**：
- 不加载 `openai` SDK 连接池
- 不发起任何 HTTPS 请求
- AI 分析报告改为基于规则的模板生成

内存节省约 **~20–40 MB**（无 API 连接池和响应缓存）。

---

### 10.4 调优后内存估算

| 组件 | 调优后估算 | 节省 |
|---|---|---|
| Redis | ≤ 1 GB（封顶） | 防止无限增长 |
| OpenClaw Node.js | ~300 MB | -100 MB（堆上限 512 MB）|
| 4 × Python 守护进程 | ~800 MB（合计） | -100~300 MB（MALLOC_ARENA_MAX=2）|
| Nginx | ~8 MB | -12 MB（减少 worker 缓冲区） |
| **调优后合计** | **~1.1 ~ 2.1 GB** | **节省约 0.4 ~ 0.7 GB** |

> **结论：** 2核16G 完全可以流畅运行本系统。调优后活跃内存约 1.1~2.1 GB，
> 剩余 14 GB 可作为操作系统文件缓存、Docker 镜像层缓存和峰值缓冲使用。

---

*最后更新：2026-03-16*
