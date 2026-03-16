"""
backtest_engine.py — Sentinel-A 离线回测引擎

功能：
  1. 从 akshare 拉取 A 股历史 K 线数据（或从 CSV 载入）
  2. 运行 SentinelBaseStrategy（20 日均线突破 + 8% 硬性止损）
  3. 将突破信号送入 AI「时光机」过滤器（DeepSeek），
     模拟只在 AI 认可时才入场的场景
  4. 对比纯量化 vs AI 过滤后的胜率、最大回撤、夏普比率，
     并输出 Matplotlib 对比图

用法：
  export ARK_API_KEY="your_key"          # 可选，不设则跳过 AI 过滤
  python backtest_engine.py              # 使用默认示例股票
  python backtest_engine.py --symbol 600519 --start 20230101 --end 20240101
"""

import os
import sys
import json
import logging
import argparse
import datetime
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 无头模式（容器内无显示器）
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

import backtrader as bt
import akshare as ak
from openai import Client, OpenAIError

# ── 日志 ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('BacktestEngine')

# ── 默认参数 ─────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL    = '000001'   # 平安银行（示例）
DEFAULT_START     = '20220101'
DEFAULT_END       = datetime.date.today().strftime('%Y%m%d')
DEFAULT_CASH      = 100_000.0
DEFAULT_COMMISSION = 0.0003    # 万三佣金
DEFAULT_SLIPPAGE  = 0.001      # 0.1% 滑点

# ── AI 配置 ──────────────────────────────────────────────────────────────────
ARK_API_KEY  = os.environ.get('ARK_API_KEY')
ARK_BASE_URL = os.environ.get('ARK_BASE_URL', 'https://ark.cn-beijing.volces.com/api/v3')
ARK_MODEL    = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')

ai_client: Optional[Client] = None
if ARK_API_KEY:
    ai_client = Client(api_key=ARK_API_KEY, base_url=ARK_BASE_URL)
    log.info('AI 过滤器已启用 (model: %s)', ARK_MODEL)
else:
    log.warning('ARK_API_KEY 未设置，AI 过滤将跳过（视为全部通过）。')


# =============================================================================
# 1. 数据源
# =============================================================================

def fetch_stock_data(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    从 akshare 拉取 A 股前复权日 K 线，返回 Backtrader 可直接使用的 DataFrame。
    列：datetime, open, high, low, close, volume, openinterest
    """
    log.info('拉取股票 %s 历史数据 [%s → %s] ...', symbol, start, end)
    try:
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period='daily',
            start_date=start,
            end_date=end,
            adjust='qfq',   # 前复权
        )
    except Exception as exc:
        log.error('akshare 数据拉取失败 (symbol=%s): %s', symbol, exc)
        raise RuntimeError(f'无法获取股票 {symbol} 的历史数据: {exc}') from exc
    # 统一列名
    col_map = {
        '日期': 'datetime', '开盘': 'open', '最高': 'high',
        '最低': 'low',    '收盘': 'close', '成交量': 'volume',
    }
    df = df.rename(columns=col_map)
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.sort_values('datetime').reset_index(drop=True)
    df['openinterest'] = 0
    needed = ['datetime', 'open', 'high', 'low', 'close', 'volume', 'openinterest']
    return df[needed]


def df_to_bt_feed(df: pd.DataFrame) -> bt.feeds.PandasData:
    """将 DataFrame 转换为 Backtrader 数据源。"""
    df = df.set_index('datetime')
    return bt.feeds.PandasData(
        dataname=df,
        datetime=None,  # 索引即为时间
        open='open',
        high='high',
        low='low',
        close='close',
        volume='volume',
        openinterest='openinterest',
    )


# =============================================================================
# 2. 量化策略
# =============================================================================

class SentinelBaseStrategy(bt.Strategy):
    """
    底层量化逻辑：
      - 入场：收盘价由下往上突破 N 日均线
      - 出场：收盘价跌破入场价 × (1 - stop_loss_pct)
      - 可选：AI 时光机二次审核（ai_filter=True）
    """
    params = (
        ('ma_period',     20),
        ('stop_loss_pct', 0.08),
        ('ai_filter',     False),  # True = 启用 AI 过滤
        ('symbol',        ''),
    )

    def __init__(self):
        self.sma       = bt.indicators.SimpleMovingAverage(
            self.data.close, period=self.p.ma_period,
        )
        self.crossover = bt.indicators.CrossOver(self.data.close, self.sma)
        self.buy_price = 0.0
        self.trade_logs: list[dict] = []
        self._pending_ai: Optional[dict] = None  # 待 AI 审核的信号

    def next(self):
        # 已持仓：硬性止损
        if self.position:
            if self.data.close[0] < self.buy_price * (1.0 - self.p.stop_loss_pct):
                self.sell()
                self.trade_logs.append({
                    'date':   self.data.datetime.date(0).isoformat(),
                    'price':  round(float(self.data.close[0]), 4),
                    'PnL':    round(-self.p.stop_loss_pct * 100, 2),
                    'type':   'stop_loss',
                    'ai_verdict': 'N/A',
                })
            return

        # 突破信号（金叉）
        if self.crossover[0] > 0:
            date_str = self.data.datetime.date(0).isoformat()
            verdict  = 'BUY'

            if self.p.ai_filter:
                verdict = ai_time_machine_filter(date_str, self.p.symbol)
                log.info('[AI Filter] %s @ %s → %s', self.p.symbol, date_str, verdict)

            self.trade_logs.append({
                'date':       date_str,
                'price':      round(float(self.data.close[0]), 4),
                'PnL':        0,
                'type':       'signal',
                'ai_verdict': verdict,
            })

            if verdict == 'BUY':
                self.buy()
                self.buy_price = self.data.close[0]

    def notify_trade(self, trade):
        """记录真实盈亏。"""
        if not trade.isclosed:
            return
        for rec in reversed(self.trade_logs):
            if rec['type'] == 'signal' and rec['PnL'] == 0:
                rec['PnL'] = round(trade.pnlcomm / (trade.price * trade.size) * 100, 2)
                break


# =============================================================================
# 3. AI 时光机过滤器
# =============================================================================

def ai_time_machine_filter(date_str: str, symbol: str) -> str:
    """
    在不使用任何未来数据的前提下，让大模型判断该突破是否可信。
    返回 'BUY' 或 'REJECT'。
    """
    if ai_client is None:
        return 'BUY'  # 无 AI 密钥时视为全部通过

    prompt = (
        f'时间定格在 {date_str}，股票代码 {symbol} 在技术面触发了20日均线突破信号。\n'
        '请结合该日期前已知的宏观环境、行业景气度和市场情绪，'
        '以极度严苛的态度判断这是否是一次真突破还是诱多陷阱。\n'
        '仅回答 [买入] 或 [否决]，不要解释。'
    )
    try:
        resp = ai_client.chat.completions.create(
            model=ARK_MODEL,
            messages=[
                {'role': 'system', 'content': '你是冷血量化机器，判断突破信号真伪。'},
                {'role': 'user',   'content': prompt},
            ],
            temperature=0.1,
            max_tokens=10,
        )
        reply = resp.choices[0].message.content.strip()
        return 'BUY' if '[买入]' in reply else 'REJECT'
    except OpenAIError as exc:
        log.warning('AI 过滤器调用失败，默认拒绝: %s', exc)
        return 'REJECT'


# =============================================================================
# 4. 运行回测 + 统计
# =============================================================================

def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    use_ai: bool = False,
    cash: float = DEFAULT_CASH,
) -> tuple[bt.Cerebro, list[dict], float]:
    """
    运行一次完整回测，返回 (cerebro, trade_logs, final_portfolio_value)。
    """
    cerebro = bt.Cerebro(stdstats=True)
    cerebro.adddata(df_to_bt_feed(df))
    cerebro.addstrategy(
        SentinelBaseStrategy,
        ai_filter=use_ai,
        symbol=symbol,
    )
    cerebro.broker.setcash(cash)
    cerebro.broker.setcommission(commission=DEFAULT_COMMISSION)
    cerebro.broker.set_slippage_perc(DEFAULT_SLIPPAGE)

    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe', riskfreerate=0.02)
    cerebro.addanalyzer(bt.analyzers.DrawDown,    _name='drawdown')
    cerebro.addanalyzer(bt.analyzers.Returns,     _name='returns')
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')

    results   = cerebro.run()
    strat     = results[0]
    final_val = cerebro.broker.getvalue()

    # 收集分析结果
    sharpe   = strat.analyzers.sharpe.get_analysis().get('sharperatio') or 0.0
    drawdown = strat.analyzers.drawdown.get_analysis().get('max', {}).get('drawdown', 0.0)
    total_ret = (final_val - cash) / cash * 100

    trades_info = strat.analyzers.trades.get_analysis()
    total_closed = trades_info.get('total', {}).get('closed', 0)
    won          = trades_info.get('won',   {}).get('total',  0)
    win_rate     = (won / total_closed * 100) if total_closed else 0.0

    log.info(
        '[%s] %s | 总收益率: %.2f%% | 夏普: %.3f | 最大回撤: %.2f%% | '
        '胜率: %.1f%% (%d/%d)',
        'AI过滤' if use_ai else '纯量化',
        symbol,
        total_ret, sharpe, drawdown,
        win_rate, won, total_closed,
    )

    return cerebro, strat.trade_logs, final_val


# =============================================================================
# 5. 可视化
# =============================================================================

def plot_comparison(
    df: pd.DataFrame,
    logs_quant: list[dict],
    logs_ai:    list[dict],
    symbol:     str,
    output:     str = 'backtest_result.png',
) -> None:
    """
    绘制三合一对比图：
      ① K 线 + 均线 + 交易标记（纯量化 vs AI 过滤）
      ② 累计收益率曲线
      ③ 胜率 / 交易次数对比柱状图
    """
    dates  = pd.to_datetime(df['datetime'])
    closes = df['close'].values

    fig, axes = plt.subplots(3, 1, figsize=(14, 14))
    fig.suptitle(f'Sentinel-A 回测分析 — {symbol}', fontsize=14, fontweight='bold')

    # ── 子图 1：价格 + 交易标记 ──────────────────────────────────────────────
    ax1 = axes[0]
    ax1.plot(dates, closes, color='#1a1a2e', linewidth=1, label='收盘价')
    sma = pd.Series(closes).rolling(20).mean().values
    ax1.plot(dates, sma, color='#e94560', linewidth=1, linestyle='--', label='MA20')

    def _mark_trades(logs: list[dict], color: str, marker: str, label: str, offset: float = 0):
        buy_dates, buy_prices = [], []
        for rec in logs:
            if rec['type'] == 'signal' and rec.get('ai_verdict', 'BUY') == 'BUY':
                try:
                    d = pd.Timestamp(rec['date'])
                    buy_dates.append(d)
                    buy_prices.append(rec['price'] * (1 + offset))
                except Exception:
                    pass
        if buy_dates:
            ax1.scatter(buy_dates, buy_prices, color=color, marker=marker,
                        s=80, zorder=5, label=label)

    _mark_trades(logs_quant, '#0099cc', '^', '纯量化买入', -0.01)
    _mark_trades(logs_ai,    '#ff6600', '^', 'AI过滤买入',  0.01)
    ax1.set_ylabel('价格 (元)')
    ax1.legend(loc='upper left', fontsize=8)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    ax1.grid(alpha=0.3)

    # ── 子图 2：累计收益率曲线 ────────────────────────────────────────────────
    ax2 = axes[1]

    def _cumret(logs: list[dict], label: str, color: str):
        closed = [r for r in logs if r['PnL'] != 0]
        if not closed:
            return
        cum = np.cumprod([1 + r['PnL'] / 100 for r in closed]) - 1
        x   = list(range(1, len(cum) + 1))
        ax2.plot(x, cum * 100, color=color, linewidth=1.5, label=label, marker='o', markersize=3)

    _cumret(logs_quant, '纯量化', '#0099cc')
    _cumret(logs_ai,    'AI过滤', '#ff6600')
    ax2.axhline(0, color='grey', linestyle='--', linewidth=0.8)
    ax2.set_xlabel('交易次数')
    ax2.set_ylabel('累计收益率 (%)')
    ax2.legend()
    ax2.grid(alpha=0.3)

    # ── 子图 3：胜率 + 交易次数对比 ───────────────────────────────────────────
    ax3 = axes[2]

    def _stats(logs: list[dict]) -> tuple[float, int]:
        closed = [r for r in logs if r['PnL'] != 0]
        if not closed:
            return 0.0, 0
        wins = sum(1 for r in closed if r['PnL'] > 0)
        return wins / len(closed) * 100, len(closed)

    wr_q, cnt_q = _stats(logs_quant)
    wr_a, cnt_a = _stats(logs_ai)

    x     = np.arange(2)
    width = 0.35
    bars1 = ax3.bar(x - width / 2, [wr_q, wr_a],  width, label='胜率 (%)',   color=['#0099cc', '#ff6600'])
    ax3_r = ax3.twinx()
    bars2 = ax3_r.bar(x + width / 2, [cnt_q, cnt_a], width, label='交易次数', color=['#aaccee', '#ffcc99'])

    ax3.set_xticks(x)
    ax3.set_xticklabels(['纯量化', 'AI过滤'])
    ax3.set_ylabel('胜率 (%)')
    ax3_r.set_ylabel('交易次数')

    for bar, val in zip(bars1, [wr_q, wr_a]):
        ax3.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                 f'{val:.1f}%', ha='center', va='bottom', fontsize=9)
    for bar, val in zip(bars2, [cnt_q, cnt_a]):
        ax3_r.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                   str(val), ha='center', va='bottom', fontsize=9)

    lines = [bars1, bars2]
    labels = ['胜率 (%)', '交易次数']
    ax3.legend(lines, labels, loc='upper right')
    ax3.grid(alpha=0.3)

    plt.tight_layout()
    plt.savefig(output, dpi=150, bbox_inches='tight')
    log.info('回测图表已保存至: %s', output)


# =============================================================================
# 6. 主入口
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(description='Sentinel-A 离线回测引擎')
    p.add_argument('--symbol', default=DEFAULT_SYMBOL, help='A 股代码，如 600519')
    p.add_argument('--start',  default=DEFAULT_START,  help='开始日期 YYYYMMDD')
    p.add_argument('--end',    default=DEFAULT_END,    help='结束日期 YYYYMMDD')
    p.add_argument('--cash',   default=DEFAULT_CASH,   type=float, help='初始资金')
    p.add_argument('--output', default='backtest_result.png', help='图表输出路径')
    p.add_argument('--csv',    default=None, help='从 CSV 文件载入（列名同 akshare）')
    return p.parse_args()


def main():
    args = parse_args()

    # 1. 载入数据
    if args.csv:
        log.info('从 CSV 载入数据: %s', args.csv)
        df = pd.read_csv(args.csv)
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.sort_values('datetime').reset_index(drop=True)
        if 'openinterest' not in df.columns:
            df['openinterest'] = 0
    else:
        df = fetch_stock_data(args.symbol, args.start, args.end)

    if df.empty or len(df) < 25:
        log.error('数据不足（%d 行），至少需要 25 条 K 线。', len(df))
        sys.exit(1)

    log.info('数据加载完成: %d 条 K 线 [%s → %s]',
             len(df), df['datetime'].iloc[0].date(), df['datetime'].iloc[-1].date())

    # 2. 纯量化回测
    log.info('── 运行纯量化策略 ──')
    _, logs_quant, val_quant = run_backtest(df, args.symbol, use_ai=False, cash=args.cash)

    # 3. AI 过滤回测
    log.info('── 运行 AI 过滤策略 ──')
    _, logs_ai, val_ai = run_backtest(df, args.symbol, use_ai=bool(ARK_API_KEY), cash=args.cash)

    # 4. 结果汇总
    print('\n' + '=' * 60)
    print(f'  Sentinel-A 回测结果汇总 — {args.symbol}')
    print('=' * 60)
    ret_q = (val_quant - args.cash) / args.cash * 100
    ret_a = (val_ai   - args.cash) / args.cash * 100
    print(f'  纯量化 → 最终市值: {val_quant:,.2f}  总收益率: {ret_q:+.2f}%')
    print(f'  AI过滤 → 最终市值: {val_ai:,.2f}  总收益率: {ret_a:+.2f}%')
    print('=' * 60 + '\n')

    # 5. 可视化
    plot_comparison(df, logs_quant, logs_ai, args.symbol, output=args.output)

    # 6. 保存详细日志
    log_path = args.output.replace('.png', '_trades.json')
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump({'quant': logs_quant, 'ai': logs_ai}, f, ensure_ascii=False, indent=2)
    log.info('交易日志已保存至: %s', log_path)


if __name__ == '__main__':
    main()
