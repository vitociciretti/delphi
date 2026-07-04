"""
LLM 连接设置（Bring-Your-Own-Key）。

整个应用（种子助手、本体生成、以及 OASIS 模拟子进程）都通过同一组
OpenAI 兼容的环境变量读取 LLM 配置：LLM_API_KEY / LLM_BASE_URL /
LLM_MODEL_NAME。本模块提供一个可在运行时修改并持久化的设置层：

- 保存到 uploads 目录下的 JSON（该目录已被 gitignore，密钥不会入库）；
- 保存时同时更新 os.environ 与 Config 类属性，因此当前 Flask 进程
  与随后 fork 的模拟子进程都会立即使用新配置；
- 启动时自动加载已持久化的设置。

任意 OpenAI 兼容的服务都可使用：OpenAI、Anthropic(Claude)、通义千问、
Gemini、Groq、OpenRouter、Ollama、LM Studio 等。
"""

import json
import os
from typing import Any, Dict, Optional

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('delphi.llm_settings')

_SETTINGS_PATH = os.path.join(Config.UPLOAD_FOLDER, 'llm_settings.json')

# 允许持久化的字段
_FIELDS = ('provider', 'api_key', 'base_url', 'model')


def _read_file() -> Dict[str, Any]:
    if not os.path.exists(_SETTINGS_PATH):
        return {}
    try:
        with open(_SETTINGS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f) or {}
    except Exception as e:
        logger.warning(f"读取 LLM 设置失败: {e}")
        return {}


def _write_file(data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_SETTINGS_PATH), exist_ok=True)
    with open(_SETTINGS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def apply_settings(data: Dict[str, Any]) -> None:
    """将设置应用到当前进程环境与 Config，使后续 LLM 调用与子进程生效。"""
    api_key = (data.get('api_key') or '').strip()
    base_url = (data.get('base_url') or '').strip()
    model = (data.get('model') or '').strip()

    if api_key:
        os.environ['LLM_API_KEY'] = api_key
        Config.LLM_API_KEY = api_key
    if base_url:
        os.environ['LLM_BASE_URL'] = base_url
        Config.LLM_BASE_URL = base_url
    if model:
        os.environ['LLM_MODEL_NAME'] = model
        Config.LLM_MODEL_NAME = model


def save_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """持久化并应用设置。返回已保存（掩码后的）设置。"""
    existing = _read_file()
    # 未提供 api_key 时保留已有密钥（前端返回的是掩码，不应覆盖）
    incoming_key = (data.get('api_key') or '').strip()
    if not incoming_key or _is_masked(incoming_key):
        data = {**data, 'api_key': existing.get('api_key', '')}
    clean = {k: (data.get(k) or '').strip() for k in _FIELDS}
    _write_file(clean)
    apply_settings(clean)
    logger.info(
        f"LLM 设置已更新: provider={clean.get('provider')}, "
        f"model={clean.get('model')}, base_url={clean.get('base_url')}"
    )
    return masked_settings()


def _is_masked(key: str) -> bool:
    """前端回传的掩码形如 '••••1234'。"""
    return '•' in key or (key.startswith('*') and key.endswith(key[-4:]) and '*' in key)


def _mask_key(key: str) -> str:
    if not key:
        return ''
    if len(key) <= 4:
        return '••••'
    return '••••' + key[-4:]


def masked_settings() -> Dict[str, Any]:
    """返回当前设置，api_key 以掩码形式呈现（不泄露完整密钥）。"""
    data = _read_file()
    # 若无持久化文件，回退到当前 Config（可能来自 .env）
    provider = data.get('provider', '')
    api_key = data.get('api_key', Config.LLM_API_KEY or '')
    base_url = data.get('base_url', Config.LLM_BASE_URL or '')
    model = data.get('model', Config.LLM_MODEL_NAME or '')
    return {
        'provider': provider,
        'api_key_masked': _mask_key(api_key),
        'has_key': bool(api_key),
        'base_url': base_url,
        'model': model,
    }


def apply_persisted_on_startup() -> None:
    """应用启动时已持久化的设置（若存在）。"""
    data = _read_file()
    if data:
        apply_settings(data)
        logger.info(f"已加载持久化的 LLM 设置: model={data.get('model')}")


def test_connection(api_key: str, base_url: str, model: str) -> Dict[str, Any]:
    """用给定配置发起一次极小的补全请求，验证连通性。"""
    from ..utils.llm_client import LLMClient
    # 掩码或留空的密钥表示沿用已保存的密钥
    if not api_key or _is_masked(api_key):
        api_key = _read_file().get('api_key', '') or Config.LLM_API_KEY or ''
    try:
        client = LLMClient(api_key=api_key or 'not-needed', base_url=base_url, model=model)
        reply = client.chat(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            temperature=0,
            max_tokens=8,
        )
        return {"ok": True, "reply": (reply or '').strip()[:60]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
