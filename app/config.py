"""Application settings. Every value can be overridden with an environment variable (see .env.example)."""
import os
from datetime import timedelta


def _flag(name, default=False):
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


class Config:
    # Signs the session cookie. start.sh writes a random one into .env on first run.
    SECRET_KEY = os.environ.get("AOI_SECRET_KEY", "")

    # "mongodb" (normal) or "memory" (tests and quick demos only: data is lost when the server stops).
    DB_BACKEND = os.environ.get("AOI_DB", "mongodb").strip().lower()
    MONGO_URI = os.environ.get("AOI_MONGO_URI", "mongodb://127.0.0.1:27017")
    MONGO_DB = os.environ.get("AOI_MONGO_DB", "aoi_labs")

    # Business time zone: operating hours, running numbers and printed times use it.
    TIMEZONE = os.environ.get("AOI_TIMEZONE", "Asia/Bangkok")

    DEFAULT_LANG = os.environ.get("AOI_DEFAULT_LANG", "th")
    LANGUAGES = ("th", "en")
    DEFAULT_LOGIN_BRANCH = os.environ.get("AOI_DEFAULT_LOGIN_BRANCH", "factory2")
    MAX_LOGIN_FAILS = int(os.environ.get("AOI_MAX_LOGIN_FAILS", "5"))

    SESSION_COOKIE_NAME = "aoi_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("AOI_COOKIE_SECURE")      # turn on when served over HTTPS only
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024                   # forms are small; the largest upload is a market report PDF

    # Printed on the queue label (mill contact details).
    LABEL_ADDRESS_EN = os.environ.get("AOI_LABEL_ADDRESS_EN", "195 Moo 8 Sawathi, Meuang, Khon Kaen 40000")
    LABEL_ADDRESS_TH = os.environ.get("AOI_LABEL_ADDRESS_TH", "195 หมู่ 8 ตำบลสาวะถี อำเภอเมือง จังหวัดขอนแก่น 40000")
    LABEL_TEL = os.environ.get("AOI_LABEL_TEL", "095 161 6497")
    LABEL_EMAIL = os.environ.get("AOI_LABEL_EMAIL", "sorasuk.kh@gmail.com")

    # Automation > AI > Ask the system. Leave the key empty to switch the chat off (the other AI parts work without it).
    AI_API_KEY = os.environ.get("AOI_AI_API_KEY", "").strip()
    AI_MODEL = os.environ.get("AOI_AI_MODEL", "claude-sonnet-4-5").strip()
    AI_API_URL = os.environ.get("AOI_AI_API_URL", "https://api.anthropic.com/v1/messages").strip()

    # Financial > Market prices: the Thai Rice Millers Association price page (its newest Pricerice*.pdf report).
    # update 54: the browser asks every time whether a script / style changed (304 when not), so an iPad never keeps
    # an old app.js after an update
    SEND_FILE_MAX_AGE_DEFAULT = 0
    MARKET_URL = os.environ.get("AOI_MARKET_URL", "http://www.thairicemillers.org/index.php?lay=show&ac=article&Ntype=19").strip()
    # update 50: Thai Rice Exporters Association weekly FOB export prices (US$/ton), for Automation > AI > Price analysis
    EXPORT_URL = os.environ.get("AOI_EXPORT_URL", "http://www.thairiceexporters.or.th/price.htm").strip()
    USD_THB = float(os.environ.get("AOI_USD_THB", "0") or 0)          # starting exchange rate; set on the screen
    MARKET_AUTO = os.environ.get("AOI_MARKET_AUTO", "1").strip() not in ("0", "", "false", "no")   # fetch once a day
    # Automation > AI: supply indicator (NABC monthly agricultural production index, an index, not prices)
    NABC_INDEX_URL = os.environ.get("AOI_NABC_INDEX_URL", "https://agriapi.nabc.go.th/api/production-index-month/sector").strip()
    # Financial > Market prices: NABC daily farm prices (agriapi.nabc.go.th/home/price), one product name per SKU
    NABC_PRICE_URL = os.environ.get("AOI_NABC_PRICE_URL", "https://agriapi.nabc.go.th/api/daily-prices/product").strip()
    MARKET_HOUR = int(os.environ.get("AOI_MARKET_HOUR", "9") or 9)                                # from this local hour
