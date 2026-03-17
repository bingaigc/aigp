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

# ── OpenClaw 频道绑定（可通过环境变量指定每个 Agent 的专属频道 ID） ───────────────
# 全局覆盖：所有 Agent 消息统一推送至此频道（优先级最高）
GLOBAL_CHANNEL_ID = os.environ.get('OPENCLAW_CHANNEL_ID')

# 各 Agent 专属频道 ID（未设则 Skill 使用 agentId 路由 + 广播兜底）
AGENT_CHANNEL_IDS = {
    'sentinel-a': os.environ.get('SENTINEL_A_CHANNEL_ID'),
    'analyst-b':  os.environ.get('ANALYST_B_CHANNEL_ID'),
    'guardian-c': os.environ.get('GUARDIAN_C_CHANNEL_ID'),
    'scout-d':    os.environ.get('SCOUT_D_CHANNEL_ID'),
}

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

# ── 银河战舰：中转网关（MODEL_GATEWAY_URL 设置后统一接管所有模型访问） ────────────
# 将 OpenClaw 的 primary provider 指向本地网关，网关再路由到 NVIDIA NIM / HF / ARK
_gateway_url     = os.environ.get('MODEL_GATEWAY_URL', '')
_nvidia_api_key  = os.environ.get('NVIDIA_API_KEY', '')
_hf_api_key      = os.environ.get('HF_API_KEY', '')

if _gateway_url:
    providers = config.setdefault('models', {}).setdefault('providers', {})
    providers['galaxy-gateway'] = {
        'api':     'openai-completions',
        'baseUrl': _gateway_url,
        'apiKey':  'gateway',
        'models':  [
            # ── 银河战舰核心舰队（NVIDIA NIM，均已验证可用）──────────────────
            # 综合推理旗舰（Sentinel-A 复盘战报，推荐）
            {'id': 'nvidia/llama-3.3-nemotron-super-49b-v1',         'name': '🦅 Nemotron-Super-49B (旗舰推理)'},
            {'id': 'nvidia/llama-3.1-nemotron-ultra-253b-v1',        'name': '🦅 Nemotron-Ultra-253B (超强推理)'},
            # 量化分析（Analyst-B 板块轮动，推荐）
            {'id': 'qwen/qwen2.5-coder-32b-instruct',                'name': '📊 Qwen2.5-Coder-32B (量化分析)'},
            {'id': 'qwen/qwq-32b',                                   'name': '📊 QwQ-32B (深度推理)'},
            {'id': 'deepseek-ai/deepseek-r1-distill-qwen-32b',       'name': '📊 DeepSeek-R1-Qwen32B (蒸馏推理)'},
            # 风险评估（Guardian-C 低延迟，推荐）
            {'id': 'meta/llama-3.1-8b-instruct',                     'name': '🛡️ Llama-3.1-8B (低延迟风控)'},
            {'id': 'microsoft/phi-4-mini-instruct',                  'name': '🛡️ Phi-4-Mini (轻量风控)'},
            # 实时侦察（Scout-D 超低延迟，推荐）
            {'id': 'nvidia/nemotron-mini-4b-instruct',               'name': '🔭 Nemotron-Mini-4B (极速侦察)'},
            {'id': 'meta/llama-3.2-3b-instruct',                     'name': '🔭 Llama-3.2-3B (超快侦察)'},
            # 中文优化模型（可替换任意 Agent）
            {'id': 'qwen/qwen2.5-7b-instruct',                       'name': '🇨🇳 Qwen2.5-7B (中文优化)'},
            {'id': 'deepseek-ai/deepseek-r1-distill-llama-8b',       'name': '🇨🇳 DeepSeek-R1-8B (中文推理)'},
            # 其他可选成员
            {'id': 'meta/llama-4-maverick-17b-128e-instruct',        'name': 'Llama-4-Maverick-17B'},
            {'id': 'mistralai/mistral-small-3.1-24b-instruct-2503',  'name': 'Mistral-Small-3.1-24B'},
            {'id': 'google/gemma-3-27b-it',                          'name': 'Gemma-3-27B'},
        ],
    }
    # 当网关启动时，将 OpenClaw 自身的对话主模型也切换到网关
    (config
        .setdefault('agents', {})
        .setdefault('defaults', {})
        .setdefault('model', {})
    )['primary'] = 'galaxy-gateway/nvidia/llama-3.3-nemotron-super-49b-v1'
    print(f"[config] 银河战舰网关已配置，baseUrl={_gateway_url}，接管 OpenClaw 对话模型")

elif _nvidia_api_key:
    # 未启动本地网关但有 NVIDIA Key：直接注册 NVIDIA NIM 作为第二 provider
    providers = config.setdefault('models', {}).setdefault('providers', {})
    providers['nvidia-nim'] = {
        'api':     'openai-completions',
        'baseUrl': 'https://integrate.api.nvidia.com/v1',
        'apiKey':  _nvidia_api_key,
        'models':  [
            {'id': 'nvidia/llama-3.3-nemotron-super-49b-v1',    'name': '🦅 Nemotron-Super-49B'},
            {'id': 'nvidia/nemotron-mini-4b-instruct',           'name': '🔭 Nemotron-Mini-4B'},
            {'id': 'meta/llama-3.3-70b-instruct',                'name': 'Llama-3.3-70B'},
            {'id': 'qwen/qwen2.5-coder-32b-instruct',            'name': '📊 Qwen2.5-Coder-32B'},
            {'id': 'deepseek-ai/deepseek-r1-distill-qwen-32b',   'name': '📊 DeepSeek-R1-Qwen32B'},
            {'id': 'meta/llama-3.1-8b-instruct',                 'name': '🛡️ Llama-3.1-8B'},
            {'id': 'mistralai/mistral-small-3.1-24b-instruct-2503', 'name': 'Mistral-Small-24B'},
        ],
    }
    print(f"[config] NVIDIA NIM 直连已配置（无本地网关模式）")

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
    # 写入专属频道绑定（让 OpenClaw 知道消息应推送到哪个频道）
    resolved_channel_id = (
        GLOBAL_CHANNEL_ID
        or AGENT_CHANNEL_IDS.get(agent['id'])
    )
    if resolved_channel_id:
        entry['channelId'] = resolved_channel_id
        print(f"[config] {agent['name']} 绑定频道: {resolved_channel_id}")
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
