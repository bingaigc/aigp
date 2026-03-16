# =============================================================================
# Sentinel 终极量化交易系统 — 一键化极客部署 Dockerfile
# 集成: OpenClaw 前端 + DeepSeek 大脑 + Redis 状态机
#       + A 股实盘监控 + ttyd Web 终端 + Nginx 路由
# 平台: linux/amd64  端口: 7860 (统一入口)
# =============================================================================

# ── 0. 构建参数 ───────────────────────────────────────────────────────────────
ARG TTYD_VERSION=1.7.7

# ── 1. 基础镜像：OpenClaw + Playwright 浏览器环境 ─────────────────────────────
FROM --platform=linux/amd64 ghcr.io/openclaw/openclaw:main

USER root
WORKDIR /app

# Playwright 浏览器缓存路径（与 OpenClaw 基础镜像一致）
ENV PLAYWRIGHT_BROWSERS_PATH=/app/pw-browsers \
    PYTHONUNBUFFERED=1 \
    NPM_CONFIG_LOGLEVEL=warn

# ── 2. 系统依赖（单层合并，减少镜像层数） ────────────────────────────────────
# 包含：基础工具 + Playwright 浏览器运行时库 + 服务组件
RUN apt-get update && apt-get install -y --no-install-recommends \
        # 基础工具
        curl ca-certificates gnupg wget jq git build-essential \
        # Python 3
        python3 python3-pip python3-dev \
        # Node.js / npm（基础镜像通常已含，此处保底）
        nodejs npm \
        # 服务组件
        nginx redis-server \
        # Playwright Chromium 运行时依赖
        libnss3 libnspr4 \
        libatk1.0-0 libatk-bridge2.0-0 \
        libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpangocairo-1.0-0 libgtk-3-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── 3. ttyd Web 终端 ──────────────────────────────────────────────────────────
ARG TTYD_VERSION
RUN curl -fsSL \
        "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
        -o /usr/local/bin/ttyd \
    && chmod +x /usr/local/bin/ttyd

# ── 4. Python 量化依赖（A 股全家桶） ──────────────────────────────────────────
# 固定主要依赖版本，提升可重现性
RUN pip3 install --no-cache-dir --break-system-packages \
        "akshare>=1.12" \
        "pandas>=2.0" \
        "numpy>=1.26" \
        "openai>=1.12" \
        "redis>=5.0" \
        "schedule>=1.2" \
        "backtrader>=1.9" \
        "matplotlib>=3.8" \
        "huggingface_hub>=0.20"

# ── 5. npm 全局依赖 ───────────────────────────────────────────────────────────
RUN npm install -g npm@latest --quiet \
    && npm install -g playwright --omit=dev --quiet \
    && npm cache clean --force

# ── 6. OpenClaw 目录 ──────────────────────────────────────────────────────────
RUN mkdir -p /root/.openclaw/skills /root/.openclaw/agents /root/.openclaw/personas \
    && chmod -R 755 /root/.openclaw

# ── 7. Nginx 反向代理配置 ─────────────────────────────────────────────────────
COPY nginx.conf /etc/nginx/sites-available/default

# ── 8. OpenClaw 配置初始化脚本 ────────────────────────────────────────────────
COPY config.py   /app/config.py

# ── 8b. HF Hub 数据恢复 / 备份脚本 ───────────────────────────────────────────
COPY restore.py  /app/restore.py
COPY backup.py   /app/backup.py
RUN chmod +x /app/restore.py /app/backup.py

# ── 9. Sentinel-A 人格定义 ────────────────────────────────────────────────────
COPY personas/Sentinel-A.md /root/.openclaw/personas/Sentinel-A.md

# ── 9b. OpenClaw Agent 配置文档（团队定义 / 引导 / 心跳 / 工具 / 用户手册）────
COPY agents/ /root/.openclaw/agents/

# ── 9c. 其余 AI 员工 Persona 文件 ────────────────────────────────────────────
COPY personas/Analyst-B.md  /root/.openclaw/personas/Analyst-B.md
COPY personas/Guardian-C.md /root/.openclaw/personas/Guardian-C.md
COPY personas/Scout-D.md    /root/.openclaw/personas/Scout-D.md

# ── 10. Sentinel-A 量化引擎守护进程 ──────────────────────────────────────────
COPY sentinel_daemon.py /app/sentinel_daemon.py

# ── 10b. 其余三个 AI 员工守护进程 ────────────────────────────────────────────
COPY analyst_daemon.py  /app/analyst_daemon.py
COPY guardian_daemon.py /app/guardian_daemon.py
COPY scout_daemon.py    /app/scout_daemon.py

# ── 11. 离线回测引擎 ──────────────────────────────────────────────────────────
COPY backtest_engine.py /app/backtest_engine.py

# ── 12. OpenClaw Skill（Redis 监听 → 前端主动推送） ──────────────────────────
COPY skills/sentinel-alert/ /root/.openclaw/skills/sentinel-alert/
RUN npm install --prefix /root/.openclaw/skills/sentinel-alert \
        --omit=dev --silent \
    && npm cache clean --force

# ── 13. 启动脚本 ──────────────────────────────────────────────────────────────
COPY start_hf.sh /app/start_hf.sh
RUN chmod +x /app/start_hf.sh

# ── 健康检查 ──────────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -sf http://localhost:7860 > /dev/null || exit 1

# ── 暴露统一入口 ──────────────────────────────────────────────────────────────
EXPOSE 7860

ENTRYPOINT ["/bin/bash", "/app/start_hf.sh"]
