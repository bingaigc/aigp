"""
Sentinel-A 量化引擎守护进程
每日收盘后（16:30）抓取 A 股涨停池与龙虎榜数据，
通过 DeepSeek 大模型生成复盘战报，并经 Redis 总线广播至 OpenClaw 前端。
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
log = logging.getLogger('Sentinel-A')

# ── 环境变量 ────────────────────────────────────────────────────────────────
ARK_API_KEY = os.environ.get('ARK_API_KEY')
ARK_MODEL_ID = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
REDIS_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379')
REDIS_CHANNEL = 'OPENCLAW_ALERTS'
REPORT_TIME = os.environ.get('REPORT_TIME', '16:30')  # 可通过环境变量调整
HEARTBEAT_TTL = int(os.environ.get('HEARTBEAT_TTL', '360'))  # 心跳 key 存活时间（秒）

# ── 银河战舰：中转网关 + 专属模型配置 ───────────────────────────────────────
# MODEL_GATEWAY_URL: 指向 gateway/model_router.py 的地址（如 http://127.0.0.1:8090）
# SENTINEL_A_MODEL:  Sentinel-A 专属模型（推荐强推理、中文优秀的大模型）
MODEL_GATEWAY_URL = os.environ.get('MODEL_GATEWAY_URL', '')
AGENT_MODEL = os.environ.get(
    'SENTINEL_A_MODEL',
    'nvidia/llama-3.3-nemotron-super-49b-v1',  # 默认：NVIDIA NIM 顶级推理模型
)

# ── 客户端 ──────────────────────────────────────────────────────────────────
# 路由优先级：中转网关 > Volcengine ARK > 离线模式
ai_client: Client | None = None
MODEL_ID: str = ARK_MODEL_ID  # 实际使用的 model 参数
if MODEL_GATEWAY_URL:
    # 银河战舰模式：通过中转网关访问多厂商模型
    ai_client = Client(api_key='gateway', base_url=MODEL_GATEWAY_URL)
    MODEL_ID = AGENT_MODEL
    log.info('银河战舰模式已启用：网关=%s  模型=%s', MODEL_GATEWAY_URL, MODEL_ID)
elif ARK_API_KEY:
    ai_client = Client(
        api_key=ARK_API_KEY,
        base_url='https://ark.cn-beijing.volces.com/api/v3',
    )
else:
    log.warning('ARK_API_KEY 未设置，AI 分析功能已禁用。')

redis_client = redis.from_url(REDIS_URL, decode_responses=True)


# ── 工具函数 ─────────────────────────────────────────────────────────────────
def _publish(msg_type: str, content: str) -> None:
    """向 Redis 总线广播消息。"""
    payload = json.dumps({'type': msg_type, 'content': content}, ensure_ascii=False)
    redis_client.publish(REDIS_CHANNEL, payload)
    log.info('消息已广播至 Redis 总线: %s', msg_type)


def _fetch_sentiment(date_str: str) -> dict:
    """抓取涨停池情绪数据。"""
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df.empty:
            return {'状态': '无涨停数据'}
        return {
            '涨停数': len(df),
            '最高连板': int(df['连板数'].max()),
            '平均封板时间': str(df['首次封板时间'].mode().iloc[0]) if '首次封板时间' in df.columns else 'N/A',
        }
    except Exception as exc:
        log.warning('涨停池抓取失败: %s', exc)
        return {'状态': f'抓取失败: {exc}'}


def _fetch_lhb(date_str: str) -> list[dict]:
    """抓取龙虎榜前五净买入。"""
    try:
        df = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        if df.empty:
            return []
        df['净买额(万)'] = (df['净买额'] / 10_000).round(2)
        cols = [c for c in ['代码', '名称', '净买额(万)'] if c in df.columns]
        return (df
                .sort_values('净买额(万)', ascending=False)
                .head(5)[cols]
                .to_dict(orient='records'))
    except Exception as exc:
        log.warning('龙虎榜抓取失败: %s', exc)
        return []


def _fetch_sector_leaders(date_str: str) -> list[dict]:
    """
    抓取概念板块涨幅榜前五（板块轮动参考）。
    优先使用当日实时涨幅排名，而非锁定固定板块。
    """
    try:
        df = ak.stock_board_concept_name_em()
        if df.empty:
            return []
        # 按涨跌幅降序排列，取前五板块名称
        rank_col = next((c for c in df.columns if '涨跌幅' in c), None)
        if rank_col:
            df = df.sort_values(rank_col, ascending=False)
        return df.head(5).to_dict(orient='records')
    except Exception as exc:
        log.debug('板块数据抓取失败（非致命）: %s', exc)
        return []


def generate_daily_report() -> None:
    """生成并广播每日复盘战报（主任务）。"""
    now = datetime.datetime.now()
    # 跳过周末
    if now.weekday() >= 5:
        log.info('今日为周末，跳过复盘。')
        return

    date_str = now.strftime('%Y%m%d')
    log.info('开始生成 %s 复盘战报...', date_str)

    # 1. 采集数据
    sentiment = _fetch_sentiment(date_str)
    lhb = _fetch_lhb(date_str)

    # 2. AI 深度推理
    if ai_client is None:
        report = (
            f'## {date_str} 复盘简报（离线模式）\n\n'
            f'- **情绪指标**: {sentiment}\n'
            f'- **龙虎榜核心**: {lhb}\n\n'
            '_ARK_API_KEY 未配置，AI 推理已跳过。_'
        )
    else:
        prompt = (
            f'今日日期: {date_str}\n'
            f'情绪指标: {json.dumps(sentiment, ensure_ascii=False)}\n'
            f'龙虎榜核心买入(Top5): {json.dumps(lhb, ensure_ascii=False)}\n\n'
            '请以严格的 Markdown 格式生成一份冰冷的 A 股复盘简报，'
            '内容包含：①情绪周期判断（亢奋/平淡/恐慌），'
            '②主线题材梳理，③连板梯队健康度，'
            '④明日操作纪律（含防守止损线）。禁止主观情绪，只信仰数据。'
        )
        try:
            resp = ai_client.chat.completions.create(
                model=MODEL_ID,
                messages=[
                    {'role': 'system', 'content': '你是 Sentinel-A，冷血量化复盘机器。'},
                    {'role': 'user', 'content': prompt},
                ],
                temperature=0.2,
                max_tokens=2048,
            )
            report = resp.choices[0].message.content
        except OpenAIError as exc:
            log.error('AI 推理失败: %s', exc)
            report = f'AI 推理失败: {exc}\n\n原始数据：{sentiment} | {lhb}'

    # 3. 广播
    _publish('每日战报', report)
    log.info('复盘战报已发送。')


# ── 实盘心跳（每5分钟检查连接状态）───────────────────────────────────────────
def heartbeat() -> None:
    """向 Redis 写入心跳，供前端判断守护进程是否存活。"""
    try:
        redis_client.setex('sentinel:heartbeat', HEARTBEAT_TTL, datetime.datetime.now().isoformat())
    except Exception as exc:
        log.warning('心跳写入失败: %s', exc)


# ── 主循环 ───────────────────────────────────────────────────────────────────
schedule.every().day.at(REPORT_TIME).do(generate_daily_report)
schedule.every(5).minutes.do(heartbeat)

log.info('🦅 Sentinel-A 量化引擎已启动 (复盘时间: %s)', REPORT_TIME)
heartbeat()  # 立即写入首次心跳

while True:
    schedule.run_pending()
    time.sleep(30)
