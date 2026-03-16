'use strict';
/**
 * sentinel-alert — OpenClaw Multi-Agent Hub Skill
 *
 * 订阅 Redis OPENCLAW_ALERTS 频道，将来自所有 AI 员工的消息
 * 路由到 OpenClaw 中各 Agent 绑定的专属频道（或全局广播）。
 *
 * ═══════════════════════════════════════════════════════════════════
 * 【需要配置的环境变量】
 *
 * ── 必须配置（消息总线） ──────────────────────────────────────────
 * REDIS_URL             Redis 连接地址（默认 redis://127.0.0.1:6379）
 *
 * ── 可选：OpenClaw 频道绑定 ───────────────────────────────────────
 * OPENCLAW_CHANNEL_ID   全局覆盖：所有 Agent 消息统一推送到此频道 ID。
 *                       不设则按下方各 Agent 专属频道路由。
 *
 * SENTINEL_A_CHANNEL_ID Sentinel-A 🦅 专属 OpenClaw 频道 ID
 * ANALYST_B_CHANNEL_ID  Analyst-B  📊 专属 OpenClaw 频道 ID
 * GUARDIAN_C_CHANNEL_ID Guardian-C 🛡️ 专属 OpenClaw 频道 ID
 * SCOUT_D_CHANNEL_ID    Scout-D    🔭 专属 OpenClaw 频道 ID
 *
 * 如果以上频道 ID 均未配置，Skill 回退到全局广播（broadcast to all
 * sessions），同时仍会附带 agentId 提示 OpenClaw 将消息归属到正确 Agent。
 *
 * 频道 ID 获取方式：
 *   在 OpenClaw 前端 → 打开对应 Agent 的对话窗口 → URL 中 "channelId=xxx"
 *   或通过 OpenClaw 管理 API: GET /api/channels?agentId=sentinel-a
 *
 * ── 其他 ─────────────────────────────────────────────────────────
 * 其余环境变量（ARK_API_KEY、GATEWAY_TOKEN 等）由 Python 守护进程和
 * config.py 读取，Skill 本身无需感知。详见 MEMORY.md 第 2 节。
 * ═══════════════════════════════════════════════════════════════════
 */
const { createClient } = require('redis');

const REDIS_URL              = process.env.REDIS_URL || 'redis://127.0.0.1:6379';
const CHANNEL                = 'OPENCLAW_ALERTS';
const RECONNECT_MS           = 5_000;
const MAX_RECONNECT_DELAY_MS = 60_000;

// 全局频道 ID（设置后覆盖所有 Agent 的专属频道）
const GLOBAL_CHANNEL_ID = process.env.OPENCLAW_CHANNEL_ID || null;

// 各 Agent 的 OpenClaw 专属频道 ID（通过环境变量配置）
const AGENT_CHANNEL_IDS = {
    'Sentinel-A': process.env.SENTINEL_A_CHANNEL_ID || null,
    'Analyst-B':  process.env.ANALYST_B_CHANNEL_ID  || null,
    'Guardian-C': process.env.GUARDIAN_C_CHANNEL_ID || null,
    'Scout-D':    process.env.SCOUT_D_CHANNEL_ID    || null,
};

// Redis source 字段 → OpenClaw agentId 映射
const SOURCE_TO_AGENT_ID = {
    'Sentinel-A': 'sentinel-a',
    'Analyst-B':  'analyst-b',
    'Guardian-C': 'guardian-c',
    'Scout-D':    'scout-d',
};

/** 每个 Agent 的显示配置 */
const AGENT_META = {
    'Sentinel-A': { icon: '🦅', label: 'Sentinel-A 主控哨兵' },
    'Analyst-B':  { icon: '📊', label: 'Analyst-B 量化分析师' },
    'Guardian-C': { icon: '🛡️', label: 'Guardian-C 风控守卫' },
    'Scout-D':    { icon: '🔭', label: 'Scout-D 游骑侦察' },
};

/** 各 Agent 心跳键 */
const HEARTBEAT_KEYS = {
    'Sentinel-A': 'sentinel:heartbeat',
    'Analyst-B':  'analyst:heartbeat',
    'Guardian-C': 'guardian:heartbeat',
    'Scout-D':    'scout:heartbeat',
};

/**
 * 将消息推送到 OpenClaw 中对应 Agent 绑定的专属频道。
 * 路由优先级（高 → 低）：
 *   1. GLOBAL_CHANNEL_ID（OPENCLAW_CHANNEL_ID env var）
 *   2. 各 Agent 专属 channel ID（SENTINEL_A_CHANNEL_ID 等 env vars）
 *   3. agentId 路由（OpenClaw 内部将消息归属到对应 Agent 的会话）
 *   4. 通用广播（broadcast to all sessions，兜底）
 *
 * @param {object} app   - OpenClaw App 实例
 * @param {string} source - 消息来源 Agent 名（如 'Sentinel-A'）
 * @param {string} content - 格式化后的消息内容（Markdown）
 */
function pushToChannel(app, source, content) {
    const agentId   = SOURCE_TO_AGENT_ID[source];
    // 按优先级确定目标频道 ID
    const channelId = GLOBAL_CHANNEL_ID
        || AGENT_CHANNEL_IDS[source]
        || null;

    const payload = { role: 'assistant', content };
    if (channelId) {
        // 推送到指定频道（最精确路由）
        payload.channelId = channelId;
    } else if (agentId) {
        // 附带 agentId，让 OpenClaw 将消息归属到该 Agent 绑定的频道
        payload.agentId = agentId;
    }

    if (app.chat && typeof app.chat.broadcast === 'function') {
        app.chat.broadcast(payload);
        if (channelId) {
            console.log(`[Multi-Agent Hub] 消息已推送到频道 ${channelId} (${source})`);
        } else {
            console.log(`[Multi-Agent Hub] 消息已推送，agentId=${agentId || '广播'} (${source})`);
        }
    } else if (typeof app.emit === 'function') {
        // 备用：session 事件注入
        app.emit('session:message:inject', payload);
        console.log(`[Multi-Agent Hub] 消息已通过 session:inject 推送 (${source})`);
    } else {
        console.warn('[Multi-Agent Hub] 广播接口不可用，消息被丢弃。source=', source);
    }
}

/**
 * @param {import('@openclaw/sdk').App} app
 */
module.exports = async function registerSkill(app) {
    const channelSummary = GLOBAL_CHANNEL_ID
        ? `全局频道 ${GLOBAL_CHANNEL_ID}`
        : Object.entries(AGENT_CHANNEL_IDS)
            .filter(([, v]) => v)
            .map(([k, v]) => `${k}→${v}`)
            .join(', ') || '未配置频道（使用 agentId 路由 + 全局广播兜底）';

    console.log('[Multi-Agent Hub] 初始化中... 监控 AI 员工: ' + Object.keys(AGENT_META).join(', '));
    console.log('[Multi-Agent Hub] 频道路由配置:', channelSummary);

    let subscriber;

    async function connect() {
        subscriber = createClient({ url: REDIS_URL });

        subscriber.on('error', (err) => {
            console.error('[-] Redis 连接异常:', err.message);
        });

        subscriber.on('reconnecting', () => {
            console.warn('[~] Redis 重连中...');
        });

        await subscriber.connect();
        console.log('✅ [Multi-Agent Hub] 已连接至 Redis，监听频道:', CHANNEL);

        await subscriber.subscribe(CHANNEL, (raw) => {
            try {
                const data    = JSON.parse(raw);
                const source  = data.source  || 'Sentinel-A';
                const type    = data.type    || '通知';
                const content = data.content || raw;
                const level   = data.level   || 'info';
                const ts      = new Date().toLocaleTimeString('zh-CN', { hour12: false });

                const meta    = AGENT_META[source] || { icon: '🤖', label: source };

                // 根据级别选择前缀样式
                const levelPrefix = level === 'critical' ? '🚨🚨🚨 **紧急预警**'
                    : level === 'warning'  ? '⚠️ **风险提示**'
                    : '📡 **实时播报**';

                const finalMessage =
                    `> ${meta.icon} **${meta.label}** · ${levelPrefix} · \`${type}\` — ${ts}\n\n${content}`;

                pushToChannel(app, source, finalMessage);
            } catch (e) {
                console.error('[Multi-Agent Hub] 消息解析失败:', e.message, '| 原始数据:', raw);
            }
        });
    }

    // 首次连接，失败时按指数退避重试
    async function connectWithRetry(attempt = 0) {
        try {
            await connect();
        } catch (err) {
            const boundedAttempt = Math.min(attempt, 5);
            const delay = Math.min(RECONNECT_MS * (2 ** boundedAttempt), MAX_RECONNECT_DELAY_MS);
            console.error(`[-] Redis 连接失败 (第 ${attempt + 1} 次)，${delay / 1000}s 后重试:`, err.message);
            setTimeout(() => connectWithRetry(attempt + 1), delay);
        }
    }

    await connectWithRetry();

    // 心跳检查：每2分钟确认所有 AI 员工的守护进程存活（心跳 TTL 为 360s，每5分钟更新）
    setInterval(async () => {
        try {
            if (!subscriber || !subscriber.isOpen) return;
            for (const [agent, key] of Object.entries(HEARTBEAT_KEYS)) {
                const ts = await subscriber.get(key);
                if (!ts) {
                    console.warn(`[Multi-Agent Hub] ${agent} 心跳超时，守护进程可能已停止运行。`);
                }
            }
        } catch (_) { /* 静默忽略心跳检查失败 */ }
    }, 120_000);
};
