"""Background work of the server: the daily market price fetch (AOI_MARKET_AUTO, from AOI_MARKET_HOUR).

A small thread wakes every 15 minutes. Only the first worker of the day takes the turn (see market.daily_due),
so running several server processes still fetches once a day. Not started in tests.
"""
import threading
import time

_started = False
_health_started = False


def start_health(app):
    """Update 48: a server health check every 5 minutes (Automation > AI, the red "!" on the menu)."""
    global _health_started
    if _health_started or app.config.get("TESTING"):
        return
    _health_started = True

    def loop():
        from .models import health
        time.sleep(20)
        while True:
            try:
                with app.app_context():
                    doc = health.check(sample_seconds=1.0)
                    if doc["LEVEL"] == health.CRIT:
                        bad = [f["key"] for f in doc["FINDINGS"] if f["level"] == health.CRIT]
                        app.logger.warning("Server health critical: %s", ", ".join(bad))
            except Exception:                                   # never let the thread die
                pass
            time.sleep(5 * 60)

    threading.Thread(target=loop, name="aoi-health", daemon=True).start()


def start(app):
    global _started
    start_health(app)
    if _started or app.config.get("TESTING") or not app.config.get("MARKET_AUTO"):
        return
    _started = True

    def _history_once(app):
        """Once per NABC product: its whole price history for the market price graph (about 2 years)."""
        from .models import market_history
        from .models.base import col, ValidationError
        from .models.counter import Counter
        from .models.market import MarketMap
        for m in MarketMap.all():
            product = m.get("NABC_PRODUCT")
            if m["SOURCE"] != "NABC" or not product or col(Counter.COL).find_one({"COUNTER_KEY": "NABC-HIST-" + product}):
                continue
            try:
                saved = market_history.download_nabc_history([product])
                Counter.next("NABC-HIST-" + product)
                app.logger.info("NABC price history: %s", saved)
            except ValidationError as error:
                app.logger.warning("NABC price history of %s: %s", product, error.key)

    def loop():
        from .models.base import ValidationError
        from .models.market import daily_due, fetch_all
        while True:
            try:
                with app.app_context():
                    if daily_due():
                        result = fetch_all("system:market", force=False)
                        app.logger.info("Market prices: NABC %s new record(s); report %s",
                                        (result["nabc"] or {}).get("new"), (result["report"] or {}).get("SOURCE_URL"))
                        for error in result["errors"]:
                            app.logger.warning("Daily market price fetch: %s", error.key)
                        _history_once(app)
                        from .models import export_price          # update 50: the rice exporters' weekly FOB prices
                        try:
                            app.logger.info("Export prices: %s", export_price.fetch("system:market"))
                        except ValidationError as error:
                            app.logger.warning("Daily export price fetch: %s", error.key)
                    from .models import supply
                    if supply.daily_due():
                        try:
                            app.logger.info("NABC production index: %s month(s) saved", supply.fetch("system:nabc"))
                        except ValidationError as error:
                            app.logger.warning("Daily NABC index fetch failed: %s", error.key)
            except Exception:                                   # never let the thread die
                app.logger.exception("Market price scheduler error")
            time.sleep(15 * 60)

    threading.Thread(target=loop, name="aoi-market", daemon=True).start()
