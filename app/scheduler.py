"""Background work of the server: the daily market price fetch (AOI_MARKET_AUTO, from AOI_MARKET_HOUR).

A small thread wakes every 15 minutes. Only the first worker of the day takes the turn (see market.daily_due),
so running several server processes still fetches once a day. Not started in tests.
"""
import threading
import time

_started = False


def start(app):
    global _started
    if _started or app.config.get("TESTING") or not app.config.get("MARKET_AUTO"):
        return
    _started = True

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
