#!/usr/bin/env bash
# =============================================================================
# start_hf.sh — Sentinel 终极交易系统一键点火脚本
# 启动顺序: Redis → OpenClaw → ttyd → Nginx
# =============================================================================
set -euo pipefail

# ── 颜色与日志 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date +%H:%M:%S)] $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN: $*${NC}"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR: $*${NC}"; }

# ── PID 追踪（优雅退出） ───────────────────────────────────────────────────────
PIDS=()
cleanup() {
    log "收到退出信号，正在关闭所有子进程..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    service redis-server stop 2>/dev/null || true
    log "系统已安全关闭。"
}
trap cleanup SIGTERM SIGINT

# ── 1. 启动 Redis ─────────────────────────────────────────────────────────────
log "--- 启动底层总线 Redis ---"
service redis-server start

# 等待 Redis 就绪（最多 10 秒）
for i in $(seq 1 10); do
    redis-cli ping &>/dev/null && break
    warn "Redis 未就绪，等待 ($i/10)..."
    sleep 1
done
redis-cli ping &>/dev/null || { err "Redis 启动失败，退出。"; exit 1; }
log "Redis 已就绪 ✓"

# ── 2. 初始化 OpenClaw 配置 ───────────────────────────────────────────────────
log "--- 初始化 OpenClaw 环境 ---"
python3 /app/config.py || warn "config.py 执行异常（继续启动）"
node /app/dist/entry.js doctor --fix >/dev/null 2>&1 || true

# ── 3. 安装 Skill 依赖（构建时已完成，此处仅作安全兜底） ─────────────────────
SKILL_DIR="/root/.openclaw/skills/sentinel-alert"
if [ -f "$SKILL_DIR/package.json" ] && [ ! -d "$SKILL_DIR/node_modules" ]; then
    log "node_modules 缺失，重新安装 sentinel-alert 依赖..."
    npm install --prefix "$SKILL_DIR" --silent --omit=dev
fi

echo ""
echo "======================================================="
echo -e "🚀 ${GREEN}终极交易系统点火：OpenClaw + Sentinel-A + Web 终端${NC}"
echo "======================================================="
echo ""

# ── 4. 启动 Sentinel-A 守护进程 ───────────────────────────────────────────────
log "启动 Sentinel-A 量化守护进程..."
python3 /app/sentinel_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[Sentinel-A] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 5. 启动 OpenClaw 网关 ─────────────────────────────────────────────────────
log "启动 OpenClaw 网关 (port 7861)..."
PORT=7861 node /app/dist/entry.js gateway 2>&1 | \
    while IFS= read -r line; do
        echo "[OpenClaw]   $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# 等待 OpenClaw 网关就绪
for i in $(seq 1 15); do
    curl -sf http://127.0.0.1:7861 >/dev/null 2>&1 && break
    sleep 1
done
log "OpenClaw 网关已就绪 ✓"

# ── 6. 启动 ttyd Web 终端 ─────────────────────────────────────────────────────
log "启动 ttyd Web 终端 (port 7862)..."
ttyd -b /term -p 7862 -W \
    --writable \
    --client-option enableSixel=false \
    bash 2>&1 | \
    while IFS= read -r line; do
        echo "[ttyd]       $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 7. 启动 Nginx 路由（前台，作为 PID 1 子进程监控点）────────────────────────
log "启动 Nginx 反向代理 (port 7860)..."
exec nginx -g 'daemon off; error_log /dev/stderr error;'
