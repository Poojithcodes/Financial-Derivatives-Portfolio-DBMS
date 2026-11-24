import os


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "local-development-key-change-me")
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
    MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
    MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "derivative_ledger")
    MYSQL_USER = os.getenv("MYSQL_USER", "ledger")
    MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "ledger-local-password")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://ledger:ledger-local-password@127.0.0.1:27017/?authSource=admin")
    MONGO_DATABASE = os.getenv("MONGO_DATABASE", "derivative_ledger_audit")
    KITE_API_KEY = os.getenv("KITE_API_KEY", "")
    KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
    KITE_TOKEN_ENCRYPTION_KEY = os.getenv("KITE_TOKEN_ENCRYPTION_KEY", "")
    KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:5000/kite/callback")
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
