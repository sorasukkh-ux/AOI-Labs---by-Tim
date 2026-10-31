"""Update 106: the shop's own music file for a billboard theme (Christmas or Songkran), instead of the built-in tune.

The file is kept in MongoDB (AOITS_BB_MUSIC, one per branch and theme); its name, size and version are copied to
SYSP_BB.MUSIC_FILES so the billboard does not read the file on every refresh. The shop must own the music and hold a
licence to play it in public; the settings page says so."""
import hashlib

from .base import col, NO_ID, ValidationError, audit_update
from .branch import Branch
from ..timeutil import now_utc

COL = "AOITS_BB_MUSIC"
THEMES = ("default", "christmas", "songkran", "cards", "ranking")   # update 107: every billboard theme
MAX_BYTES = 10 * 1024 * 1024
MIMES = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "ogg": "audio/ogg", "wav": "audio/wav"}


def kind_of(data):
    """The audio type from the first bytes (the file name is not trusted)."""
    head = data[:16]
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "m4a"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    return None


def files(branch_id):
    """{theme: {'name', 'size', 'ext', 'ver', 'date', 'user'}} from SYSP_BB.MUSIC_FILES."""
    saved = ((Branch.get(branch_id) or {}).get("SYSP_BB") or {}).get("MUSIC_FILES") or {}
    return {t: {"name": f.get("NAME"), "size": f.get("SIZE"), "ext": f.get("EXT"), "ver": f.get("VER"),
                "date": f.get("DATE"), "user": f.get("USER")} for t, f in saved.items() if t in THEMES and f}


def get(branch_id, theme):
    if theme not in THEMES:
        return None
    return col(COL).find_one({"BRANCH_ID": branch_id, "THEME": theme}, NO_ID)


def save(branch_id, theme, filename, data, ope_user):
    if not Branch.get(branch_id):
        raise ValidationError("errBranchUnknown")
    if theme not in THEMES:
        raise ValidationError("bbMusicErrTheme")
    if not data:
        raise ValidationError("bbMusicErrEmpty")
    if len(data) > MAX_BYTES:
        raise ValidationError("bbMusicErrSize", mb=MAX_BYTES // (1024 * 1024))
    ext = kind_of(data)
    if not ext:
        raise ValidationError("bbMusicErrType")
    name = (filename or "music").replace("\\", "/").rsplit("/", 1)[-1][:80] or "music"
    ver = hashlib.sha1(data).hexdigest()[:10]
    now = now_utc()
    col(COL).update_one({"BRANCH_ID": branch_id, "THEME": theme},
                        {"$set": {"BRANCH_ID": branch_id, "THEME": theme, "FILE_NAME": name, "FILE_EXT": ext, "MIME": MIMES[ext],
                                  "FILE_SIZE": len(data), "FILE_VER": ver, "DATA": data, "UPLOAD_DATE": now, "UPLOAD_USER": ope_user}},
                        upsert=True)
    _set_meta(branch_id, theme, {"NAME": name, "SIZE": len(data), "EXT": ext, "VER": ver, "DATE": now, "USER": ope_user}, ope_user)
    return files(branch_id)[theme]


def delete(branch_id, theme, ope_user):
    if theme not in THEMES:
        raise ValidationError("bbMusicErrTheme")
    col(COL).delete_one({"BRANCH_ID": branch_id, "THEME": theme})
    _set_meta(branch_id, theme, None, ope_user)


def _set_meta(branch_id, theme, meta, ope_user):
    bb = dict((Branch.get(branch_id) or {}).get("SYSP_BB") or {})
    music = dict(bb.get("MUSIC_FILES") or {})
    if meta:
        music[theme] = meta
    else:
        music.pop(theme, None)
    bb["MUSIC_FILES"] = music
    values = {"SYSP_BB": bb}
    values.update(audit_update(ope_user))
    col(Branch.COL).update_one({"BRANCH_ID": branch_id}, {"$set": values})
