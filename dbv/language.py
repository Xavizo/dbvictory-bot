"""The player's display language, shared by the launcher and the panel.

The launcher's language switch writes it to the installation's state folder
(.dbvictory/language.json); the panel reads it from there and follows. Polish
stays the default, so installations that never chose keep the labels they know.
"""
import json
import os
from pathlib import Path

from dbv.paths import INSTALL_ROOT, state_dir

LANGUAGES = ("pl", "en")
DEFAULT = "pl"
FILE_NAME = "language.json"


def language_file(root=INSTALL_ROOT):
    return state_dir(root) / FILE_NAME


def read_language(root=INSTALL_ROOT):
    """The chosen language code; the default for a missing, unreadable or unknown choice."""
    try:
        value = json.loads(language_file(root).read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        return DEFAULT
    return value if value in LANGUAGES else DEFAULT


def write_language(language, root=INSTALL_ROOT):
    """Save the choice atomically, so the panel never reads half a file."""
    if language not in LANGUAGES:
        raise ValueError(f"Unknown language: {language!r}")
    file = language_file(root)
    file.parent.mkdir(parents=True, exist_ok=True)
    temp = file.with_name(file.name + ".tmp")
    temp.write_text(json.dumps({"language": language}), encoding="utf-8")
    os.replace(temp, file)
    return language


class LanguageWatch:
    """read_language for a caller that asks often (every panel state push): rereads only on change."""

    def __init__(self, root=INSTALL_ROOT):
        self._root = Path(root)
        self._seen = None
        self._value = DEFAULT

    def __call__(self):
        try:
            stat = language_file(self._root).stat()
            seen = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            seen = None
        if seen != self._seen:
            self._seen = seen
            self._value = read_language(self._root) if seen else DEFAULT
        return self._value
