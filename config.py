"""
OpenClaw 启动配置脚本
运行时读取环境变量，写入 /root/.openclaw/openclaw.json

多智能体（AI 员工）注册：
  - Sentinel-A  🦅  主控哨兵    — 每日复盘 + 实盘预警
  - Analyst-B   📊  量化分析师  — 板块轮动 + 主线挖掘
  - Guardian-C  🛡️  风控守卫    — 实时风险评估 + 仓位管理
  - Scout-D     🔭  游骑侦察    — 盘前/盘中/尾盘三段狙击
"""
import os
import json

CONFIG_PATH   = '/root/.openclaw/openclaw.json'
PERSONA_DIR   = '/root/.openclaw/personas'
ALERTS_CHANNEL = 'OPENCLAW_ALERTS'

# ── 多智能体定义表 ───────────────────────────────────────────────────────────
AGENTS = [
    {
        'id':          'sentinel-a',
        'name':        'Sentinel-A 🦅',
        'description': '主控哨兵 — 每日收盘复盘战报、实盘预警、市场情绪总指挥',
        'persona':     f'{PERSONA_DIR}/Sentinel-A.md',
        'icon':        '🦅',
        'schedule':    '工作日 16:30 自动触发',
        'channel':     ALERTS_CHANNEL,
    },
    {
        'id':          'analyst-b',
        'name':        'Analyst-B 📊',
        'description': '量化分析师 — 盘中每小时板块轮动扫描，识别主升/衰退板块',
        'persona':     f'{PERSONA_DIR}/Analyst-B.md',
        'icon':        '📊',
        'schedule':    '09:30 / 10:30 / 11:00 / 13:30 / 14:30',
        'channel':     ALERTS_CHANNEL,
    },
    {
        'id':          'guardian-c',
        'name':        'Guardian-C 🛡️',
        'description': '风控守卫 — 每 5 分钟评估大盘风险系数，触发熔断预警',
        'persona':     f'{PERSONA_DIR}/Guardian-C.md',
        'icon':        '🛡️',
        'schedule':    '盘中每 5 分钟',
        'channel':     ALERTS_CHANNEL,
    },
    {
        'id':          'scout-d',
        'name':        'Scout-D 🔭',
        'description': '游骑侦察 — 盘前(09:15)/上午(11:30)/尾盘(14:30) 三段侦察，狙击高质量涨停板',
        'persona':     f'{PERSONA_DIR}/Scout-D.md',
        'icon':        '🔭',
        'schedule':    '09:15 / 11:30 / 14:30',
        'channel':     ALERTS_CHANNEL,
    },
]

# ── 读取现有配置（如有） ──────────────────────────────────────────────────────
config: dict = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}

# 清除内置 skills，让外部 skill 完全接管
config.pop('skills', None)

# ── 网关配置 ─────────────────────────────────────────────────────────────────
gateway_token = os.environ.get('GATEWAY_TOKEN', 'OpenClaw_Secure_2026!')
config.setdefault('gateway', {}).update({
    'mode': 'local',
    'bind': 'lan',
    'port': 7861,
    'auth': {'mode': 'token', 'token': gateway_token},
})
config['gateway'].setdefault('controlUi', {}).update({'enabled': True})

# ── DeepSeek / Volcengine 模型接入 ───────────────────────────────────────────
ark_api_key = os.environ.get('ARK_API_KEY')
model_id    = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
if ark_api_key:
    providers = config.setdefault('models', {}).setdefault('providers', {})
    providers['volcengine'] = {
        'api':     'openai-completions',
        'baseUrl': 'https://ark.cn-beijing.volces.com/api/v3',
        'apiKey':  ark_api_key,
        'models':  [{'id': model_id, 'name': 'DeepSeek-R1/V3 (Sentinel)'}],
    }
    (config
        .setdefault('agents', {})
        .setdefault('defaults', {})
        .setdefault('model', {})
    )['primary'] = f'volcengine/{model_id}'
    print(f"[config] Volcengine 模型已配置: {model_id}")
else:
    print("[config] 警告: ARK_API_KEY 未设置，AI 分析功能将不可用。")

# ── 多智能体注册 ─────────────────────────────────────────────────────────────
agent_registry = config.setdefault('agents', {}).setdefault('registry', [])

# 清空旧注册表，重新写入（幂等）
agent_registry.clear()
for agent in AGENTS:
    entry = {
        'id':          agent['id'],
        'name':        agent['name'],
        'description': agent['description'],
        'icon':        agent['icon'],
        'schedule':    agent['schedule'],
        'channel':     agent['channel'],
    }
    # 若 persona 文件存在，写入引用
    if os.path.exists(agent['persona']):
        entry['persona'] = agent['persona']
    agent_registry.append(entry)
    print(f"[config] 注册 AI 员工: {agent['name']}")

print(f"[config] 共注册 {len(agent_registry)} 个 AI 智能体")

# ── 写入配置 ─────────────────────────────────────────────────────────────────
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
with open(CONFIG_PATH, 'w') as f:
    json.dump(config, f, indent=4, ensure_ascii=False)
print(f"[config] 配置已写入 {CONFIG_PATH}")
