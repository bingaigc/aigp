# BOOTSTRAP — 系统引导与一键启动手册

> 本文档描述 Sentinel 终极量化交易系统从零到完全运行的完整引导流程。
> 无论你是在本地调试还是在云服务器冷启动，按此手册操作即可点火整个宇宙。

---

## 1. 快速启动（生产环境 · 30 秒点火）

```bash
docker run -d \
  --name sentinel \
  --restart always \
  -p 7860:7860 \
  -e ARK_API_KEY="你的_Volcengine_ARK_API_Key" \
  -e ARK_MODEL_ID="ep-20260312230909-pskjv" \
  -e GATEWAY_TOKEN="你的自定义访问密码" \
  -e REPORT_TIME="16:30" \
  ghcr.io/bingaigc/aigp:latest
```

浏览器访问 `http://服务器IP:7860` 即可看到 OpenClaw 界面。

---

## 2. 系统启动顺序（`start_hf.sh` 内部流程）

```
阶段 0: 环境准备
  └─ 检查 /app/config.py、/app/sentinel_daemon.py 是否存在

阶段 1: Redis 总线启动
  └─ service redis-server start
  └─ 轮询 redis-cli ping（最多 10 次 × 1s）
  └─ ✓ Redis 就绪 → 继续

阶段 2: OpenClaw 配置初始化
  └─ python3 /app/config.py
     ├─ 读取 ARK_API_KEY、GATEWAY_TOKEN 环境变量
     ├─ 写入 /root/.openclaw/openclaw.json
     └─ 配置 Volcengine DeepSeek 模型端点

阶段 3: OpenClaw 自检
  └─ node /app/dist/entry.js doctor --fix

阶段 4: Skill 依赖安全检查
  └─ 检查 node_modules 是否存在（构建时已安装，此处兜底）

阶段 5: 并行启动所有服务
  ├─ Sentinel-A 量化守护进程  → python3 /app/sentinel_daemon.py &
  ├─ OpenClaw 网关            → node /app/dist/entry.js gateway & (PORT=7861)
  └─ ttyd Web 终端            → ttyd -b /term -p 7862 -W bash &

阶段 6: Nginx 反向代理（前台运行 · PID 1 监控点）
  └─ nginx -g 'daemon off;'
     ├─ port 7860 → / → OpenClaw (7861)
     └─ port 7860 → /term/ → ttyd (7862)
```

---

## 3. 环境变量完整参考

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| `ARK_API_KEY` | ⭐️ 推荐 | — | Volcengine ARK API 密钥；不填则 AI 分析功能关闭，系统仍可运行 |
| `ARK_MODEL_ID` | 否 | `ep-20260312230909-pskjv` | DeepSeek 模型 Endpoint ID |
| `ARK_BASE_URL` | 否 | `https://ark.cn-beijing.volces.com/api/v3` | ARK API 地址 |
| `GATEWAY_TOKEN` | 否 | `OpenClaw_Secure_2026!` | OpenClaw 前端访问 Token（建议修改） |
| `REDIS_URL` | 否 | `redis://127.0.0.1:6379` | Redis 连接地址 |
| `REPORT_TIME` | 否 | `16:30` | 每日复盘战报触发时间（24h 制，如 `15:00`） |

---

## 4. 端口映射一览

| 外部端口 | 内部端口 | 服务 | 路径 |
|----------|----------|------|------|
| `7860` | `7860` | Nginx 统一入口 | `/` → OpenClaw, `/term/` → ttyd |
| — | `7861` | OpenClaw 网关 | 内部，无需暴露 |
| — | `7862` | ttyd Web 终端 | 内部，无需暴露 |
| — | `6379` | Redis | 内部，无需暴露 |

---

## 5. 本地开发环境引导

### 5.1 依赖安装（Python）
```bash
pip install akshare pandas numpy openai redis schedule backtrader matplotlib huggingface_hub
```

### 5.2 依赖安装（Node.js Skill）
```bash
cd skills/sentinel-alert && npm install
```

### 5.3 本地运行守护进程
```bash
# 启动 Redis（本地需先安装）
redis-server &

# 初始化 OpenClaw 配置（可选）
export ARK_API_KEY="..."
python3 config.py

# 启动 Sentinel-A 守护进程
python3 sentinel_daemon.py
```

### 5.4 运行离线回测
```bash
export ARK_API_KEY="..."
python3 backtest_engine.py --symbol 600519 --start 20230101 --end 20240101
# → 输出 backtest_result.png 和 backtest_result_trades.json
```

---

## 6. 构建自定义镜像

```bash
git clone https://github.com/bingaigc/aigp.git
cd aigp

# 使用默认 ttyd 版本
docker build -t my-sentinel .

# 指定 ttyd 版本
docker build --build-arg TTYD_VERSION=1.7.7 -t my-sentinel .

# 多平台构建（推送至 GHCR）
docker buildx build \
  --platform linux/amd64 \
  --push \
  -t ghcr.io/bingaigc/aigp:latest \
  .
```

---

## 7. 健康检查与故障排查

### 7.1 Docker 健康状态
```bash
docker inspect --format='{{.State.Health.Status}}' sentinel
# healthy | unhealthy | starting
```

### 7.2 查看实时日志
```bash
docker logs -f sentinel --tail 100
# 日志前缀:
# [Sentinel-A] HH:MM:SS ... → Python 守护进程
# [OpenClaw]   HH:MM:SS ... → Node.js 网关
# [ttyd]       HH:MM:SS ... → Web 终端
```

### 7.3 Redis 手动诊断
```bash
docker exec -it sentinel redis-cli
> PING                           # 返回 PONG 表示正常
> GET sentinel:heartbeat         # 查看最后心跳时间
> SUBSCRIBE OPENCLAW_ALERTS      # 监听消息总线
```

### 7.4 强制触发复盘（测试用）
```bash
docker exec -it sentinel python3 -c "
import sys
sys.path.insert(0, '/app')
from sentinel_daemon import generate_daily_report
generate_daily_report()
"
```

---

## 8. 升级流程

```bash
# 停止旧容器（数据无状态，直接替换）
docker stop sentinel && docker rm sentinel

# 拉取最新镜像
docker pull ghcr.io/bingaigc/aigp:latest

# 重新启动（参数与原来相同）
docker run -d --name sentinel ...（同第 1 节命令）
```
