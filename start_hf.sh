#!/usr/bin/env bash
# =============================================================================
# start_hf.sh — Sentinel 终极交易系统一键点火脚本
# 启动顺序: HF 恢复 → Redis → OpenClaw → 四大 AI 员工守护进程 → ttyd → Nginx
# AI 员工: Sentinel-A 🦅 | Analyst-B 📊 | Guardian-C 🛡️ | Scout-D 🔭
# 持久化:  启动时从 HF Hub 恢复数据；关闭时 + 定时备份到 HF Hub
# =============================================================================
set -euo pipefail

# ── 颜色与日志 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[$(date +%H:%M:%S)] $*${NC}"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN: $*${NC}"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR: $*${NC}"; }

# ── 备份间隔（分钟，可通过环境变量覆盖；默认 60 分钟，降低 HF Hub API 调用频率） ─
BACKUP_INTERVAL_MIN="${BACKUP_INTERVAL_MIN:-60}"

# ── 2核16G 低内存调优：限制 glibc 内存竞技场，减少碎片 ─────────────────────────
export MALLOC_ARENA_MAX=2
# 限制 Node.js 老生代堆上限（默认 ~1.4 GB），为 Python 守护进程留出空间
# 允许外部环境变量 NODE_OPTIONS 覆盖（如需调大堆，设置 NODE_OPTIONS=--max-old-space-size=768）
if [ -z "${NODE_OPTIONS:-}" ]; then
    export NODE_OPTIONS="--max-old-space-size=512"
fi

# ── PID 追踪（优雅退出） ───────────────────────────────────────────────────────
PIDS=()
cleanup() {
    log "收到退出信号，正在关闭所有子进程..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    # ── 关闭前备份数据到 HF Hub ───────────────────────────────────────────────
    log "关闭前触发数据备份..."
    backup_output=$(python3 /app/backup.py 2>&1) || \
        warn "关闭备份失败（已忽略）: ${backup_output}"
    [ -n "${backup_output:-}" ] && echo "${backup_output}"
    service redis-server stop 2>/dev/null || true
    log "系统已安全关闭。"
}
trap cleanup SIGTERM SIGINT

# ── 0. 从 HF Hub 恢复数据（首次部署或重启时恢复历史配置） ─────────────────────
log "--- 恢复 HF Hub 数据 ---"
python3 /app/restore.py || warn "数据恢复失败（跳过，继续启动）"

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

# ── 2核16G 低内存调优：限制 Redis 最大内存，防止无上限增长 ──────────────────────
# allkeys-lru: 内存满时自动淘汰最近最少使用的 key，确保系统稳定
redis-cli CONFIG SET maxmemory 1gb           >/dev/null
redis-cli CONFIG SET maxmemory-policy allkeys-lru >/dev/null
log "Redis 内存上限已设为 1 GB (allkeys-lru) ✓"

# ── 2. 初始化 OpenClaw 配置（含多智能体注册） ─────────────────────────────────
log "--- 初始化 OpenClaw 环境（注册 4 个 AI 员工）---"
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
echo -e "🚀 ${GREEN}终极交易系统点火：OpenClaw + 四大 AI 员工 + Web 终端${NC}"
echo "======================================================="
echo -e "  🦅 Sentinel-A  — 主控哨兵（每日复盘 + 实盘预警）"
echo -e "  📊 Analyst-B   — 量化分析师（板块轮动）"
echo -e "  🛡️  Guardian-C  — 风控守卫（实时风险监控）"
echo -e "  🔭 Scout-D     — 游骑侦察（盘前/盘中/尾盘三段狙击）"
echo -e "  💾 HF Backup   — 每 ${BACKUP_INTERVAL_MIN} 分钟自动备份至 HF Hub"
echo "======================================================="
echo ""

# ── 4. 银河战舰模型中转网关（可选，MODEL_GATEWAY_URL 指向本机时自动启动） ────────
MODEL_GATEWAY_PORT="${MODEL_GATEWAY_PORT:-8090}"
# 判断是否需要本地网关：MODEL_GATEWAY_URL 精确匹配 127.0.0.1:PORT 或 localhost:PORT
_gw_url="${MODEL_GATEWAY_URL:-}"
if [[ "$_gw_url" =~ ^https?://(127\.0\.0\.1|localhost):${MODEL_GATEWAY_PORT}(/|$) ]]; then
    log "启动 银河战舰模型中转网关 🌌 (port ${MODEL_GATEWAY_PORT})..."
    python3 /app/gateway/model_router.py 2>&1 | \
        while IFS= read -r line; do
            echo "[ModelGateway🌌] $(date +%H:%M:%S) $line"
        done &
    PIDS+=($!)
    # 等待网关就绪（最多30秒，兼顾冷启动较慢的环境）
    for i in $(seq 1 30); do
        curl -sf "http://127.0.0.1:${MODEL_GATEWAY_PORT}/health" >/dev/null 2>&1 && break
        sleep 1
    done
    curl -sf "http://127.0.0.1:${MODEL_GATEWAY_PORT}/health" >/dev/null 2>&1 \
        && log "银河战舰网关已就绪 ✓ (port ${MODEL_GATEWAY_PORT})" \
        || warn "银河战舰网关可能未就绪，继续启动（非致命）"
fi

# ── 5. 启动 Sentinel-A 守护进程（主控哨兵） ───────────────────────────────────
log "启动 Sentinel-A 🦅 主控哨兵..."
python3 /app/sentinel_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[Sentinel-A 🦅] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 6. 启动 Analyst-B 守护进程（量化分析师） ─────────────────────────────────
log "启动 Analyst-B 📊 量化分析师..."
python3 /app/analyst_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[Analyst-B  📊] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 7. 启动 Guardian-C 守护进程（风控守卫） ──────────────────────────────────
log "启动 Guardian-C 🛡️  风控守卫..."
python3 /app/guardian_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[Guardian-C 🛡️] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 8. 启动 Scout-D 守护进程（游骑侦察） ─────────────────────────────────────
log "启动 Scout-D 🔭 游骑侦察..."
python3 /app/scout_daemon.py 2>&1 | \
    while IFS= read -r line; do
        echo "[Scout-D    🔭] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 9. 启动 OpenClaw 网关 ─────────────────────────────────────────────────────
log "启动 OpenClaw 网关 (port 7861)..."
PORT=7861 node /app/dist/entry.js gateway 2>&1 | \
    while IFS= read -r line; do
        echo "[OpenClaw   🌐] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# 等待 OpenClaw 网关就绪
for i in $(seq 1 15); do
    curl -sf http://127.0.0.1:7861 >/dev/null 2>&1 && break
    sleep 1
done
log "OpenClaw 网关已就绪 ✓"

# ── 10. 启动 ttyd Web 终端 ─────────────────────────────────────────────────────
log "启动 ttyd Web 终端 (port 7862)..."
ttyd -b /term -p 7862 -W \
    --writable \
    --client-option enableSixel=false \
    bash 2>&1 | \
    while IFS= read -r line; do
        echo "[ttyd       💻] $(date +%H:%M:%S) $line"
    done &
PIDS+=($!)

# ── 11. 启动定时备份循环（后台） ─────────────────────────────────────────────
log "启动定时备份 💾 (间隔 ${BACKUP_INTERVAL_MIN} 分钟)..."
(
    BACKUP_SLEEP=$(( BACKUP_INTERVAL_MIN * 60 ))
    while true; do
        sleep "${BACKUP_SLEEP}"
        python3 /app/backup.py 2>&1 | \
            while IFS= read -r line; do
                echo "[HF Backup  💾] $(date +%H:%M:%S) $line"
            done
    done
) &
PIDS+=($!)

# ── 12. 启动 Nginx 路由（前台，作为 PID 1 子进程监控点）──────────────────────
log "启动 Nginx 反向代理 (port 7860)..."
exec nginx -g 'daemon off; error_log /dev/stderr error;'
