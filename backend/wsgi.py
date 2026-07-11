"""
WSGI entry point for production (gunicorn).

    gunicorn -c gunicorn.conf.py wsgi:app

Unlike run.py's main(), this does NOT call Config.validate() — in BYO-key mode
the server holds no LLM/Zep keys, so there is nothing to validate at boot; keys
arrive per-request. create_app() also starts the simulation reaper and registers
process cleanup in this worker.
"""

import os

from app import create_app

app = create_app()

# Behind nginx: trust one proxy hop so request.remote_addr / X-Forwarded-Proto
# reflect the real client (used by the rate limiter's IP fallback). Disable by
# setting TRUST_PROXY=false if not fronted by a reverse proxy.
if os.environ.get('TRUST_PROXY', 'true').lower() == 'true':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

