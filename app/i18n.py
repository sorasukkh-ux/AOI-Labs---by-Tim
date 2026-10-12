"""Thai / English interface text. The text lives in app/translations/th.json and en.json."""
import json
import os

from flask import current_app, session

_TEXT = {}


def _load():
    if not _TEXT:
        folder = os.path.join(os.path.dirname(__file__), "translations")
        for lang in ("th", "en"):
            with open(os.path.join(folder, lang + ".json"), encoding="utf-8") as handle:
                _TEXT[lang] = json.load(handle)
    return _TEXT


def get_lang():
    lang = session.get("lang")
    return lang if lang in current_app.config["LANGUAGES"] else current_app.config["DEFAULT_LANG"]


def t(key, **params):
    """Text for `key` in the current language, with {placeholders} filled from `params`."""
    text = _load()
    value = text[get_lang()].get(key)
    if value is None:
        value = text["en"].get(key, key)
    for name, replacement in params.items():
        value = value.replace("{" + name + "}", str(replacement))
    return value


def texts(*keys):
    """A {key: text} dictionary for the scripts of one page."""
    return {key: t(key) for key in keys}


def t_in(lang, key, **params):
    """Text for `key` in a given language (the TV shows Thai with small English labels)."""
    text = _load()
    value = text.get(lang, {}).get(key) or text["en"].get(key, key)
    for name, replacement in params.items():
        value = value.replace("{" + name + "}", str(replacement))
    return value
