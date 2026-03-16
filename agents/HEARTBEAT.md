# HEARTBEAT — 系统心跳与健康监控规范

> Sentinel-A 的心跳机制是整个系统可靠性的基石。
> 只要心跳在跳，哨兵就在岗。心跳停止，系统立即预警。

---

## 1. 心跳架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                    心跳信号流转链路                               │
│                                                                  │
│  sentinel_daemon.py                                              │
│      │  每 5 分钟                                                │
│      ▼                                                           │
│  Redis SETEX sentinel:heartbeat 60 <timestamp>                   │
│      │  TTL = 60 秒（1分钟内无更新则自动过期）                   │
│      ▼                                                           │
│  skills/sentinel-alert/index.js                                  │
│      │  每 60 秒检查 GET sentinel:heartbeat                      │
│      │  ├─ key 存在 → 守护进程正常                              │
│      │  └─ key 不存在 → 发出"守护进程心跳超时"警告              │
│      ▼                                                           │
│  OpenClaw 前端  ← chat.broadcast() 推送警告弹窗                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 心跳 Redis 键规范

| 键名 | 类型 | TTL | 值格式 | 更新频率 |
|------|------|-----|--------|----------|
| `sentinel:heartbeat` | String | 60s | ISO 8601 时间戳 | 每 5 分钟 |
| `sentinel:status` | Hash | 永久 | 见下方 | 每次状态变更 |
| `sentinel:last_report` | String | 永久 | YYYYMMDD | 每日复盘完成后 |
| `sentinel:report_count` | Counter | 永久 | 整数 | 每次复盘+1 |

### `sentinel:status` Hash 字段
```
field: started_at     → 守护进程启动时间 (ISO 8601)
field: version        → Sentinel-A 版本号
field: last_fetch_ok  → 最后一次数据抓取成功时间
field: last_ai_ok     → 最后一次 AI 推理成功时间
field: error_count    → 累计错误次数
field: mode           → online | offline (ARK_API_KEY 是否配置)
```

---

## 3. 心跳节拍时序图

```
时间轴:
T+0m  → Sentinel-A 启动，立即写入首次心跳
T+5m  → 第一次定时心跳
T+10m → 第二次定时心跳
...
T+16:30 → 触发每日复盘战报生成
T+16:31 → 战报发布至 OPENCLAW_ALERTS，更新 sentinel:last_report

Skill 监控侧:
T+0m  → Skill 启动，连接 Redis
T+1m  → 第一次心跳检查
T+2m  → 第二次心跳检查（若 T+5m 前守护进程未发心跳 → 超时警告）
...
```

---

## 4. 健康状态定义

| 状态码 | 名称 | 判断条件 | 处置建议 |
|--------|------|----------|----------|
| `HEALTHY` | 完全健康 | 心跳正常 + AI 可用 + 最近 24h 有复盘 | 无需处理 |
| `DEGRADED` | 降级运行 | 心跳正常 + AI 不可用（无 ARK_API_KEY） | 检查环境变量 |
| `STALE` | 数据陈旧 | 最近一次复盘超过 2 个交易日前 | 检查 REPORT_TIME 设置 |
| `SILENT` | 守护静默 | `sentinel:heartbeat` 键不存在 | 立即检查 sentinel_daemon 进程 |
| `DEAD` | 系统宕机 | Docker 健康检查失败（curl 7860 超时） | 重启容器 |

---

## 5. 心跳实现代码参考

### Python 侧（sentinel_daemon.py 内）
```python
def heartbeat() -> None:
    """向 Redis 写入心跳，TTL=60s，守护进程每 5 分钟更新一次。"""
    try:
        redis_client.setex(
            'sentinel:heartbeat',
            60,                                        # TTL（秒）
            datetime.datetime.now().isoformat()        # 当前时间戳
        )
        # 同步更新状态哈希
        redis_client.hset('sentinel:status', mapping={
            'last_heartbeat': datetime.datetime.now().isoformat(),
        })
    except Exception as exc:
        log.warning('心跳写入失败: %s', exc)

# 触发规则
schedule.every(5).minutes.do(heartbeat)
heartbeat()  # 启动时立即执行首次心跳
```

### JavaScript 侧（sentinel-alert/index.js 内）
```javascript
// 每分钟检查守护进程心跳
setInterval(async () => {
    try {
        if (!subscriber || !subscriber.isOpen) return;
        const ts = await subscriber.get('sentinel:heartbeat');
        if (!ts) {
            console.warn('[Sentinel Skill] 守护进程心跳超时！Sentinel-A 可能已停止运行。');
            // 可在此处触发前端告警
        }
    } catch (_) { /* 静默忽略心跳检查失败 */ }
}, 60_000);
```

---

## 6. 手动心跳诊断命令

```bash
# 检查心跳是否存活
docker exec sentinel redis-cli GET sentinel:heartbeat
# 有输出 → 正常；无输出 → 心跳过期，守护进程可能已崩溃

# 查看守护进程状态详情
docker exec sentinel redis-cli HGETALL sentinel:status

# 查看最后一次复盘日期
docker exec sentinel redis-cli GET sentinel:last_report

# 查看 Redis 所有 Sentinel 相关键
docker exec sentinel redis-cli KEYS "sentinel:*"

# 手动注入一条测试心跳（调试用）
docker exec sentinel redis-cli SETEX sentinel:heartbeat 60 "manual-test"

# 实时监听总线（验证消息推送是否正常）
docker exec -it sentinel redis-cli SUBSCRIBE OPENCLAW_ALERTS
```

---

## 7. 告警升级策略

```
Level 1 — INFO（蓝色）:
  触发条件: 心跳正常，复盘生成成功
  处置: 在 OpenClaw 聊天窗口显示战报

Level 2 — WARNING（橙色）:
  触发条件: AI 推理失败（OpenAIError），回退到离线模式报告
  处置: OpenClaw 弹窗提示，附带离线版战报

Level 3 — CRITICAL（红色）:
  触发条件: sentinel:heartbeat 键消失（守护进程宕机）
  处置: OpenClaw 强制弹窗告警 + 建议用户重启容器

Level 4 — SYSTEM（灰色）:
  触发条件: Docker HEALTHCHECK 失败（Nginx/端口不通）
  处置: 容器自动重启（--restart always）
```

---

## 8. SLA 目标（参考指标）

| 指标 | 目标值 |
|------|--------|
| 心跳可用性 | ≥ 99.5%（每月允许停跳 ≤ 3.6 小时） |
| 复盘战报准时率 | ≥ 95%（工作日 16:30 ± 5 分钟内发布） |
| AI 推理成功率 | ≥ 90%（余下 10% 回退离线模式） |
| 端口 7860 可用性 | ≥ 99.9%（Docker HEALTHCHECK 保障） |
