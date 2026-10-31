"""LINE Flex Message cards (update 52): what the customer gets on LINE looks like the printed card, not only the QR.

  customer_card  - the customer card: logo, AOI Labs, "โรงสีข้าว · บัตรลูกค้า", name, customer code, mobile, the note,
                   the QR of the customer code (same as Admin > Customers > Print QR card)
  receipt        - update 62: the receipt (Print Billing) of customer buying/selling orders: logo, AOI Labs, address,
                   receipt title + order type, order numbers, customer, date, the lines (kg x THB/kg = THB) and the total
  queue_card     - the milling queue card: logo, MILLING + queue number, address, customer, payment, receive time,
                   the QR of the order, the order code, phone / e-mail and the date (same as the printed queue label)

LINE draws the card itself (Thai text always right); the pictures (logo, QR) are fetched by LINE from the public
https address of the server, the same as before. The text message stays as the card's alternative text (the
notification preview, and old LINE versions that cannot show cards).
"""
from flask import current_app, url_for

INK, MUTED, LINE_GREY = "#16202C", "#5B6B7F", "#DDE4EC"


def _text(text, size="sm", weight="regular", color=INK, **extra):
    return dict({"type": "text", "text": str(text) if text not in (None, "") else "-", "size": size, "weight": weight,
                 "color": color, "wrap": True}, **extra)


def _logo(base):
    return {"type": "image", "url": base + url_for("static", filename="img/logo.png"), "size": "full",
            "aspectRatio": "600:247", "aspectMode": "fit"}


def _head(base, title, sub):
    return {"type": "box", "layout": "horizontal", "spacing": "md", "alignItems": "center", "contents": [
        {"type": "box", "layout": "vertical", "flex": 2, "contents": [_logo(base)]},
        {"type": "box", "layout": "vertical", "flex": 5, "contents": [_text(title, "lg", "bold"), _text(sub, "xs", color=MUTED)]}]}


def _qr(url):
    return {"type": "image", "url": url, "size": "full", "aspectRatio": "1:1", "aspectMode": "fit"}


def customer_card(customer, qr_url, base, mobile, sub, note):
    """The customer card as a Flex bubble."""
    return {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
            "contents": [
                _head(base, "AOI Labs", sub),
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "horizontal", "spacing": "md", "alignItems": "center", "contents": [
                    {"type": "box", "layout": "vertical", "flex": 3, "spacing": "xs", "contents": [
                        _text(customer.get("CUST_NAME"), "lg", "bold"),
                        _text(customer["CUST_CODE"], "xl", "bold"),
                        *([_text(mobile, "sm")] if mobile else []),
                        _text(note, "xxs", color=MUTED)]},
                    {"type": "box", "layout": "vertical", "flex": 2, "contents": [_qr(qr_url)]}]}]}}


def queue_card(label, qr_url, base):
    """The milling queue card as a Flex bubble. `label`: label_data() of the order (the printed label's fields)."""
    cfg = current_app.config
    return {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
            "contents": [
                {"type": "box", "layout": "horizontal", "alignItems": "center", "contents": [
                    {"type": "box", "layout": "vertical", "flex": 3, "contents": [_logo(base)]},
                    {"type": "box", "layout": "vertical", "flex": 3, "contents": [
                        _text("MILLING", "xs", "bold", MUTED, align="end"), _text(label.get("no"), "3xl", "bold", align="end")]}]},
                _text(cfg["LABEL_ADDRESS_EN"], "xxs", color=MUTED),
                _text(cfg["LABEL_ADDRESS_TH"], "xxs", color=MUTED),
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [
                    _text("Customer : %s" % label.get("customer"), "md", "bold"),
                    _text("Payment: %s THB" % label.get("pay"), "sm"),
                    _text("Receive: %s" % label.get("receive"), "sm")]},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "filler"}, {"type": "box", "layout": "vertical", "flex": 3, "contents": [_qr(qr_url)]}, {"type": "filler"}]},
                _text(label.get("qr"), "sm", "bold", align="center"),
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "box", "layout": "vertical", "flex": 3, "contents": [
                        _text("Tel: %s" % cfg["LABEL_TEL"], "xxs", color=MUTED), _text("Email: %s" % cfg["LABEL_EMAIL"], "xxs", color=MUTED)]},
                    {"type": "box", "layout": "vertical", "flex": 2, "contents": [
                        _text(label.get("date"), "xxs", color=MUTED, align="end"), _text(label.get("time"), "xxs", color=MUTED, align="end")]}]}]}}


def receipt(title, orders_no, customer, date, lines, total, base=None, labels=None):
    """The receipt as a Flex bubble. `lines`: [(name, kg, price, amount)] as texts; `base` None -> no logo (LINE
    fetches pictures only from a public https address), the card is still sent. `labels`: the Thai row titles."""
    cfg = current_app.config
    lb = labels or {}

    def row(left, right, size="sm", weight="regular", color=INK):
        return {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            _text(left, size, weight, color, flex=3), _text(right, size, weight, color, flex=4, align="end")]}

    def item(name, kg, price, amount):
        return {"type": "box", "layout": "vertical", "contents": [
            _text(name, "sm", "bold"),
            {"type": "box", "layout": "horizontal", "contents": [
                _text("%s kg x %s" % (kg, price), "xs", color=MUTED, flex=3), _text(amount, "sm", flex=2, align="end")]}]}

    head = [{"type": "box", "layout": "vertical", "contents": [_logo(base)], "paddingStart": "60px", "paddingEnd": "60px"}] if base else []
    return {"type": "bubble", "size": "mega", "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
            "contents": head + [
                _text("AOI Labs", "lg", "bold", align="center"),
                _text(cfg["LABEL_ADDRESS_TH"], "xxs", color=MUTED, align="center"),
                _text("Tel. %s" % cfg["LABEL_TEL"], "xxs", color=MUTED, align="center"),
                _text(title, "md", "bold", align="center"),
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "vertical", "spacing": "xs", "contents": [
                    row(lb.get("no", "เลขที่"), orders_no), row(lb.get("customer", "ลูกค้า"), customer), row(lb.get("date", "วันที่"), date)]},
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [item(*l) for l in lines] or [_text("-")]},
                {"type": "separator", "color": LINE_GREY},
                row(lb.get("total", "รวม"), "%s THB" % total, "md", "bold")]}}


def coupon_card(title, name, value, expire, note, qr_url, code, base, labels):
    """Update 96: the campaign coupon - festive header, value in THB, the customer, expiry, the QR to show at the counter."""
    head = [_logo(base)] if base else []
    return {"type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "backgroundColor": "#B91C1C", "paddingAll": "14px", "contents": [
                _text("🎄 " + title, "md", "bold", "#FFFFFF"), _text(labels["coupon"], "xs", color="#FDE68A")]},
            "body": {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px", "contents": head + [
                {"type": "box", "layout": "baseline", "contents": [
                    _text(value, "3xl", "bold", "#15803D", flex=0), _text(" THB", "md", "bold", "#15803D", flex=0)]},
                _text(labels["for"] + " " + name, "sm", "bold"),
                _text(labels["expire"] + " " + expire, "xs", color=MUTED),
                *([_text(note, "xs", color=MUTED)] if note else []),
                {"type": "separator", "color": LINE_GREY},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "filler"}, {"type": "box", "layout": "vertical", "flex": 4, "contents": [_qr(qr_url)]}, {"type": "filler"}]},
                _text(code, "xs", "bold", align="center"),
                _text(labels["how"], "xxs", color=MUTED, align="center")]}}
