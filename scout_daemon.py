"""
scout_daemon.py — Scout-D 游骑侦察守护进程

职责：
  - 09:15 盘前侦察：扫描竞价异动，锁定当日强势候选
  - 11:30 上午复盘：总结上午资金流向，更新强弱名单
  - 14:30 尾盘侦察：扫描尾盘异动和封板质量，评估收盘信号
  - 将侦察报告广播至 Redis 总线（source: Scout-D）
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
log = logging.getLogger('Scout-D')

# ── 环境变量 ────────────────────────────────────────────────────────────────
ARK_API_KEY  = os.environ.get('ARK_API_KEY')
ARK_MODEL_ID = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
REDIS_URL    = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379')
CHANNEL      = 'OPENCLAW_ALERTS'
AGENT_NAME   = 'Scout-D'

# 筛选阈值
MIN_SEAL_FUND_WAN = 10_000   # 最低封板资金（万元）：1 亿元
MAX_BOMB_COUNT    = 1        # 最大允许炸板次数

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
    payload = json.dumps({
        'type':      msg_type,
        'content':   content,
        'timestamp': datetime.datetime.now().isoformat(),
        'source':    AGENT_NAME,
        'level':     level,
    }, ensure_ascii=False)
    redis_client.publish(CHANNEL, payload)
    log.info('消息已广播: %s', msg_type)


def _fetch_zt_pool(date_str: str) -> list[dict]:
    """
    抓取涨停池，筛选出高质量封板个股：
    封板资金 > 阈值 且 炸板次数 ≤ 阈值。
    """
    try:
        df = ak.stock_zt_pool_em(date=date_str)
        if df.empty:
            return []
        # 标准化列名（不同版本 akshare 列名可能略有差异）
        col_seal = next((c for c in df.columns if '封板' in c and '资金' in c), None)
        col_bomb = next((c for c in df.columns if '炸板' in c), None)
        col_board= next((c for c in df.columns if '连板' in c), None)

        if col_seal:
            df = df[df[col_seal] / 10_000 >= MIN_SEAL_FUND_WAN]
        if col_bomb:
            df = df[df[col_bomb] <= MAX_BOMB_COUNT]

        cols = [c for c in ['代码', '名称', col_seal, col_bomb, col_board, '所属行业']
                if c is not None and c in df.columns]
        return df.sort_values(col_seal, ascending=False).head(10)[cols].to_dict(orient='records')
    except Exception as exc:
        log.warning('涨停池抓取失败: %s', exc)
        return []


def _fetch_sector_momentum() -> list[dict]:
    """抓取今日强势概念板块（涨幅前3）。"""
    try:
        df = ak.stock_board_concept_name_em()
        if df.empty:
            return []
        rank_col = next((c for c in df.columns if '涨跌幅' in c), None)
        if rank_col:
            df = df.sort_values(rank_col, ascending=False)
        return df.head(3).to_dict(orient='records')
    except Exception as exc:
        log.debug('板块数据抓取失败（非致命）: %s', exc)
        return []


def _read_guardian_risk() -> float:
    """从 Redis 读取 Guardian-C 的实时风险系数（不存在时返回中性 5.0）。"""
    try:
        score = redis_client.hget('guardian:status', 'risk_score')
        return float(score) if score else 5.0
    except Exception:
        return 5.0


def _build_report(session: str, date_str: str,
                  targets: list[dict], sectors: list[dict],
                  risk_score: float) -> str:
    """在无 AI 时构建基础侦察报告。"""
    high_risk = risk_score >= 7.0
    now_str   = datetime.datetime.now().strftime('%H:%M')
    report    = (
        f'## 🔭 Scout-D 侦察快报 [{session}] {now_str}\n\n'
        f'**当前风险系数（Guardian-C）**: {risk_score}/10\n\n'
        '### 🎯 高质量涨停标的\n'
    )
    if targets:
        report += '| 代码 | 名称 | 封板资金 | 炸板次数 |\n|------|------|---------|----------|\n'
        for t in targets[:5]:
            code     = t.get('代码', '-')
            name     = t.get('名称', '-')
            seal_key = next((k for k in t if '封板' in k and '资金' in k), '')
            bomb_key = next((k for k in t if '炸板' in k), '')
            seal     = t.get(seal_key, '-')
            bomb     = t.get(bomb_key, '-')
            report += f'| {code} | {name} | {seal} | {bomb} |\n'
    else:
        report += '_暂无满足条件的高质量涨停标的。_\n'

    report += '\n### 📡 强势板块\n'
    for s in sectors:
        name = s.get('板块名称', s.get('名称', '-'))
        chg  = s.get(next((k for k in s if '涨跌幅' in k), ''), '-')
        report += f'- {name}: {chg}\n'

    if high_risk:
        report += '\n### 🚫 风险提示\n- ⛔ 当前风险系数过高，建议不追板，等待风险回落再操作。\n'
    else:
        report += '\n### 💡 操作建议\n- 止损设置: 封板价以下 3%\n- 仓位: 单票 ≤ 20%\n'

    report += '\n_ARK_API_KEY 未配置，AI 分析已跳过。_'
    return report


def _ai_report(session: str, date_str: str,
               targets: list[dict], sectors: list[dict],
               risk_score: float) -> str:
    """调用 DeepSeek 生成侦察报告。"""
    prompt = (
        f'当前时间: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M")} [{session}]\n'
        f'今日高质量涨停标的（封板资金充足+炸板少）: {json.dumps(targets, ensure_ascii=False)}\n'
        f'强势概念板块 TOP3: {json.dumps(sectors, ensure_ascii=False)}\n'
        f'Guardian-C 实时风险系数: {risk_score}/10\n\n'
        '你是 Scout-D，游骑侦察，请生成侦察快报（Markdown），包含：\n'
        '①今日重点标的（表格，最多5个，含代码/名称/信号判断）\n'
        '②盘面异动信号分析\n'
        '③风险提示（如有炸板风险标注）\n'
        '④操作建议（进攻条件和止损位）\n'
        '若风险系数 >= 7，强制输出"当前不宜追板"。'
    )
    try:
        resp = ai_client.chat.completions.create(
            model=ARK_MODEL_ID,
            messages=[
                {'role': 'system', 'content': '你是 Scout-D，冷血市场侦察员，发现机会也发现陷阱。'},
                {'role': 'user',   'content': prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        return resp.choices[0].message.content
    except OpenAIError as exc:
        log.error('AI 侦察分析失败: %s', exc)
        return _build_report(session, date_str, targets, sectors, risk_score)


# ── 三个固定时段的侦察任务 ───────────────────────────────────────────────────
def _scout(session: str) -> None:
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return
    date_str   = now.strftime('%Y%m%d')
    risk_score = _read_guardian_risk()
    targets    = _fetch_zt_pool(date_str)
    sectors    = _fetch_sector_momentum()

    log.info('[%s] 开始侦察... 候选标的: %d 个', session, len(targets))

    if ai_client is not None:
        report = _ai_report(session, date_str, targets, sectors, risk_score)
    else:
        report = _build_report(session, date_str, targets, sectors, risk_score)

    level = 'warning' if risk_score >= 7.0 else 'info'
    _publish(f'侦察快报({session})', report, level=level)
    log.info('[%s] 侦察快报已发送。', session)


def pre_market_scout() -> None:
    _scout('盘前')


def midday_scout() -> None:
    _scout('上午收盘')


def late_session_scout() -> None:
    _scout('尾盘')


# ── 心跳 ─────────────────────────────────────────────────────────────────────
def heartbeat() -> None:
    try:
        redis_client.setex('scout:heartbeat', 60, datetime.datetime.now().isoformat())
    except Exception as exc:
        log.warning('心跳写入失败: %s', exc)


# ── 调度 ─────────────────────────────────────────────────────────────────────
schedule.every().day.at('09:15').do(pre_market_scout)
schedule.every().day.at('11:30').do(midday_scout)
schedule.every().day.at('14:30').do(late_session_scout)
schedule.every(5).minutes.do(heartbeat)

log.info('🔭 Scout-D 游骑侦察已启动')
heartbeat()

while True:
    schedule.run_pending()
    time.sleep(15)
