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

---

### 2.3 HF Hub 持久化变量（仅 Hugging Face Spaces 部署需要）

| 变量名 | 示例值 | 默认值 | 说明 |
|---|---|---|---|
| `HF_DATASET` | `your-username/openclaw-backup` | 无 | Hugging Face Dataset 仓库 ID，用于存储 `/root/.openclaw` 的完整快照。**需提前在 HF Hub 创建一个私有 Dataset 仓库。** 若不设置，启动时跳过数据恢复，关闭时跳过数据备份。 |
| `HF_TOKEN` | `hf_AbCdEfGhIjKlMn` | 无 | Hugging Face 用户访问令牌，**必须拥有对目标 Dataset 仓库的 write 权限**。在 [HF Settings → Access Tokens](https://huggingface.co/settings/tokens) 中创建，选择 `write` 类型。 |
| `BACKUP_INTERVAL_MIN` | `60` | `60` | 定时自动备份间隔（分钟）。每次备份会调用 HF Hub API 上传 `/root/.openclaw`（排除日志和 `node_modules`）。频率过高（< 30 分钟）可能触发 HF Hub 速率限制，**不建议低于 30**。 |

> ⚠️ **安全提醒：** `HF_TOKEN` 和 `ARK_API_KEY` 是高权限凭证，请务必通过 HF Spaces 的加密 **Secrets** 功能配置，**绝对不要**硬编码在代码或 Dockerfile 中。

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

`index.js` 内部会读取 `REDIS_URL` 环境变量（与 Python 守护进程共享同一变量名）：

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接地址，Skill 通过此地址订阅消息总线 |

---

*最后更新：2026-03-16*
