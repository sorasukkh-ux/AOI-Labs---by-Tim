"""Rice varieties and the SKUs (AOIMS_MATERIAL codes) of each variety.

The mill buys, mills and sells three varieties. Paddy, rice and broken rice are separate SKUs per variety;
rice bran and husk are one SKU each. The white-rice codes are the original ones (PADDY, RICE_W5, BROKEN),
so the data recorded before the varieties existed stays white rice.
"""
JASMINE, WHITE, GLUTINOUS = "HM", "WH", "GL"
VARIETIES = (JASMINE, WHITE, GLUTINOUS)
DEFAULT = WHITE
VAR_KEYS = {JASMINE: "varJasmine", WHITE: "varWhite", GLUTINOUS: "varGlutinous"}
# Default milling priority of a new order by variety (the operator can still change it): U urgent, H high, M medium, L low
DEFAULT_PRIORITY = {JASMINE: "H", WHITE: "M", GLUTINOUS: "L"}

PADDY, RICE, BROKEN, BRAN, HUSK = "paddy", "rice", "broken", "bran", "husk"
KIND_KEYS = {PADDY: "skuPaddy", RICE: "skuRice", BROKEN: "skuBroken", BRAN: "skuBran", HUSK: "skuHusk"}

CODES = {
    JASMINE: {PADDY: "PADDY_HM", RICE: "RICE_HM", BROKEN: "BROKEN_HM"},
    WHITE: {PADDY: "PADDY", RICE: "RICE_W5", BROKEN: "BROKEN"},
    GLUTINOUS: {PADDY: "PADDY_GL", RICE: "RICE_GL", BROKEN: "BROKEN_GL"},
}
MAT_BRAN, MAT_HUSK = "BRAN", "HUSK"

# code -> (kind, variety or None)
INFO = {code: (kind, var) for var, kinds in CODES.items() for kind, code in kinds.items()}
INFO.update({MAT_BRAN: (BRAN, None), MAT_HUSK: (HUSK, None)})

# every SKU in display order: per variety paddy, rice, broken; then bran and husk
ALL = tuple(CODES[v][k] for v in VARIETIES for k in (PADDY, RICE, BROKEN)) + (MAT_BRAN, MAT_HUSK)
# what Customer Service buys and sells (the design's rate tables; husk is sold from Inventory only)
TRADED = tuple(c for c in ALL if c != MAT_HUSK)

# Thai names for AOIMS_MATERIAL.MAT_DESC
DESC_TH = {"PADDY_HM": "ข้าวเปลือกหอมมะลิ", "RICE_HM": "ข้าวหอมมะลิ", "BROKEN_HM": "ปลายข้าวหอมมะลิ",
           "PADDY": "ข้าวเปลือกเจ้า", "RICE_W5": "ข้าวสารขาว 5%", "BROKEN": "ปลายข้าวเจ้า",
           "PADDY_GL": "ข้าวเปลือกเหนียว", "RICE_GL": "ข้าวเหนียว", "BROKEN_GL": "ปลายข้าวเหนียว",
           "BRAN": "รำข้าว", "HUSK": "แกลบ"}
MAT_TYPE = {PADDY: "PD", RICE: "RC", BROKEN: "BR", BRAN: "RB", HUSK: "HK"}


def clean(value):
    """A variety code from a form, or the default (white rice)."""
    value = (value or "").strip().upper()
    return value if value in VARIETIES else DEFAULT


def code(variety, kind):
    """The SKU of a variety: code('HM', 'rice') -> 'RICE_HM'. Bran and husk are shared."""
    if kind == BRAN:
        return MAT_BRAN
    if kind == HUSK:
        return MAT_HUSK
    return CODES[clean(variety)][kind]


def kind_of(mat_code):
    return INFO.get(mat_code, (None, None))[0]


def variety_of(mat_code):
    return INFO.get(mat_code, (None, None))[1]


def mat_name(mat_code):
    """Translated SKU name, e.g. 'Rice (Jasmine)' / 'ข้าวสาร (หอมมะลิ)'."""
    from ..i18n import t
    kind, var = INFO.get(mat_code, (None, None))
    if kind is None:
        return mat_code or ""
    return t(KIND_KEYS[kind]) + ((" (%s)" % t(VAR_KEYS[var])) if var else "")


def groups(codes=ALL):
    """[(variety or None, [codes])] in display order, for tables grouped by variety."""
    out = []
    for var in list(VARIETIES) + [None]:
        members = [c for c in codes if variety_of(c) == var]
        if members:
            out.append((var, members))
    return out
