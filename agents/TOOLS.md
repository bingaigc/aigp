# TOOLS — Sentinel-A 工具箱完整手册

> 工具是哨兵的武器。本文档完整描述 Sentinel-A 可调用的所有工具、数据源和 API 接口。

---

## 1. 工具总览

```
┌─────────────────────────────────────────────────────────────────┐
│                   Sentinel-A 工具箱                              │
│                                                                  │
│  📊 数据采集层        🧠 AI 推理层          ⚙️ 执行层           │
│  ─────────────        ───────────          ───────             │
│  akshare             DeepSeek-R1/V3        Redis Pub/Sub        │
│  涨停池              链式推理               OPENCLAW_ALERTS      │
│  龙虎榜              时光机过滤             心跳管理             │
│  板块轮动            报告生成               状态机               │
│                                                                  │
│  📈 回测层            🖥️ 前端层            🔧 运维层             │
│  ─────────            ─────────           ─────────            │
│  backtrader          OpenClaw SDK          ttyd Web 终端         │
│  SentinelStrategy    chat.broadcast()      Nginx 反向代理        │
│  Matplotlib          session:inject        Docker HEALTHCHECK    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 数据采集工具（akshare）

### 2.1 涨停池（核心情绪指标）
```python
import akshare as ak

# 当日涨停池（东方财富）
df = ak.stock_zt_pool_em(date='20260316')
# 返回字段: 代码, 名称, 涨跌幅, 最新价, 成交额, 流通市值,
#           总市值, 换手率, 封板资金, 首次封板时间, 最后封板时间,
#           炸板次数, 涨停统计, 连板数, 所属行业

# 昨日涨停今日表现（检验梯队延续性）
df_yesterday = ak.stock_zt_pool_previous_em(date='20260316')
```

### 2.2 龙虎榜（机构资金动向）
```python
# 龙虎榜详情（净买入排序）
df = ak.stock_lhb_detail_em(start_date='20260316', end_date='20260316')
# 关键字段: 代码, 名称, 净买额, 买入总额, 卖出总额

# 龙虎榜游资排行
df_yizi = ak.stock_lhb_jgzz_em(start_date='20260316', end_date='20260316')
```

### 2.3 板块轮动（题材发现）
```python
# 概念板块实时排名（动态，非固定板块）
df = ak.stock_board_concept_name_em()
# 按涨跌幅排序，取前五

# 行业板块排名
df_industry = ak.stock_board_industry_name_em()
```

### 2.4 历史 K 线（回测数据源）
```python
# 前复权日 K 线（Backtrader 友好格式）
df = ak.stock_zh_a_hist(
    symbol='000001',      # 股票代码（不带市场前缀）
    period='daily',       # daily / weekly / monthly
    start_date='20220101',
    end_date='20241231',
    adjust='qfq',         # qfq=前复权, hfq=后复权, ''=不复权
)
```

### 2.5 资金流向（大单追踪）
```python
# 个股资金流向
df = ak.stock_individual_fund_flow(stock='000001', market='sz')

# 北向资金（外资动向）
df_north = ak.stock_hsgt_north_net_flow_in_em(start_date='20260301', end_date='20260316')
```

### 2.6 宏观数据（中期判断参考）
```python
# 上证指数历史数据
df_index = ak.stock_zh_index_daily(symbol='sh000001')

# A 股市场情绪指数（VIX 类似物）
df_sentiment = ak.stock_a_pe()
```

---

## 3. AI 推理工具（DeepSeek via Volcengine ARK）

### 3.1 标准调用（复盘报告生成）
```python
from openai import Client

client = Client(
    api_key=os.environ['ARK_API_KEY'],
    base_url='https://ark.cn-beijing.volces.com/api/v3'
)

response = client.chat.completions.create(
    model='ep-20260312230909-pskjv',  # DeepSeek-R1/V3 Endpoint
    messages=[
        {'role': 'system', 'content': '你是 Sentinel-A，冷血量化复盘机器。'},
        {'role': 'user',   'content': prompt}
    ],
    temperature=0.2,   # 低温度 = 更确定性的输出
    max_tokens=2048,
)
report = response.choices[0].message.content
```

### 3.2 时光机过滤器调用（信号审核）
```python
response = client.chat.completions.create(
    model='ep-20260312230909-pskjv',
    messages=[
        {'role': 'system', 'content': '你是冷血量化机器，判断突破信号真伪。'},
        {'role': 'user',   'content':
            f'时间定格在 {date_str}，股票 {symbol} 触发20日均线突破。'
            '仅回答 [买入] 或 [否决]，不要解释。'
        }
    ],
    temperature=0.1,   # 极低温度 = 最确定性判断
    max_tokens=10,
)
verdict = '买入' if '[买入]' in response.choices[0].message.content else '否决'
```

### 3.3 Prompt 工程模板库

#### 每日复盘 Prompt
```
今日日期: {date_str}
情绪指标: {sentiment_json}
龙虎榜核心买入(Top5): {lhb_json}

请以严格的 Markdown 格式生成一份冰冷的 A 股复盘简报，内容包含：
①情绪周期判断（亢奋/平淡/恐慌）
②主线题材梳理
③连板梯队健康度
④明日操作纪律（含防守止损线）
禁止主观情绪，只信仰数据。
```

#### 个股诊断 Prompt
```
股票代码: {symbol}  日期: {date_str}
当前价格: {price}  MA20: {sma20}  成交量: {volume}
请判断该标的当前技术形态，给出风险评级（1-10）和操作建议（买入/持有/减仓/清仓）。
必须附带止损线。
```

---

## 4. Redis 状态机工具

### 4.1 消息总线（Pub/Sub）
```python
import redis, json

r = redis.from_url('redis://127.0.0.1:6379', decode_responses=True)

# 发布消息
r.publish('OPENCLAW_ALERTS', json.dumps({
    'type': '每日战报',
    'content': report_markdown,
    'timestamp': datetime.datetime.now().isoformat(),
    'source': 'Sentinel-A',
    'level': 'info'
}, ensure_ascii=False))

# 订阅消息（JavaScript 侧）
# await subscriber.subscribe('OPENCLAW_ALERTS', callback)
```

### 4.2 状态存储
```python
# 心跳（TTL=60s，每5分钟刷新）
r.setex('sentinel:heartbeat', 60, datetime.datetime.now().isoformat())

# 状态哈希
r.hset('sentinel:status', mapping={
    'started_at':    start_time,
    'version':       'v2.0',
    'mode':          'online' if ARK_API_KEY else 'offline',
    'error_count':   0,
})

# 复盘记录
r.set('sentinel:last_report', date_str)
r.incr('sentinel:report_count')
```

---

## 5. 回测工具（backtrader + Analyst-B）

### 5.1 命令行调用
```bash
# 基础回测（akshare 数据源）
python /app/backtest_engine.py \
  --symbol 600519 \
  --start  20230101 \
  --end    20241231 \
  --cash   100000

# CSV 数据源
python /app/backtest_engine.py --csv /data/my_stock.csv

# 完整参数
python /app/backtest_engine.py \
  --symbol 000001 --start 20220101 --end 20241231 \
  --cash 500000 --output /tmp/result.png
```

### 5.2 策略参数调优
```python
# 在 backtest_engine.py 中修改默认参数
DEFAULT_COMMISSION = 0.0003   # 万三（可改为万一）
DEFAULT_SLIPPAGE   = 0.001    # 0.1%（可改为 0.0005）

# 策略参数
class SentinelBaseStrategy(bt.Strategy):
    params = (
        ('ma_period',     20),    # 可改为 5/10/60
        ('stop_loss_pct', 0.08),  # 可改为 0.05/0.10
        ('ai_filter',     True),  # 是否启用 AI 过滤
    )
```

---

## 6. OpenClaw 前端工具

### 6.1 消息注入接口
```javascript
// 方式一：chat.broadcast（优先）
app.chat.broadcast({ role: 'assistant', content: markdownContent });

// 方式二：session 事件注入（备选）
app.emit('session:message:inject', { role: 'assistant', content: markdownContent });
```

### 6.2 Skill 注册格式
```json
// manifest.json
{
  "name": "sentinel-alert",
  "version": "1.1.0",
  "description": "Sentinel-A 底层心跳与战报监听器",
  "permissions": ["chat:write", "system:events"]
}
```

---

## 7. 运维工具（ttyd + Docker）

### 7.1 Web 终端访问
```
URL: http://服务器IP:7860/term/
功能: 直接访问容器内 bash，可执行任意命令
安全: 通过 Nginx 反向代理，与 OpenClaw 共享端口
```

### 7.2 常用运维命令（在 ttyd 中执行）
```bash
# 查看所有进程
ps aux | grep -E 'python|node|redis|nginx'

# 手动触发复盘
python3 -c "from sentinel_daemon import generate_daily_report; generate_daily_report()"

# 检查 Redis 状态
redis-cli INFO server | grep -E 'version|uptime|connected'

# 查看 OpenClaw 日志
journalctl -u openclaw 2>/dev/null || tail -f /var/log/openclaw.log

# 测试 DeepSeek API 连通性
python3 -c "
import os
from openai import Client
c = Client(api_key=os.environ['ARK_API_KEY'], base_url='https://ark.cn-beijing.volces.com/api/v3')
r = c.chat.completions.create(model=os.environ.get('ARK_MODEL_ID','ep-20260312230909-pskjv'), messages=[{'role':'user','content':'ping'}], max_tokens=5)
print('OK:', r.choices[0].message.content)
"
```
