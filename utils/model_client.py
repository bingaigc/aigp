"""
银河战舰共享客户端工厂 (Shared Model Client Factory)
====================================================
各 AI 员工守护进程共用的 OpenAI 客户端初始化逻辑：
  路由优先级：中转网关 > Volcengine ARK > 离线模式

使用方法：
    from utils.model_client import make_ai_client
    ai_client, MODEL_ID = make_ai_client(
        agent_model_env='SENTINEL_A_MODEL',
        default_model='nvidia/llama-3.3-nemotron-super-49b-v1',
        logger=log,
    )
"""

import logging
import os
from typing import Optional

try:
    from openai import Client
except ImportError:  # 单元测试环境下 openai 可能未安装
    Client = None  # type: ignore[assignment,misc]

ARK_BASE_URL = 'https://ark.cn-beijing.volces.com/api/v3'


def make_ai_client(
    agent_model_env: str,
    default_model: str,
    logger: Optional[logging.Logger] = None,
) -> 'tuple[Client | None, str]':
    """
    根据环境变量选择 AI 客户端和模型。

    Args:
        agent_model_env: 专属模型环境变量名（如 'SENTINEL_A_MODEL'）
        default_model:   未设置环境变量时的默认 NVIDIA NIM 模型
        logger:          可选日志器；未提供时使用模块级 logger

    Returns:
        (ai_client, model_id)
        ai_client 为 None 表示离线模式（AI 推理不可用）
    """
    log = logger or logging.getLogger('model_client')

    ark_api_key       = os.environ.get('ARK_API_KEY', '')
    ark_model_id      = os.environ.get('ARK_MODEL_ID', 'ep-20260312230909-pskjv')
    model_gateway_url = os.environ.get('MODEL_GATEWAY_URL', '')
    agent_model       = os.environ.get(agent_model_env, default_model)

    if Client is None:
        log.error('openai 包未安装，AI 功能不可用。')
        return None, ark_model_id

    if model_gateway_url:
        # 银河战舰模式：通过中转网关访问多厂商模型
        client = Client(api_key='gateway', base_url=model_gateway_url)
        log.info('银河战舰模式已启用：网关=%s  模型=%s', model_gateway_url, agent_model)
        return client, agent_model

    if ark_api_key:
        client = Client(api_key=ark_api_key, base_url=ARK_BASE_URL)
        return client, ark_model_id

    log.warning('MODEL_GATEWAY_URL 与 ARK_API_KEY 均未设置，AI 分析功能已禁用。')
    return None, ark_model_id
