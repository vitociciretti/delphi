"""
配置管理
统一从项目根目录的 .env 文件加载配置
"""

import os
from dotenv import load_dotenv

# 加载项目根目录的 .env 文件
# 路径: Delphi/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # 如果根目录没有 .env，尝试加载环境变量（用于生产环境）
    load_dotenv(override=True)


class Config:
    """Flask配置类"""
    
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'delphi-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # 匿名工作区 cookie 是否仅经 HTTPS 传输。生产（nginx+TLS）应设为 True。
    COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'False').lower() == 'true'
    
    # JSON配置 - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX 格式）
    JSON_AS_ASCII = False
    
    # LLM配置（统一使用OpenAI格式）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # Zep配置
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # 文件上传配置
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # 文本处理配置
    DEFAULT_CHUNK_SIZE = 500  # 默认切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # 默认重叠大小
    
    # OASIS模拟配置
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # ===== WS-3 公开部署的用量限制（保护服务器算力，非 token 成本）=====
    # 并发模拟子进程是真正稀缺的资源（每个模拟 = 一个吃 CPU/内存的子进程）。
    # 全局上限保护整台机器；单工作区上限保证公平。
    MAX_CONCURRENT_SIMULATIONS = int(os.environ.get('MAX_CONCURRENT_SIMULATIONS', '4'))
    MAX_CONCURRENT_SIMULATIONS_PER_WORKSPACE = int(
        os.environ.get('MAX_CONCURRENT_SIMULATIONS_PER_WORKSPACE', '1'))
    # 模拟规模硬上限（用户无法逾越）：轮数 × Agent 数 ≈ 计算量。
    MAX_SIMULATION_ROUNDS = int(os.environ.get('MAX_SIMULATION_ROUNDS', '50'))
    MAX_SIMULATION_AGENTS = int(os.environ.get('MAX_SIMULATION_AGENTS', '50'))
    # 单个模拟的最长墙钟时间（秒）；超时由 reaper 回收，避免卡死的模拟长期占用名额。
    SIMULATION_MAX_WALLCLOCK_SECONDS = int(
        os.environ.get('SIMULATION_MAX_WALLCLOCK_SECONDS', '3600'))
    SIMULATION_REAPER_INTERVAL_SECONDS = int(
        os.environ.get('SIMULATION_REAPER_INTERVAL_SECONDS', '60'))

    # 请求速率限制（Flask-Limiter）。默认较宽松，昂贵端点单独收紧。
    RATELIMIT_ENABLED = os.environ.get('RATELIMIT_ENABLED', 'True').lower() == 'true'
    # 全局默认较宽松（前端会高频轮询状态端点），仅作洪泛保护；
    # 昂贵端点由下方按小时的严格上限单独兜底。
    RATELIMIT_DEFAULT = os.environ.get('RATELIMIT_DEFAULT', '600 per minute')
    RATELIMIT_SIMULATION_START = os.environ.get('RATELIMIT_SIMULATION_START', '20 per hour')
    RATELIMIT_PREPARE = os.environ.get('RATELIMIT_PREPARE', '30 per hour')
    RATELIMIT_ONTOLOGY = os.environ.get('RATELIMIT_ONTOLOGY', '40 per hour')
    RATELIMIT_REPORT = os.environ.get('RATELIMIT_REPORT', '40 per hour')

    # 场景/领域预设配置 (Scenario / domain presets)
    # SCENARIO_DEFAULT: 未指定场景时使用的预设 id（默认 social_media，与旧行为完全一致）
    # SCENARIO_PRESETS_DIR: 额外的用户自定义预设目录，放入 JSON 即可新增领域，无需改动代码
    SCENARIO_DEFAULT = os.environ.get('SCENARIO_DEFAULT', 'social_media')
    SCENARIO_PRESETS_DIR = os.environ.get(
        'SCENARIO_PRESETS_DIR',
        os.path.join(os.path.dirname(__file__), '../uploads/scenarios')
    )

    # OASIS平台可用动作配置
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report Agent配置
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls) -> list[str]:
        """验证必要配置。

        BYO-key 模式下服务器**不需要** LLM/ZEP 密钥（每个请求自带），故不再对其
        硬性校验。这里只返回真正会导致生产不安全的问题。
        """
        errors: list[str] = []
        # 生产环境（非 debug）必须提供强随机 SECRET_KEY——工作区 cookie 依赖它签名。
        if not cls.DEBUG and cls.SECRET_KEY == 'delphi-secret-key':
            errors.append(
                "SECRET_KEY 仍为默认值——生产环境必须设置强随机的 SECRET_KEY"
                "（否则匿名工作区 cookie 可被伪造）。"
            )
        return errors

