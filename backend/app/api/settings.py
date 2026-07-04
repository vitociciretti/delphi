"""
设置相关接口：LLM 连接配置（Bring-Your-Own-Key）。
"""

import traceback
from flask import request, jsonify

from . import settings_bp
from ..services import llm_settings
from ..utils.logger import get_logger

logger = get_logger('delphi.api.settings')


@settings_bp.route('/llm', methods=['GET'])
def get_llm_settings():
    """获取当前 LLM 设置（api_key 掩码）。"""
    try:
        return jsonify({"success": True, "data": llm_settings.masked_settings()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e),
                        "traceback": traceback.format_exc()}), 500


@settings_bp.route('/llm', methods=['POST'])
def save_llm_settings():
    """
    保存并应用 LLM 设置。

    请求（JSON）：{ provider, api_key, base_url, model }
    未提供 api_key（或为掩码）时，保留已保存的密钥。
    """
    try:
        data = request.get_json() or {}
        if not (data.get('base_url') or '').strip():
            return jsonify({"success": False, "error": "base_url is required"}), 400
        if not (data.get('model') or '').strip():
            return jsonify({"success": False, "error": "model is required"}), 400
        saved = llm_settings.save_settings(data)
        return jsonify({"success": True, "data": saved})
    except Exception as e:
        return jsonify({"success": False, "error": str(e),
                        "traceback": traceback.format_exc()}), 500


@settings_bp.route('/llm/test', methods=['POST'])
def test_llm_settings():
    """用给定配置测试连通性（不落盘）。"""
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
