"""Hand the verified installation to its ordinary, interactive session entry."""
from pathlib import Path
import os
import sys

from launcher_lock import launcher_lock


def main(root):
    feed = root / ".codex" / "updates"
    with launcher_lock(root / ".codex" / "standalone.lock"):
        from dbv_revision import read_head_revision
        if (read_head_revision(root) != os.environ.get("DBV_LAUNCHER_REVISION")
                or any((feed / name).exists() for name in ("pending.json", "transaction.json"))):
            return 75  # A release arrived after the window's preflight.
        from run_bot import main as interactive_session
        return interactive_session(session_lock_held=True)


if __name__ == "__main__":
    try:
        sys.exit(main(Path(__file__).resolve().parents[1]))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
