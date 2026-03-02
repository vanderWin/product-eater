"""Flask application configuration."""
import os


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-key-change-in-prod")

    # Flask-Session: server-side filesystem sessions
    SESSION_TYPE = "filesystem"
    SESSION_FILE_DIR = "session_store"
    SESSION_FILE_THRESHOLD = 100
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True

    # Max upload size: 100 MB
    MAX_CONTENT_LENGTH = 100 * 1024 * 1024

    # Google Ads credentials (from env vars / Replit Secrets)
    GOOGLE_ADS = {
        "GOOGLE_ADS_DEVELOPER_TOKEN": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN"),
        "GOOGLE_ADS_CLIENT_ID": os.environ.get("GOOGLE_ADS_CLIENT_ID"),
        "GOOGLE_ADS_CLIENT_SECRET": os.environ.get("GOOGLE_ADS_CLIENT_SECRET"),
        "GOOGLE_ADS_REFRESH_TOKEN": os.environ.get("GOOGLE_ADS_REFRESH_TOKEN"),
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID": os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID"),
        "GOOGLE_ADS_CLIENT_CUSTOMER_ID": os.environ.get("GOOGLE_ADS_CLIENT_CUSTOMER_ID"),
    }
