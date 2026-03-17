"""
银河战舰模型中转网关 (Galaxy Battleship Model Router)
============================================================
一个零额外依赖的 OpenAI 兼容代理网关，将 OpenClaw 对单一端点的
请求智能路由到 NVIDIA NIM、Hugging Face Inference 或 Volcengine ARK。

工程原则：不修改 OpenClaw 底层源码，通过自建中转网关实现多厂商调度。

── 支持的模型厂商 (银河战舰成员) ────────────────────────────────────────
  NVIDIA NIM  (https://integrate.api.nvidia.com/v1)
    nvidia/* mistralai/* meta/* qwen/* deepseek-ai/* google/*
    microsoft/* minimaxai/* moonshotai/* z-ai/* ibm/* bytedance/*
    openai/* stepfun-ai/* abacusai/* marin/* utter-project/*
    gotocompany/* mediatek/* baichuan-inc/* igenius/* yentinglin/*
    institute-of-science-tokyo/* tokyotech-llm/* rakuten/*
    ai21labs/* stockmark/* speakleash/* opengpt-x/* sarvamai/*

  Hugging Face Inference (https://api-inference.huggingface.co/v1)
    任何 HF Hub 上的 text-generation 模型（备用）

  Volcengine ARK  (https://ark.cn-beijing.volces.com/api/v3)
    volcengine/* 及 ep-* 格式的模型端点 ID（原有 DeepSeek 路由）

── 环境变量 ──────────────────────────────────────────────────────────────
  MODEL_GATEWAY_PORT   网关监听端口（默认 8090）
  NVIDIA_API_KEY       NVIDIA NIM API Key（必填，如使用 nvidia/* 等路由）
  HF_API_KEY           HuggingFace Inference API Key（hf_xxx 格式，备用）
  ARK_API_KEY          Volcengine ARK API Key（DeepSeek 原有路由）

── 路由规则 ──────────────────────────────────────────────────────────────
  1. model 前缀含 volcengine/ 或 model 以 ep- 开头 → Volcengine ARK
  2. 其他所有 org/model 格式 → NVIDIA NIM（主力路由）
  3. NVIDIA_API_KEY 未设置时降级到 HuggingFace Inference API

使用方法（在 config.py 中）：
  将 OpenClaw 的 provider baseUrl 指向 http://127.0.0.1:8090
  所有模型名称直接使用 <org>/<model-id> 格式，网关自动路由。
"""

import io
import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ── 日志 ─────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [GW] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('ModelRouter')

# ── 配置 ─────────────────────────────────────────────────────────────────────
GATEWAY_PORT    = int(os.environ.get('MODEL_GATEWAY_PORT', '8090'))
NVIDIA_API_KEY  = os.environ.get('NVIDIA_API_KEY', '')
HF_API_KEY      = os.environ.get('HF_API_KEY', '')
ARK_API_KEY     = os.environ.get('ARK_API_KEY', '')

NVIDIA_BASE_URL = 'https://integrate.api.nvidia.com/v1'
HF_BASE_URL     = 'https://api-inference.huggingface.co/v1'
ARK_BASE_URL    = 'https://ark.cn-beijing.volces.com/api/v3'

# 通过 NVIDIA NIM 可用的厂商前缀（覆盖题目中所有主流厂商）
NVIDIA_NIM_PREFIXES: frozenset[str] = frozenset({
    # 核心厂商
    'nvidia', 'mistralai', 'meta', 'qwen', 'deepseek-ai', 'google',
    'microsoft', 'minimaxai', 'moonshotai', 'z-ai', 'thudm',
    # 其他厂商（均在 NIM 目录中有对应模型）
    'ibm', 'bytedance', 'openai', 'stepfun-ai', 'abacusai',
    'marin', 'utter-project', 'gotocompany', 'mediatek',
    'baichuan-inc', 'igenius', 'yentinglin',
    'institute-of-science-tokyo', 'tokyotech-llm', 'rakuten',
    'ai21labs', 'stockmark', 'speakleash', 'opengpt-x', 'sarvamai',
})

# ── 路由决策 ─────────────────────────────────────────────────────────────────

def _resolve_backend(model: str) -> tuple[str, str]:
    """
    根据 model 名称返回 (base_url, api_key)。
    优先级：Volcengine → NVIDIA NIM → HuggingFace
    """
    # Volcengine ARK：ep-* 端点 ID 或 volcengine/ 前缀
    if model.startswith('ep-') or model.startswith('volcengine/'):
        return ARK_BASE_URL, ARK_API_KEY

    org = model.split('/')[0] if '/' in model else ''

    if org in NVIDIA_NIM_PREFIXES and NVIDIA_API_KEY:
        return NVIDIA_BASE_URL, NVIDIA_API_KEY

    # NVIDIA_API_KEY 未设置时，降级到 HuggingFace
    if HF_API_KEY:
        return HF_BASE_URL, HF_API_KEY

    # 最后兜底：尝试 NVIDIA 即使没 Key（会 401，但不崩溃）
    return NVIDIA_BASE_URL, NVIDIA_API_KEY


def _forward(method: str, path: str, headers: dict, body: bytes) -> tuple[int, bytes]:
    """
    将请求转发到目标后端，返回 (status_code, response_body)。
    """
    body_json = json.loads(body) if body else {}
    model     = body_json.get('model', '')

    base_url, api_key = _resolve_backend(model)
    target_url = base_url.rstrip('/') + path

    log.info('→ %s  model=%-45s  backend=%s', method, model, base_url)

    forward_headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'User-Agent': 'sentinel-gateway/1.0',
    }
    # 透传 Accept 头（支持 SSE 流式）
    if 'Accept' in headers:
        forward_headers['Accept'] = headers['Accept']

    req = urllib.request.Request(
        url=target_url,
        data=body if body else None,
        headers=forward_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        log.error('后端响应错误 %s: %s', exc.code, exc.reason)
        error_body = exc.read() if exc.fp else b''
        return exc.code, error_body
    except urllib.error.URLError as exc:
        log.error('网络连接失败: %s', exc.reason)
        return 502, json.dumps({
            'error': {'message': f'Gateway upstream error: {exc.reason}', 'type': 'gateway_error'},
        }).encode()


# ── HTTP 请求处理 ─────────────────────────────────────────────────────────────

class GatewayHandler(BaseHTTPRequestHandler):
    """处理所有入站请求，代理到后端并返回响应。"""

    def log_message(self, fmt: str, *args: object) -> None:
        """静默 HTTP 访问日志，避免高频请求产生日志洪流。"""
        pass

    def _send_cors(self) -> None:
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def _handle(self, method: str) -> None:
        length  = int(self.headers.get('Content-Length', 0))
        body    = self.rfile.read(length) if length > 0 else b''
        headers = dict(self.headers)

        status, resp_body = _forward(method, self.path, headers, body)

        self.send_response(status)
        self._send_cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def do_GET(self) -> None:   # noqa: N802
        self._handle('GET')

    def do_POST(self) -> None:  # noqa: N802
        self._handle('POST')


# ── 健康检查端点 ──────────────────────────────────────────────────────────────

class _HealthyGatewayHandler(GatewayHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == '/health':
            body = json.dumps({'status': 'ok', 'port': GATEWAY_PORT}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            super().do_GET()


# ── 主入口 ───────────────────────────────────────────────────────────────────

def _print_config() -> None:
    log.info('══════════════════════════════════════════════════════')
    log.info('  银河战舰模型中转网关 v1.0  —  port %d', GATEWAY_PORT)
    log.info('══════════════════════════════════════════════════════')
    log.info('  NVIDIA NIM  : %s', '✅ 已配置' if NVIDIA_API_KEY else '❌ 未配置 (NVIDIA_API_KEY)')
    log.info('  HuggingFace : %s', '✅ 已配置' if HF_API_KEY else '❌ 未配置 (HF_API_KEY)')
    log.info('  Volcengine  : %s', '✅ 已配置' if ARK_API_KEY else '❌ 未配置 (ARK_API_KEY)')
    log.info('  路由规则: volcengine/* → ARK | org/* → NVIDIA NIM | 降级 → HF')
    log.info('  OpenClaw baseUrl: http://127.0.0.1:%d', GATEWAY_PORT)
    log.info('══════════════════════════════════════════════════════')


def start(blocking: bool = True) -> HTTPServer | None:
    """启动网关服务。blocking=False 时在后台线程运行，返回 server 实例。"""
    _print_config()
    server = HTTPServer(('0.0.0.0', GATEWAY_PORT), _HealthyGatewayHandler)
    log.info('网关已就绪，监听 0.0.0.0:%d', GATEWAY_PORT)

    if blocking:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info('网关已停止。')
        return None

    t = threading.Thread(target=server.serve_forever, daemon=True, name='model-gateway')
    t.start()
    return server


if __name__ == '__main__':
    start(blocking=True)
