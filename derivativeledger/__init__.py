"""DerivativeLedger Flask application factory."""
import hmac
import secrets
from flask import Flask, abort, render_template, request, session
from .config import Config
from .db import close_connections
from .web import bp


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.teardown_appcontext(close_connections)
    app.register_blueprint(bp)

    @app.before_request
    def protect_post_requests():
        token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
        if request.method == "POST":
            submitted = request.form.get("csrf_token", "")
            if not submitted or not hmac.compare_digest(token, submitted):
                abort(400, "Invalid form security token. Reload the page and try again.")

    @app.get("/health")
    def health():
        from .db import mysql_connection, mongo_database
        try:
            mysql_connection().ping(reconnect=False, attempts=1, delay=0)
            mongo_database().command("ping")
            return {"status": "ok", "mysql": "ok", "mongodb": "ok"}
        except Exception as exc:
            return {"status": "degraded", "detail": str(exc)}, 503

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Page not found"), 404

    @app.errorhandler(500)
    def server_error(_error):
        return render_template("error.html", code=500, message="An unexpected error occurred"), 500

    return app
