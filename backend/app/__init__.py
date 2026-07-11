"""
Delphi Backend - Flask应用工厂
"""

import os
import warnings

# 抑制 multiprocessing resource_tracker 的警告（来自第三方库如 transformers）
# 需要在所有其他导入之前设置
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask应用工厂函数"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 设置JSON编码：确保中文直接显示（而不是 \uXXXX 格式）
    # Flask >= 2.3 使用 app.json.ensure_ascii，旧版本使用 JSON_AS_ASCII 配置
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False
    
    # 设置日志
    logger = setup_logger('delphi')
    
    # 只在 reloader 子进程中打印启动信息（避免 debug 模式下打印两次）
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process
    
    if should_log_startup:
        logger.info("=" * 50)
        logger.info("Delphi Backend 启动中...")
        logger.info("=" * 50)
    
    # 启用CORS。supports_credentials=True 让浏览器发送/接收 workspace cookie；
    # 此时 flask-cors 会回显请求 Origin（而非字面 "*"，两者与凭据模式不兼容）。
    CORS(app, resources={r"/api/*": {"origins": "*"}}, supports_credentials=True)
    
    # 注册模拟进程清理函数（确保服务器关闭时终止所有模拟进程）
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("已注册模拟进程清理函数")

    # WS-3：启动超时模拟回收线程（避免卡死的模拟长期占用并发名额）。
    # 幂等（内部 _reaper_started 守卫），故无条件启动即可。
    SimulationRunner.start_reaper()
    
    # 匿名工作区：解析/签发 workspace cookie（数据按工作区隔离）
    from .utils.workspace import init_request_workspace, attach_workspace_cookie

    # 请求日志中间件
    @app.before_request
    def log_request():
        init_request_workspace()
        logger = get_logger('delphi.request')
        logger.debug(f"请求: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"请求体: {request.get_json(silent=True)}")

    @app.after_request
    def log_response(response):
        logger = get_logger('delphi.request')
        logger.debug(f"响应: {response.status_code}")
        return attach_workspace_cookie(response)
    
    # BYO-key：不再持久化/加载 LLM 密钥。每个请求自带 header 凭据
    # （见 utils/llm_creds.py），服务器不保存任何密钥。

    # 注册蓝图
    from .api import graph_bp, simulation_bp, report_bp, settings_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')
    app.register_blueprint(settings_bp, url_prefix='/api/settings')

    # WS-3：请求速率限制。init_app 在蓝图之后调用；其 before_request 钩子
    # 晚于上面的 workspace 初始化，故限流 key 能取到 workspace_id。
    from .utils.limiter import limiter
    limiter.init_app(app)

    # 429 以统一的 JSON 形状返回（前端按 { success, error } 解析）
    @app.errorhandler(429)
    def ratelimit_handler(e):
        from flask import jsonify
        desc = getattr(e, 'description', 'rate limit exceeded')
        return jsonify({
            "success": False,
            "error": f"Too many requests — {desc}. Please slow down and retry shortly.",
            "rate_limited": True,
        }), 429

    # 健康检查
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'Delphi Backend'}

    # WS-4：可选地由本进程直接托管已构建的前端 SPA（单容器/简单部署）。
    # 生产推荐仍用 nginx 托管静态并做 TLS；此路径仅为省事的备选。
    if os.environ.get('SERVE_STATIC', 'false').lower() == 'true':
        from flask import send_from_directory
        default_static = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '../../frontend/dist'))
        static_dir = os.environ.get('STATIC_DIR', default_static)

        @app.route('/', defaults={'path': ''})
        @app.route('/<path:path>')
        def spa(path):
            # API/健康检查交给各自的路由；未命中静态文件则回退到 index.html（前端路由）
            if path.startswith('api/') or path == 'health':
                return {'success': False, 'error': 'Not found'}, 404
            full = os.path.join(static_dir, path)
            if path and os.path.isfile(full):
                return send_from_directory(static_dir, path)
            return send_from_directory(static_dir, 'index.html')

        if should_log_startup:
            logger.info(f"已启用前端静态托管: {static_dir}")

    if should_log_startup:
        logger.info("Delphi Backend 启动完成")

    return app

