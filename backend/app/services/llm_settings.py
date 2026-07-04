"""
LLM 连接测试（Bring-Your-Own-Key，无状态）。

Delphi 是公开的 BYO-key 工具：每个用户自带密钥，服务器**不持久化任何密钥**。
浏览器把配置存在 sessionStorage，并在每次请求的 header 中携带
（见 utils/llm_creds.py）。因此本模块只保留一个无副作用的连通性测试——
既不落盘，也不修改进程环境或 Config。
"""

from typing import Any, Dict

from ..utils.logger import get_logger

logger = get_logger('delphi.llm_settings')


def test_connection(api_key: str, base_url: str, model: str) -> Dict[str, Any]:
    """用给定配置发起一次极小的补全请求，验证连通性。不落盘、不改全局状态。"""
    from ..utils.llm_client import LLMClient
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
