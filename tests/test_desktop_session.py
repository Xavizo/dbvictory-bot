import asyncio
import sys
from types import SimpleNamespace

import pytest

from launcher.desktop_session import main


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installation lock")
@pytest.mark.parametrize("changed", ["none", "revision", "pending.json", "transaction.json"])
def test_desktop_handoff_holds_lock_and_uses_reviewed_version(tmp_path, monkeypatch, changed):
    from launcher_lock import launcher_lock

    reviewed = "a" * 40
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("b" * 40 if changed == "revision" else reviewed)
    feed = tmp_path / ".codex/updates"
    feed.mkdir(parents=True)
    if changed.endswith(".json"):
        (feed / changed).touch()
    monkeypatch.setenv("DBV_LAUNCHER_REVISION", reviewed)

    async def interactive(args, env, *, on_confirm):
        assert args.start is False
        with launcher_lock(tmp_path / ".codex/standalone.lock"):
            pass  # Idle launcher must not block a newer release.
        on_confirm()
        assert changed == "none"
        with pytest.raises(RuntimeError), launcher_lock(tmp_path / ".codex/standalone.lock"):
            pytest.fail("another session acquired the installation lock")
        return 0

    monkeypatch.setitem(sys.modules, "run_bot", SimpleNamespace(run=interactive, prepare_environment=lambda port:{}))
    assert main(tmp_path) == (0 if changed == "none" else 75)


@pytest.mark.parametrize("input_lines", [[], ["q\n"]])
def test_console_aborted_before_start_never_claims_session(input_lines):
    from run_bot import run_console

    async def lines():
        for line in input_lines:
            yield line

    def unexpected_confirmation():
        pytest.fail("the session was confirmed without a Start gesture")

    asyncio.run(run_console(SimpleNamespace(start=False), {}, lines(), on_confirm=unexpected_confirmation))
