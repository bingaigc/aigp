"""
OpenClaw 启动配置脚本
运行时读取环境变量，写入 /root/.openclaw/openclaw.json
"""
import os
import json

CONFIG_PATH = '/root/.openclaw/openclaw.json'

# 读取现有配置（如有）
config: dict = {}
if os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH) as f:
        try:
            config = json.load(f)
        except json.JSONDecodeError:
            config = {}

# 清除内置 skills，让外部 skill 完全接管
config.pop('skills', None)

# 网关配置
gateway_token = os.environ.get('GATEWAY_TOKEN', 'OpenClaw_Secure_2026!')
config.setdefault('gateway', {}).update({
    'mode': 'local',
    'bind': 'lan',
    'port': 7861,
    'auth': {'mode': 'token', 'token': gateway_token},
})
config['gateway'].setdefault('controlUi', {}).update({'enabled': True})

# DeepSeek / Volcengine 模型接入（仅当 ARK_API_KEY 存在时）
ark_api_key = os.environ.get('ARK_API_KEY')
model_id = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
if ark_api_key:
    providers = config.setdefault('models', {}).setdefault('providers', {})
    providers['volcengine'] = {
        'api': 'openai-completions',
        'baseUrl': 'https://ark.cn-beijing.volces.com/api/v3',
        'apiKey': ark_api_key,
        'models': [{'id': model_id, 'name': 'DeepSeek-R1/V3 (Sentinel)'}],
    }
    (config
        .setdefault('agents', {})
        .setdefault('defaults', {})
        .setdefault('model', {})
    )['primary'] = f'volcengine/{model_id}'
    print(f"[config] Volcengine 模型已配置: {model_id}")
else:
    print("[config] 警告: ARK_API_KEY 未设置，AI 分析功能将不可用。")

os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
with open(CONFIG_PATH, 'w') as f:
    json.dump(config, f, indent=4, ensure_ascii=False)
print(f"[config] 配置已写入 {CONFIG_PATH}")
