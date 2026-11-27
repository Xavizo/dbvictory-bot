"""Hand the verified installation to its ordinary, interactive session entry."""
import asyncio
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import os
import sys

from dbv.paths import state_dir
from launcher_lock import launcher_lock


class ReleaseChanged(Exception):
    """The desktop window must refresh its release before opening a session."""


def main(root):
    from dbv_revision import installed_revision
    from run_bot import prepare_environment, run

    feed = state_dir(root) / "updates"
    with ExitStack() as session:
        def lock_reviewed_release():
            session.enter_context(launcher_lock(state_dir(root) / "standalone.lock"))
            if (installed_revision(root) != os.environ.get("DBV_LAUNCHER_REVISION")
                    or any((feed / name).exists() for name in ("pending.json", "transaction.json"))
                    or (feed / "github" / "transaction.json").exists()):
                raise ReleaseChanged

        # The interactive prompt may wait for an arbitrary time. Only the user's
        # Start click claims the installation, and it must still be the same release.
        args = SimpleNamespace(game_port=7475, start=False, desktop=True)
        try:
            asyncio.run(run(args, prepare_environment(args.game_port), on_confirm=lock_reviewed_release))
        except ReleaseChanged:
            return 75
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main(Path(__file__).resolve().parents[1]))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"BŁĄD: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
