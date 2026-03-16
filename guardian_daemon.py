"""
guardian_daemon.py — Guardian-C 风控守卫守护进程

职责：
  - 每 5 分钟（盘中）评估大盘整体风险系数（0~10 分）
  - 监控连板梯队健康度，低于 40% 时触发情绪退潮预警
  - 大盘单日跌幅 > 2% 时触发系统性风险扫描并发出红色预警
  - 将风险播报广播至 Redis 总线（source: Guardian-C）
"""
import os
import json
import time
import datetime
import logging
import schedule
import redis
import akshare as ak
from openai import Client, OpenAIError

# ── 日志 ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('Guardian-C')

# ── 环境变量 ────────────────────────────────────────────────────────────────
ARK_API_KEY  = os.environ.get('ARK_API_KEY')
ARK_MODEL_ID = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
REDIS_URL    = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379')
CHANNEL      = 'OPENCLAW_ALERTS'
AGENT_NAME   = 'Guardian-C'
HEARTBEAT_TTL = int(os.environ.get('HEARTBEAT_TTL', '360'))  # 心跳 key 存活时间（秒）

# 风险阈值
RISK_HIGH_THRESHOLD    = 7.0   # 风险系数超过此值发出预警
RISK_EXTREME_THRESHOLD = 8.0   # 风险系数超过此值为"极高风险"
INDEX_DROP_THRESHOLD   = -2.0  # 大盘单日跌幅触发系统性扫描（百分比）
BOARD_HEALTH_THRESHOLD = 40.0  # 连板健康度低于此值触发退潮预警

# ── 客户端 ──────────────────────────────────────────────────────────────────
ai_client: Client | None = None
if ARK_API_KEY:
    ai_client = Client(
        api_key=ARK_API_KEY,
        base_url='https://ark.cn-beijing.volces.com/api/v3',
    )
else:
    log.warning('ARK_API_KEY 未设置，AI 风险分析功能已禁用。')

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _publish(msg_type: str, content: str, level: str = 'info') -> None:
    payload = json.dumps({
        'type':      msg_type,
        'content':   content,
        'timestamp': datetime.datetime.now().isoformat(),
        'source':    AGENT_NAME,
        'level':     level,
    }, ensure_ascii=False)
    redis_client.publish(CHANNEL, payload)
    log.info('消息已广播 [%s]: %s', level, msg_type)


def _fetch_index_change() -> float:
    """获取上证指数今日涨跌幅（百分比）。失败时返回 0。"""
    try:
        df = ak.stock_zh_index_spot_em(symbol='上证系列指数')
        sh = df[df['代码'] == '000001'] if '代码' in df.columns else df.head(1)
        if sh.empty:
            return 0.0
        chg_col = next((c for c in sh.columns if '涨跌幅' in c), None)
        if chg_col:
            return float(sh[chg_col].iloc[0])
        return 0.0
    except Exception as exc:
        log.debug('大盘数据抓取失败（非致命）: %s', exc)
        return 0.0


def _fetch_zt_pool_count() -> tuple[int, int]:
    """返回 (今日涨停数, 今日最高连板数)。"""
    try:
        date_str = datetime.datetime.now().strftime('%Y%m%d')
        df = ak.stock_zt_pool_em(date=date_str)
        if df.empty:
            return 0, 0
        zt_count = len(df)
        max_board = int(df['连板数'].max()) if '连板数' in df.columns else 0
        return zt_count, max_board
    except Exception as exc:
        log.debug('涨停池抓取失败: %s', exc)
        return 0, 0


def _calc_board_health(zt_count: int, max_board: int) -> float:
    """
    简化的连板健康度计算（0~100）：
    涨停数越多、最高连板数越高，健康度越高。
    无数据时返回 50（中性）。
    """
    if zt_count == 0:
        return 20.0
    base = min(zt_count / 80.0, 1.0) * 60.0
    board_bonus = min(max_board / 5.0, 1.0) * 40.0
    return round(base + board_bonus, 1)


def _calc_risk_score(index_change: float, board_health: float) -> float:
    """
    综合风险系数计算（0~10）：
    大盘跌幅和连板健康度共同决定，分数越高风险越大。
    """
    # 大盘跌幅风险（0~5分，跌 5% 以上满分）
    index_risk = min(max(-index_change, 0) / 5.0, 1.0) * 5.0
    # 情绪风险（健康度越低风险越高，0~5分）
    emotion_risk = (1.0 - min(board_health / 100.0, 1.0)) * 5.0
    return round(index_risk + emotion_risk, 1)


# ── 核心风控任务 ─────────────────────────────────────────────────────────────
def risk_scan() -> None:
    """执行一次风险扫描并按需广播预警（主任务）。"""
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return

    # 仅在盘中时段执行（09:25 ~ 15:05）
    market_open  = now.replace(hour=9,  minute=25, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=5,  second=0, microsecond=0)
    if not (market_open <= now <= market_close):
        return

    index_change = _fetch_index_change()
    zt_count, max_board = _fetch_zt_pool_count()
    board_health = _calc_board_health(zt_count, max_board)
    risk_score   = _calc_risk_score(index_change, board_health)

    # 将关键指标写入 Redis 供其他 Agent 读取
    redis_client.hset('guardian:status', mapping={
        'risk_score':    risk_score,
        'index_change':  index_change,
        'board_health':  board_health,
        'zt_count':      zt_count,
        'max_board':     max_board,
        'updated_at':    now.isoformat(),
    })

    log.info(
        '风险扫描: 指数涨跌=%.2f%% 连板健康度=%.1f%% 风险系数=%.1f/10',
        index_change, board_health, risk_score,
    )

    # 决定是否触发预警
    is_high_risk  = risk_score >= RISK_HIGH_THRESHOLD
    is_drop_event = index_change <= INDEX_DROP_THRESHOLD
    is_ebb_tide   = board_health < BOARD_HEALTH_THRESHOLD

    if not (is_high_risk or is_drop_event or is_ebb_tide):
        return  # 风险可控，不发广播

    level = 'critical' if (is_high_risk or is_drop_event) else 'warning'

    if ai_client is None:
        risk_label = '极高风险' if risk_score >= RISK_EXTREME_THRESHOLD else ('高风险' if risk_score >= RISK_HIGH_THRESHOLD else '中等风险')
        report = (
            f'## ⚠️ Guardian-C 风险播报\n\n'
            f'**风险系数**: {risk_score} / 10  [{risk_label}]\n\n'
            f'### 📊 关键指标\n'
            f'- 大盘今日涨跌: {index_change:+.2f}%\n'
            f'- 连板健康度: {board_health:.1f}%\n'
            f'- 涨停板数量: {zt_count}\n'
            f'- 最高连板数: {max_board}\n\n'
            f'### 🛡️ 仓位建议\n'
            f'- 推荐仓位: {"0%" if is_high_risk else "30%"}\n'
            f'- 操作: {"立即清仓！" if is_drop_event else "减至半仓，等待信号确认"}\n\n'
            f'### 🔔 触发预警\n'
            + ('- 🔴 大盘下跌 > 2%，系统性风险！\n' if is_drop_event else '')
            + ('- 🟠 风险系数超过 7，建议降仓\n' if is_high_risk else '')
            + ('- 🟡 连板情绪退潮，追板成功率骤降\n' if is_ebb_tide else '')
            + '\n_ARK_API_KEY 未配置，AI 分析已跳过。_'
        )
    else:
        prompt = (
            f'当前时间: {now.strftime("%Y-%m-%d %H:%M")}\n'
            f'大盘今日涨跌幅: {index_change:+.2f}%\n'
            f'连板健康度: {board_health:.1f}%\n'
            f'涨停板数量: {zt_count}\n'
            f'最高连板数: {max_board}\n'
            f'综合风险系数: {risk_score}/10\n\n'
            '你是 Guardian-C 风控守卫，请生成一份简洁的风险播报（Markdown），包含：\n'
            '①风险等级评定（低/中/高/极高）\n'
            '②关键风险指标分析\n'
            '③具体仓位建议（百分比）\n'
            '④止损警戒线\n'
            '⑤触发的具体预警条件\n'
            '语气要铁血，不要废话，用数字说话。'
        )
        try:
            resp = ai_client.chat.completions.create(
                model=ARK_MODEL_ID,
                messages=[
                    {'role': 'system', 'content': '你是 Guardian-C，冷血风控守卫，保护本金为第一使命。'},
                    {'role': 'user',   'content': prompt},
                ],
                temperature=0.1,
                max_tokens=800,
            )
            report = resp.choices[0].message.content
        except OpenAIError as exc:
            log.error('AI 风险分析失败: %s', exc)
            report = (
                f'AI 风险分析失败: {exc}\n\n'
                f'**原始风险系数**: {risk_score}/10\n'
                f'**大盘涨跌**: {index_change:+.2f}%\n'
                f'**连板健康度**: {board_health:.1f}%'
            )

    msg_type = '极高风险预警' if level == 'critical' else '风险提示'
    _publish(msg_type, report, level=level)


# ── 心跳 ─────────────────────────────────────────────────────────────────────
def heartbeat() -> None:
    try:
        redis_client.setex('guardian:heartbeat', HEARTBEAT_TTL, datetime.datetime.now().isoformat())
    except Exception as exc:
        log.warning('心跳写入失败: %s', exc)


# ── 调度 ─────────────────────────────────────────────────────────────────────
schedule.every(5).minutes.do(risk_scan)
schedule.every(5).minutes.do(heartbeat)

log.info('🛡️ Guardian-C 风控守卫已启动')
heartbeat()
risk_scan()  # 启动时立即执行一次

while True:
    schedule.run_pending()
    time.sleep(30)
