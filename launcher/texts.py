"""The launcher's words in the player's language, from launcher/desktop/i18n/<language>.json.

The page (render.js) and the launcher process (desktop_app, desktop_host) use the
same files. Text() is a str that remembers its key, so the launcher can say it
again in another language after the player switches.
"""
import json
from pathlib import Path

from dbv.language import DEFAULT, LANGUAGES

TEXT_DIR = Path(__file__).resolve().parent / "desktop" / "i18n"
_cache = {}
_current = [DEFAULT]


def texts(language):
    """The whole dictionary of one language; Polish fills keys the other file misses."""
    if language not in LANGUAGES:
        language = DEFAULT
    if language not in _cache:
        words = json.loads((TEXT_DIR / f"{DEFAULT}.json").read_text(encoding="utf-8"))
        if language != DEFAULT:
            words.update(json.loads((TEXT_DIR / f"{language}.json").read_text(encoding="utf-8")))
        _cache[language] = words
    return _cache[language]


def language():
    return _current[0]


def use(language):
    """Set the language every later text() call answers in."""
    _current[0] = language if language in LANGUAGES else DEFAULT


class Text(str):
    """A translated string that can be translated again: Text.again() in the current language."""

    def __new__(cls, key, params):
        words = texts(_current[0])
        value = str.__new__(cls, words.get(key, key).format(**params) if params else words.get(key, key))
        value.key, value.params = key, params
        return value

    def again(self):
        return Text(self.key, {k: v.again() if isinstance(v, Text) else v for k, v in self.params.items()})


def text(key, **params):
    return Text(key, params)


def number(value, digits=1):
    """A decimal in the player's notation: 2,5 in Polish, 2.5 in English."""
    shown = f"{value:.{digits}f}"
    return shown.replace(".", ",") if _current[0] == "pl" else shown
