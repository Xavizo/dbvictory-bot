"""Read the local checkout's commit when Git is unavailable to the desktop app."""

from pathlib import Path
import re


def read_head_revision(root: Path) -> str | None:
    git_dir = root / ".git"
    try:
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir: "):
                return None
            git_dir = root / pointer.removeprefix("gitdir: ")

        value = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if value.startswith("ref: "):
            ref = value.removeprefix("ref: ")
            if (not ref.startswith("refs/") or any(c in ref for c in "\\:\x00")
                    or any(part in ("", ".", "..") for part in ref.split("/"))):
                return None
            common = git_dir
            if (git_dir / "commondir").is_file():
                common = git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()
            try:
                value = (common / ref).read_text(encoding="ascii").strip()
            except FileNotFoundError:
                with (common / "packed-refs").open(encoding="utf-8") as packed:
                    for line in packed:
                        sha, _, name = line.rstrip("\r\n").partition(" ")
                        if name == ref:
                            value = sha
                            break
    except (OSError, UnicodeError, ValueError):
        return None
    return value if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) else None
