# USER — 用户画像与交互指南

> 本文档定义与 Sentinel-A 交互的用户画像、使用场景和最佳实践。
> 读懂用户，才能更好地服务用户。

---

## 1. 目标用户画像

### 主要用户：极客型量化投资者

```
典型特征:
  ✦ 有编程能力（Python / Shell / Node.js）
  ✦ 对 A 股市场有 3 年以上实战经验
  ✦ 理解基本的量化交易概念（均线、止损、回测）
  ✦ 追求系统化、数据驱动的决策，厌恶情绪化操作
  ✦ 愿意在 Linux 云服务器上部署和维护量化系统
  ✦ 有一定的风险承受能力，资金体量 10-500 万

需求痛点:
  ✦ 无法 7×24 盯盘，需要系统自动监控和预警
  ✦ 情绪控制困难，需要冷血的 AI 帮助执行纪律
  ✦ 数据获取繁琐，需要统一的数据抓取和分析平台
  ✦ 回测验证成本高，需要快速验证策略有效性
```

### 次要用户：研究型量化爱好者

```
典型特征:
  ✦ 学习量化投资中，有数学或 CS 背景
  ✦ 更关注系统学习和策略探索，资金体量较小
  ✦ 需要完整的教学示例和可运行的代码

核心需求:
  ✦ 可运行的回测引擎示例
  ✦ A 股数据抓取的最佳实践
  ✦ AI 辅助投资的工程实现参考
```

---

## 2. 用户交互入口

| 入口 | URL | 适合场景 |
|------|-----|----------|
| **OpenClaw 主界面** | `http://IP:7860/` | 日常查看复盘战报、与 AI 对话 |
| **Web 终端** | `http://IP:7860/term/` | 执行命令、调试、查看日志 |
| **命令行回测** | `docker exec` / 本地 | 策略开发与历史数据验证 |
| **Redis 监听** | `redis-cli SUBSCRIBE` | 高级用户实时监控消息总线 |

---

## 3. 与 Sentinel-A 的对话场景

### 场景一：每日复盘（全自动，无需干预）
```
触发: 工作日 16:30 自动触发
流程: 守护进程抓数据 → DeepSeek 生成报告 → Redis 广播 → OpenClaw 弹窗
用户操作: 查看 OpenClaw 弹窗中的 Markdown 战报即可
```

### 场景二：主动提问（OpenClaw 对话）
```
用户: "今天 000001 涨了 3%，突破了 20 日线，我应该追吗？"
Sentinel-A: 自动调用工具评估信号，给出 [买入]/[否决] 及止损线

用户: "最近涨停板数量怎么样？情绪如何？"
Sentinel-A: 调用 akshare 获取最新涨停池数据，输出情绪温度计

用户: "帮我回测 600519 过去两年的 20 日线策略"
Sentinel-A: 调用 backtest_engine 生成回测图表并展示结果
```

### 场景三：系统配置（Web 终端）
```bash
# 修改复盘时间
docker stop sentinel
docker run -d ... -e REPORT_TIME="15:00" ...

# 手动触发复盘（测试 AI 连通性）
docker exec -it sentinel python3 -c "
from sentinel_daemon import generate_daily_report
generate_daily_report()
"

# 查看实时消息总线
docker exec -it sentinel redis-cli SUBSCRIBE OPENCLAW_ALERTS
```

### 场景四：策略回测（研究模式）
```bash
# 基础回测
python backtest_engine.py --symbol 600519 --start 20230101 --end 20241231

# 高仓位 + AI 过滤
export ARK_API_KEY="your_key"
python backtest_engine.py --symbol 000001 --start 20220101 --end 20241231 --cash 500000

# 查看结果
open backtest_result.png              # 查看对比图表
cat backtest_result_trades.json | jq  # 查看详细交易日志
```

---

## 4. 用户常见问题 FAQ

### Q1: 没有 ARK_API_KEY，系统还能用吗？
```
A: 可以。系统会自动降级为"离线模式"：
   - 复盘战报仍然生成，但不含 AI 深度推理，只有原始数据
   - 回测引擎的 AI 时光机过滤器自动跳过（视为全部通过）
   - 所有其他功能正常运行
```

### Q2: 复盘战报时间可以自定义吗？
```
A: 可以。启动时设置环境变量 REPORT_TIME（24h 制）：
   docker run ... -e REPORT_TIME="15:00" ...  # 提前到 15:00 触发
```

### Q3: 如何添加自己的量化策略？
```
A: 编辑 backtest_engine.py，创建继承自 bt.Strategy 的新策略类，
   然后在 run_backtest() 中替换 addstrategy() 的参数即可。
   参考 SentinelBaseStrategy 的实现。
```

### Q4: 如何监控系统是否正在运行？
```
A: 多种方式:
   1. Docker: docker inspect --format='{{.State.Health.Status}}' sentinel
   2. Redis:  docker exec sentinel redis-cli GET sentinel:heartbeat
   3. URL:    curl -sf http://localhost:7860 && echo "OK"
   4. OpenClaw: 若守护进程异常，sentinel-alert skill 会自动推送警告弹窗
```

### Q5: 想要实盘自动交易（下单）怎么做？
```
A: 当前版本为监控和分析系统，不含自动下单功能。
   如需接入券商 API（如东方财富、国泰君安），可在 sentinel_daemon.py
   的 generate_daily_report() 函数执行后，根据信号调用券商 SDK 下单。
   请确保：① 做好风控二次确认；② 严格执行止损；③ 从小仓位开始测试。
```

---

## 5. 用户操作纪律（人机协作守则）

作为使用 Sentinel-A 的用户，以下守则有助于你从系统中获得最大价值：

```
守则一: 信任数据，质疑感觉
         当你的直觉与 Sentinel-A 的分析冲突时，先听系统的，再反思自己的。

守则二: 严格执行止损
         止损线是 Sentinel-A 给出的边界，不要因为"感觉会反弹"而放弃止损。

守则三: 仓位纪律先于收益
         在系统运行初期，单只个股仓位不超过 20%，先验证系统有效性。

守则四: 回测不等于实盘
         回测结果优秀不代表实盘一定盈利。用回测验证逻辑，用小仓位验证实盘。

守则五: 定期审计日志
         每周查看一次 backtest_result_trades.json，了解系统的决策模式。

守则六: 不依赖单一信号
         AI 过滤器给出 [买入] 信号后，仍需结合当日大盘情绪进行最终判断。
```

---

## 6. 系统升级与反馈

### 自定义 Sentinel-A 的行为
```bash
# 修改 AI 人格（在 OpenClaw 中）
vim /root/.openclaw/personas/Sentinel-A.md

# 修改复盘报告的 Prompt 模板
vim /app/sentinel_daemon.py  # 编辑 generate_daily_report() 中的 prompt

# 修改回测策略参数
vim /app/backtest_engine.py  # 编辑 SentinelBaseStrategy.params
```

### 向 OpenClaw 提问的建议格式
```
格式: [场景] + [具体问题] + [你的初步判断]

示例一: "今天市场：涨停 85 个，最高连板 7 板。我认为情绪处于亢奋期，请确认并给出明日操作建议。"

示例二: "600519 今日放量突破年线，我想追，请分析风险并给出止损线。"

示例三: "请生成 000001 最近一年的 20 日线策略回测报告，并与 AI 过滤版本对比。"
```
