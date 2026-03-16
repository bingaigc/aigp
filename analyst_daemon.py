"""
analyst_daemon.py — Analyst-B 量化分析师守护进程

职责：
  - 盘中每小时（09:30 / 10:30 / 11:00 / 13:30 / 14:30）自动扫描板块轮动
  - 抓取概念/行业板块涨幅榜，识别主升板块和衰退板块
  - 通过 DeepSeek 生成板块轮动分析报告
  - 广播至 Redis 总线 OPENCLAW_ALERTS 频道（source: Analyst-B）
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
log = logging.getLogger('Analyst-B')

# ── 环境变量 ────────────────────────────────────────────────────────────────
ARK_API_KEY  = os.environ.get('ARK_API_KEY')
ARK_MODEL_ID = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
REDIS_URL    = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379')
CHANNEL      = 'OPENCLAW_ALERTS'
AGENT_NAME   = 'Analyst-B'
HEARTBEAT_TTL = int(os.environ.get('HEARTBEAT_TTL', '360'))  # 心跳 key 存活时间（秒）

# 盘中分析时间点
ANALYSIS_TIMES = ('09:30', '10:30', '11:00', '13:30', '14:30')

# ── 客户端 ──────────────────────────────────────────────────────────────────
ai_client: Client | None = None
if ARK_API_KEY:
    ai_client = Client(
        api_key=ARK_API_KEY,
        base_url='https://ark.cn-beijing.volces.com/api/v3',
    )
else:
    log.warning('ARK_API_KEY 未设置，AI 分析功能已禁用。')

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _publish(msg_type: str, content: str, level: str = 'info') -> None:
    """向 Redis 总线广播消息（携带 source 标识）。"""
    payload = json.dumps({
        'type':      msg_type,
        'content':   content,
        'timestamp': datetime.datetime.now().isoformat(),
        'source':    AGENT_NAME,
        'level':     level,
    }, ensure_ascii=False)
    redis_client.publish(CHANNEL, payload)
    log.info('消息已广播: %s', msg_type)


def _fetch_concept_boards() -> list[dict]:
    """抓取概念板块涨幅排名（前5 + 后5）。"""
    try:
        df = ak.stock_board_concept_name_em()
        if df.empty:
            return []
        rank_col = next((c for c in df.columns if '涨跌幅' in c), None)
        if rank_col:
            df = df.sort_values(rank_col, ascending=False)
        top5    = df.head(5).to_dict(orient='records')
        bottom5 = df.tail(5).to_dict(orient='records')
        return top5 + bottom5
    except Exception as exc:
        log.warning('概念板块抓取失败: %s', exc)
        return []


def _fetch_industry_boards() -> list[dict]:
    """抓取行业板块涨幅排名（前5）。"""
    try:
        df = ak.stock_board_industry_name_em()
        if df.empty:
            return []
        rank_col = next((c for c in df.columns if '涨跌幅' in c), None)
        if rank_col:
            df = df.sort_values(rank_col, ascending=False)
        return df.head(5).to_dict(orient='records')
    except Exception as exc:
        log.warning('行业板块抓取失败: %s', exc)
        return []


def _fetch_money_flow() -> dict:
    """抓取主力资金净流入概况。"""
    try:
        df = ak.stock_market_fund_flow()
        if df.empty:
            return {}
        return df.iloc[-1].to_dict()
    except Exception as exc:
        log.debug('主力资金流向抓取失败（非致命）: %s', exc)
        return {}


# ── 核心分析任务 ─────────────────────────────────────────────────────────────
def analyze_sectors() -> None:
    """板块轮动分析（主任务）。"""
    now = datetime.datetime.now()
    # 跳过周末
    if now.weekday() >= 5:
        return

    time_str = now.strftime('%H:%M')
    log.info('开始板块轮动分析 [%s]...', time_str)

    concept_data  = _fetch_concept_boards()
    industry_data = _fetch_industry_boards()
    money_flow    = _fetch_money_flow()

    if ai_client is None:
        top_concepts  = concept_data[:5]
        bot_concepts  = concept_data[5:]
        report = (
            f'## {time_str} 板块轮动快报（离线模式）\n\n'
            f'### 📈 强势概念板块 TOP5\n'
        )
        for b in top_concepts:
            report += f"- {b}\n"
        report += '\n### 📉 衰退概念板块\n'
        for b in bot_concepts:
            report += f"- {b}\n"
        report += '\n_ARK_API_KEY 未配置，AI 分析已跳过。_'
    else:
        prompt = (
            f'当前时间: {now.strftime("%Y-%m-%d %H:%M")}\n'
            f'概念板块数据（前5强 + 后5弱）: {json.dumps(concept_data, ensure_ascii=False)}\n'
            f'行业板块 TOP5: {json.dumps(industry_data, ensure_ascii=False)}\n'
            f'主力资金概况: {json.dumps(money_flow, ensure_ascii=False)}\n\n'
            '请以 Markdown 格式生成板块轮动分析报告，内容包含：\n'
            '①强势板块 TOP3（表格格式，含涨幅和判断）\n'
            '②衰退板块（需警惕）\n'
            '③资金轮动方向判断（资金从哪里流向哪里）\n'
            '④置信度评级（高/中/低）\n'
            '⑤风险提示\n'
            '严格基于数据，不加主观情绪。'
        )
        try:
            resp = ai_client.chat.completions.create(
                model=ARK_MODEL_ID,
                messages=[
                    {'role': 'system', 'content': '你是 Analyst-B，冷血量化板块分析师。'},
                    {'role': 'user',   'content': prompt},
                ],
                temperature=0.2,
                max_tokens=1024,
            )
            report = resp.choices[0].message.content
        except OpenAIError as exc:
            log.error('AI 分析失败: %s', exc)
            report = f'AI 分析失败: {exc}\n\n原始板块数据：{concept_data}'

    _publish('板块轮动', report, level='info')
    log.info('板块轮动报告已发送。')


# ── 心跳 ─────────────────────────────────────────────────────────────────────
def heartbeat() -> None:
    try:
        redis_client.setex('analyst:heartbeat', HEARTBEAT_TTL, datetime.datetime.now().isoformat())
    except Exception as exc:
        log.warning('心跳写入失败: %s', exc)


# ── 调度（仅盘中时段触发） ────────────────────────────────────────────────────
for _t in ANALYSIS_TIMES:
    schedule.every().day.at(_t).do(analyze_sectors)

schedule.every(5).minutes.do(heartbeat)

log.info('📊 Analyst-B 量化分析师已启动')
heartbeat()

while True:
    schedule.run_pending()
    time.sleep(30)
