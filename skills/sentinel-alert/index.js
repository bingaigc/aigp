'use strict';
/**
 * sentinel-alert — OpenClaw Skill
 *
 * 订阅 Redis OPENCLAW_ALERTS 频道，将 Sentinel-A 的复盘战报和实时预警
 * 通过 OpenClaw 的 chat.broadcast / session:message:inject 接口
 * 主动推送弹窗至前端 UI。
 */
const { createClient } = require('redis');

const REDIS_URL    = process.env.REDIS_URL    || 'redis://127.0.0.1:6379';
const CHANNEL      = 'OPENCLAW_ALERTS';
const HEARTBEAT_KEY = 'sentinel:heartbeat';
const RECONNECT_MS = 5_000;

/**
 * @param {import('@openclaw/sdk').App} app
 */
module.exports = async function registerSkill(app) {
    console.log('[Sentinel Skill] 初始化中...');

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
        console.log('✅ [Sentinel Skill] 已连接至 Redis，监听频道:', CHANNEL);

        await subscriber.subscribe(CHANNEL, (raw) => {
            try {
                const data = JSON.parse(raw);
                const type    = data.type    || '通知';
                const content = data.content || raw;
                const ts      = new Date().toLocaleTimeString('zh-CN', { hour12: false });

                const finalMessage =
                    `> **🚨 Sentinel-A 实时监控 (${type})** — ${ts}\n\n${content}`;

                if (app.chat && typeof app.chat.broadcast === 'function') {
                    app.chat.broadcast({ role: 'assistant', content: finalMessage });
                } else if (typeof app.emit === 'function') {
                    app.emit('session:message:inject', {
                        role: 'assistant',
                        content: finalMessage,
                    });
                } else {
                    console.warn('[Sentinel Skill] 广播接口不可用，消息被丢弃。');
                }
            } catch (e) {
                console.error('[Sentinel Skill] 消息解析失败:', e.message, '| 原始数据:', raw);
            }
        });
    }

    // 首次连接，失败时按指数退避重试
    async function connectWithRetry(attempt = 0) {
        try {
            await connect();
        } catch (err) {
            const boundedAttempt = Math.min(attempt, 5);
            const delay = Math.min(RECONNECT_MS * (2 ** boundedAttempt), 60_000);
            console.error(`[-] Redis 连接失败 (第 ${attempt + 1} 次)，${delay / 1000}s 后重试:`, err.message);
            setTimeout(() => connectWithRetry(attempt + 1), delay);
        }
    }

    await connectWithRetry();

    // 心跳检查：每分钟确认守护进程存活
    setInterval(async () => {
        try {
            if (!subscriber || !subscriber.isOpen) return;
            const ts = await subscriber.get(HEARTBEAT_KEY);
            if (!ts) {
                console.warn('[Sentinel Skill] 守护进程心跳超时，Sentinel-A 可能已停止运行。');
            }
        } catch (_) { /* 静默忽略心跳检查失败 */ }
    }, 60_000);
};
