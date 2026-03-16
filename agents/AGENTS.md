# AGENTS — 多智能体团队定义与协作协议

> Sentinel 系统采用**分层多智能体架构**，四大 AI 员工职责清晰、边界明确，通过 Redis 总线和 OpenClaw 框架协同运作。

---

## 1. 智能体总览

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        Sentinel 多 AI 员工宇宙                            │
│                                                                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ Sentinel-A 🦅 │  │ Analyst-B 📊 │  │ Guardian-C 🛡│  │  Scout-D 🔭 │  │
│  │   主控哨兵   │  │  量化分析师  │  │   风控守卫   │  │  游骑侦察   │  │
│  │  每日复盘   │  │  板块轮动   │  │  风险监控   │  │  三段侦察   │  │
│  │  16:30 触发  │  │  每小时扫描  │  │  每5分钟扫   │  │  3段定时   │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬──────┘  │
│         │                 │                  │                 │          │
│         └─────────────────┴──────────────────┴─────────────────┘          │
│                          Redis 总线 · OPENCLAW_ALERTS 频道                 │
│                                                                           │
│                    ┌─────────────────────────────────┐                    │
│                    │  sentinel-alert Skill v2.0       │                    │
│                    │  Multi-Agent Hub（消息路由+推送） │                    │
│                    └───────────────┬─────────────────┘                    │
│                                    │ chat.broadcast()                      │
│                             OpenClaw 前端 UI                               │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Sentinel-A 🦅 — 主控哨兵（Primary）

**文件**: `sentinel_daemon.py` + `personas/Sentinel-A.md`
**模型**: DeepSeek-R1/V3 via Volcengine ARK
**运行模式**: 常驻后台守护进程（`schedule` 调度）

### 职责
| 任务 | 触发时机 | 输出 |
|------|----------|------|
| 每日复盘战报 | 工作日 16:30 | Markdown 报告 → Redis `OPENCLAW_ALERTS` |
| 实盘预警 | 按需调用 | 弹窗推送至 OpenClaw 前端 |
| 心跳信号 | 每 5 分钟 | Redis `sentinel:heartbeat` 键更新 |

### 心跳键
- `sentinel:heartbeat` — TTL=360s（`HEARTBEAT_TTL` 环境变量，默认 360），每 5 分钟更新

---

## 3. Analyst-B 📊 — 量化分析师（Specialist）

**文件**: `analyst_daemon.py` + `personas/Analyst-B.md`
**模型**: DeepSeek via ARK
**运行模式**: 常驻后台守护进程（盘中定时触发）

### 职责
| 任务 | 触发时机 | 输出 |
|------|----------|------|
| 板块轮动分析 | 09:30 / 10:30 / 11:00 / 13:30 / 14:30 | 板块快报 → Redis |
| 主线题材识别 | 同上 | 强弱板块对比 + 资金轮动方向 |

### 数据来源
- `akshare.stock_board_concept_name_em()` — 概念板块涨幅排名
- `akshare.stock_board_industry_name_em()` — 行业板块排名
- `akshare.stock_market_fund_flow()` — 主力资金净流入

### 心跳键
- `analyst:heartbeat` — TTL=360s（`HEARTBEAT_TTL` 环境变量，默认 360），每 5 分钟更新

---

## 4. Guardian-C 🛡️ — 风控守卫（Risk Manager）

**文件**: `guardian_daemon.py` + `personas/Guardian-C.md`
**模型**: DeepSeek via ARK（仅当风险超阈值时调用）
**运行模式**: 常驻后台守护进程（每 5 分钟扫描，盘中激活）

### 风险计算公式
```python
# 大盘跌幅风险（0~5分）
index_risk = min(max(-index_change, 0) / 5.0, 1.0) * 5.0

# 情绪风险（连板健康度越低风险越高，0~5分）
emotion_risk = (1.0 - min(board_health / 100.0, 1.0)) * 5.0

# 综合风险系数（0~10）
risk_score = index_risk + emotion_risk
```

### 触发条件与响应
| 触发条件 | 动作 | 消息级别 |
|----------|------|----------|
| 风险系数 ≥ 7.0 | 发出风险预警 | `critical` |
| 大盘跌幅 ≤ -2% | 系统性风险扫描 | `critical` |
| 连板健康度 < 40% | 情绪退潮预警 | `warning` |

### Redis 状态写入
```
guardian:status → Hash 字段: risk_score, index_change, board_health,
                              zt_count, max_board, updated_at
guardian:heartbeat → TTL=360s（`HEARTBEAT_TTL` 环境变量，默认 360）
```

> 其他 Agent（Scout-D）可读取 `guardian:status` 获取实时风险系数。

---

## 5. Scout-D 🔭 — 游骑侦察（Field Scout）

**文件**: `scout_daemon.py` + `personas/Scout-D.md`
**模型**: DeepSeek via ARK
**运行模式**: 常驻后台守护进程（三段定时触发）

### 侦察时段
| 时段 | 触发时间 | 任务 |
|------|----------|------|
| 盘前侦察 | 09:15 | 扫描竞价异动，筛选高封板资金标的 |
| 上午收盘 | 11:30 | 上午资金流向复盘，更新强弱名单 |
| 尾盘侦察 | 14:30 | 扫描尾盘异动、封板质量评估 |

### 标的筛选条件
```python
封板资金 > 1 亿元（MIN_SEAL_FUND_W = 10_000 万元）
炸板次数 ≤ 1 次（MAX_BOMB_COUNT = 1）
```

### Guardian-C 联动
- 自动读取 `guardian:status.risk_score`
- 风险系数 ≥ 7.0 时，报告自动标注"当前不宜追板"
- 消息 level 自动升级为 `warning`

### 心跳键
- `scout:heartbeat` — TTL=360s（`HEARTBEAT_TTL` 环境变量，默认 360），每 5 分钟更新

---

## 6. sentinel-alert Skill v2.0 — 多智能体消息总线

**文件**: `skills/sentinel-alert/index.js`
**版本**: `v2.0.0`
**功能**: 将所有 AI 员工的消息路由至 OpenClaw 中各 Agent 绑定的专属频道

### Agent 路由表
| source 字段 | 显示图标 | 显示标签 | OpenClaw agentId | 频道 ID 环境变量 |
|------------|---------|---------|-----------------|----------------|
| `Sentinel-A` | 🦅 | Sentinel-A 主控哨兵 | `sentinel-a` | `SENTINEL_A_CHANNEL_ID` |
| `Analyst-B`  | 📊 | Analyst-B 量化分析师 | `analyst-b` | `ANALYST_B_CHANNEL_ID` |
| `Guardian-C` | 🛡️ | Guardian-C 风控守卫 | `guardian-c` | `GUARDIAN_C_CHANNEL_ID` |
| `Scout-D`    | 🔭 | Scout-D 游骑侦察 | `scout-d` | `SCOUT_D_CHANNEL_ID` |

### 消息路由优先级
```
1. OPENCLAW_CHANNEL_ID（全局覆盖）
2. 各 Agent 专属频道 ID（SENTINEL_A_CHANNEL_ID 等）
3. agentId 路由（OpenClaw 内部将消息归属到对应 Agent 的会话）
4. 全局广播（兜底）
```

### 消息格式化
```
> {icon} **{label}** · {levelPrefix} · `{type}` — {HH:MM:SS}

{content}
```

### 消息级别前缀
| level | 前缀 |
|-------|------|
| `critical` | 🚨🚨🚨 **紧急预警** |
| `warning`  | ⚠️ **风险提示** |
| `info`     | 📡 **实时播报** |

### 心跳监控
每分钟检查所有四个 Agent 的心跳键，任何 Agent 停止心跳立即输出告警日志。

---

## 7. 智能体通信协议

### 7.1 消息格式规范
```json
{
  "type":      "每日战报 | 板块轮动 | 极高风险预警 | 侦察快报(盘前) | 系统通知",
  "content":   "Markdown 格式内容",
  "timestamp": "2026-03-16T16:30:00+08:00",
  "source":    "Sentinel-A | Analyst-B | Guardian-C | Scout-D",
  "level":     "info | warning | critical"
}
```

### 7.2 Agent 间数据共享（Redis Hash）
```
guardian:status → {risk_score, index_change, board_health, zt_count, max_board, updated_at}
                  ↑ Guardian-C 写入   Scout-D 读取
```

### 7.3 优先级定义
| 级别 | 类型 | OpenClaw 表现 |
|------|------|---------------|
| `critical` | 熔断预警、系统性风险、大盘暴跌 | 🚨 红色强制弹窗 |
| `warning`  | 止损触发、情绪退潮、高风险追板 | ⚠️ 橙色通知 |
| `info`     | 每日战报、板块分析、侦察快报   | 📡 蓝色消息注入 |

---

## 8. 扩展 Agent 注册规范

如需添加新 AI 员工，需完成以下步骤：

### Step 1: 创建 Persona 文件
```bash
vim /home/runner/work/aigp/aigp/personas/MyAgent-E.md
```

### Step 2: 创建守护进程
```python
# my_agent_daemon.py
# 参考 analyst_daemon.py 结构：
# 1. 定义 AGENT_NAME = 'MyAgent-E'
# 2. 实现核心任务函数（发布到 OPENCLAW_ALERTS 并携带 source 字段）
# 3. 实现心跳函数（写入 Redis myagent:heartbeat）
# 4. 配置 schedule 调度
```

### Step 3: 更新 config.py
```python
# 在 AGENTS 列表中追加新 Agent 定义
{
    'id':          'myagent-e',
    'name':        'MyAgent-E 🌟',
    'description': '描述...',
    'persona':     f'{PERSONA_DIR}/MyAgent-E.md',
    'icon':        '🌟',
    'schedule':    '触发时机',
    'channel':     'OPENCLAW_ALERTS',
},
```

### Step 4: 更新 Skill 路由表
```javascript
// skills/sentinel-alert/index.js 的 AGENT_META 对象中追加
'MyAgent-E': { icon: '🌟', label: 'MyAgent-E 描述' },
```

### Step 5: 更新 start_hf.sh
```bash
# 在步骤 7 之后追加
log "启动 MyAgent-E 🌟 ..."
python3 /app/my_agent_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[MyAgent-E  🌟] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)
```

### Step 6: 更新 Dockerfile
```dockerfile
COPY my_agent_daemon.py /app/my_agent_daemon.py
COPY personas/MyAgent-E.md /root/.openclaw/personas/MyAgent-E.md
```
