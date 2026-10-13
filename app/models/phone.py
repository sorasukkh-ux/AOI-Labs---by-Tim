"""Country calling codes for customer mobile numbers (AOIMS_CUSTOMER.MOBILE_CC). Default +66 (Thailand).

MOBILE_NO keeps the number as typed (for example 081-234-5678); shown with the code it is +66 81-234-5678:
the leading 0 of a national number is dropped, as it is when dialling from abroad.
"""
DEFAULT = "+66"
# (code, flag, English name, Thai name): Thailand and its neighbours first, then other common ones
COUNTRIES = (
    ("+66", "\U0001F1F9\U0001F1ED", "Thailand", "ไทย"),
    ("+95", "\U0001F1F2\U0001F1F2", "Myanmar", "เมียนมา"),
    ("+856", "\U0001F1F1\U0001F1E6", "Laos", "ลาว"),
    ("+855", "\U0001F1F0\U0001F1ED", "Cambodia", "กัมพูชา"),
    ("+60", "\U0001F1F2\U0001F1FE", "Malaysia", "มาเลเซีย"),
    ("+84", "\U0001F1FB\U0001F1F3", "Vietnam", "เวียดนาม"),
    ("+65", "\U0001F1F8\U0001F1EC", "Singapore", "สิงคโปร์"),
    ("+62", "\U0001F1EE\U0001F1E9", "Indonesia", "อินโดนีเซีย"),
    ("+63", "\U0001F1F5\U0001F1ED", "Philippines", "ฟิลิปปินส์"),
    ("+86", "\U0001F1E8\U0001F1F3", "China", "จีน"),
    ("+852", "\U0001F1ED\U0001F1F0", "Hong Kong", "ฮ่องกง"),
    ("+886", "\U0001F1F9\U0001F1FC", "Taiwan", "ไต้หวัน"),
    ("+81", "\U0001F1EF\U0001F1F5", "Japan", "ญี่ปุ่น"),
    ("+82", "\U0001F1F0\U0001F1F7", "South Korea", "เกาหลีใต้"),
    ("+91", "\U0001F1EE\U0001F1F3", "India", "อินเดีย"),
    ("+971", "\U0001F1E6\U0001F1EA", "United Arab Emirates", "สหรัฐอาหรับเอมิเรตส์"),
    ("+61", "\U0001F1E6\U0001F1FA", "Australia", "ออสเตรเลีย"),
    ("+44", "\U0001F1EC\U0001F1E7", "United Kingdom", "สหราชอาณาจักร"),
    ("+49", "\U0001F1E9\U0001F1EA", "Germany", "เยอรมนี"),
    ("+1", "\U0001F1FA\U0001F1F8", "United States / Canada", "สหรัฐอเมริกา / แคนาดา"),
)
CODES = tuple(c[0] for c in COUNTRIES)


def clean_code(value):
    return value if value in CODES else DEFAULT


def display(code, number):
    """'+66', '081-234-5678' -> '+66 81-234-5678'."""
    number = (number or "").strip()
    if not number:
        return ""
    return "%s %s" % (clean_code(code), number[1:] if number.startswith("0") else number)


def of(customer):
    customer = customer or {}
    return display(customer.get("MOBILE_CC"), customer.get("MOBILE_NO"))
