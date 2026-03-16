# AGENTS — 多智能体团队定义与协作协议

> Sentinel 系统采用**分层多智能体架构**，各 Agent 职责清晰、边界明确，通过 Redis 总线和 OpenClaw 框架协同运作。

---

## 1. 智能体总览

```
┌─────────────────────────────────────────────────────────────────┐
│                   Sentinel 智能体宇宙                            │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Sentinel-A  │  │  Analyst-B   │  │     Guardian-C        │  │
│  │  主控哨兵   │  │  量化分析师  │  │     风控守卫          │  │
│  │（核心 AI）  │  │（数据挖掘）  │  │（止损 / 熔断执行）   │  │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬───────────┘  │
│         │                │                       │              │
│         └────────────────┴───────────────────────┘              │
│                         Redis 总线                              │
│                   (OPENCLAW_ALERTS 频道)                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Sentinel-A — 主控哨兵（Primary）

**文件**: `sentinel_daemon.py` + `personas/Sentinel-A.md`
**模型**: DeepSeek-R1/V3 via Volcengine ARK (`ep-20260312230909-pskjv`)
**运行模式**: 常驻后台守护进程（`schedule` 调度）

### 职责
| 任务 | 触发时机 | 输出 |
|------|----------|------|
| 每日复盘战报 | 工作日 16:30 | Markdown 报告 → Redis `OPENCLAW_ALERTS` |
| 实盘预警 | 按需调用 | 弹窗推送至 OpenClaw 前端 |
| 心跳信号 | 每 5 分钟 | Redis `sentinel:heartbeat` 键更新 |

### 通信接口
```
发布: REDIS PUBLISH OPENCLAW_ALERTS <JSON>
格式: { "type": "每日战报" | "预警" | "心跳", "content": "..." }
心跳: REDIS SETEX sentinel:heartbeat 60 <ISO8601_TIMESTAMP>
```

### 关键参数（环境变量）
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ARK_API_KEY` | — | DeepSeek ARK API 密钥（必填） |
| `ARK_MODEL_ID` | `ep-20260312230909-pskjv` | 模型 Endpoint ID |
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接地址 |
| `REPORT_TIME` | `16:30` | 每日复盘触发时间（24h 制） |

---

## 3. Analyst-B — 量化分析师（Specialist）

**文件**: `backtest_engine.py`
**模型**: DeepSeek（时光机过滤器）
**运行模式**: 按需调用（命令行 / API）

### 职责
- 离线回测：从 akshare 拉取历史 K 线，运行 `SentinelBaseStrategy`
- AI 过滤：将突破信号送入 DeepSeek「时光机」二次审核
- 对比分析：输出纯量化 vs AI 过滤双路径胜率/回撤/夏普对比图
- 数据导出：生成 `backtest_result.png` 和 `_trades.json`

### 调用示例
```bash
# 基础使用
python /app/backtest_engine.py --symbol 600519 --start 20230101 --end 20240101

# 从 CSV 载入并启用 AI 过滤
export ARK_API_KEY="your_key"
python /app/backtest_engine.py --csv my_data.csv --output result.png

# 完整参数
python /app/backtest_engine.py \
  --symbol 000001 \
  --start  20220101 \
  --end    20241231 \
  --cash   500000 \
  --output /tmp/backtest_output.png
```

---

## 4. Guardian-C — 风控守卫（Risk Manager）

**实现**: 嵌入在 `SentinelBaseStrategy` 策略逻辑中
**触发**: 实时（每个 K 线 bar）

### 风控规则矩阵
| 规则 | 触发条件 | 执行动作 |
|------|----------|----------|
| 技术止损 | `close < buy_price * 0.92` | 强制卖出，记录 `stop_loss` |
| 时间止损 | 持仓 > 10 交易日无启动 | 主动清仓 |
| 回撤熔断 | 组合回撤 > 25% | 全仓清出，通知用户 |
| 情绪退潮 | 连板健康度 < 40% | 降至半仓，发出预警 |

---

## 5. sentinel-alert Skill — OpenClaw 前端推送员

**文件**: `skills/sentinel-alert/index.js`
**协议**: Redis Pub/Sub 订阅

### 功能
- 订阅 `OPENCLAW_ALERTS` 频道
- 将消息通过 `app.chat.broadcast()` 注入 OpenClaw 聊天 UI
- 每分钟检查 `sentinel:heartbeat` 键确认守护进程存活
- 断线自动重连（指数退避，上限 60 秒）

---

## 6. 智能体通信协议

### 6.1 消息格式规范
```json
{
  "type":      "每日战报 | 实盘预警 | 板块异动 | 系统通知",
  "content":   "Markdown 格式内容",
  "timestamp": "2026-03-16T16:30:00+08:00",
  "source":    "Sentinel-A | Analyst-B | Guardian-C",
  "level":     "info | warning | critical"
}
```

### 6.2 优先级定义
| 级别 | 类型 | OpenClaw 表现 |
|------|------|---------------|
| `critical` | 熔断预警、系统故障 | 红色强制弹窗 |
| `warning` | 止损触发、情绪退潮 | 橙色通知 |
| `info` | 每日战报、心跳确认 | 蓝色消息注入 |

---

## 7. 扩展 Agent 注册规范

如需添加新 Agent，请在 `/root/.openclaw/agents/` 创建同名 `.md` 文件，并遵循以下格式：

```markdown
# AGENT: <代号>
**类型**: Primary / Specialist / Utility
**触发**: 事件驱动 / 定时 / 按需
**通信**: Redis 频道 / OpenClaw API / 内部调用

## 职责
...

## 接口
...
```
