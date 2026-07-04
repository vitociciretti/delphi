"""
设置相关接口：LLM 连接测试（Bring-Your-Own-Key）。

BYO-key 模式下服务器不保存任何密钥，故没有 GET/保存 接口——配置只存在于
用户浏览器的 sessionStorage，并随每个请求以 header 携带。这里只提供一个
无状态的连通性测试。
"""

import traceback
from flask import request, jsonify

from . import settings_bp
from ..services import llm_settings
from ..utils.logger import get_logger

logger = get_logger('delphi.api.settings')


@settings_bp.route('/llm/test', methods=['POST'])
def test_llm_settings():
    """用请求体给定的配置测试连通性（不落盘、不改全局状态）。

    请求（JSON）：{ api_key, base_url, model }
    """
    try:
        data = request.get_json() or {}
        result = llm_settings.test_connection(
            api_key=(data.get('api_key') or '').strip(),
            base_url=(data.get('base_url') or '').strip(),
            model=(data.get('model') or '').strip(),
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e),
                        "traceback": traceback.format_exc()}), 500
