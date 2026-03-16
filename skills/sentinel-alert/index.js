'use strict';
/**
 * sentinel-alert — OpenClaw Multi-Agent Hub Skill
 *
 * 订阅 Redis OPENCLAW_ALERTS 频道，将来自所有 AI 员工的消息
 * 通过 OpenClaw 的 chat.broadcast / session:message:inject 接口
 * 主动推送弹窗至前端 UI。
 *
 * 支持多 Agent 路由：根据消息中的 source 字段显示对应 Agent 标识。
 */
const { createClient } = require('redis');

const REDIS_URL             = process.env.REDIS_URL || 'redis://127.0.0.1:6379';
const CHANNEL               = 'OPENCLAW_ALERTS';
const RECONNECT_MS          = 5_000;
const MAX_RECONNECT_DELAY_MS = 60_000;

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
 * @param {import('@openclaw/sdk').App} app
 */
module.exports = async function registerSkill(app) {
    console.log('[Multi-Agent Hub] 初始化中... 监控 AI 员工: ' + Object.keys(AGENT_META).join(', '));

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

                if (app.chat && typeof app.chat.broadcast === 'function') {
                    app.chat.broadcast({ role: 'assistant', content: finalMessage });
                } else if (typeof app.emit === 'function') {
                    app.emit('session:message:inject', {
                        role: 'assistant',
                        content: finalMessage,
                    });
                } else {
                    console.warn('[Multi-Agent Hub] 广播接口不可用，消息被丢弃。source=', source);
                }
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

    // 心跳检查：每分钟确认所有 AI 员工的守护进程存活
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
    }, 60_000);
};
