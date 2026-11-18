from datetime import datetime

import pytest

from dbv_revision import read_head_revision


REVISION = "9e7edf4067ab84510b53a4bf85574d456245f957"
OLDER = "ab" * 20


def test_actual_engine_label_without_git_on_path(tmp_path, monkeypatch):
    import mcp_server

    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(REVISION + "\n")
    monkeypatch.setenv("PATH", "")
    version, started = mcp_server._engine_label(tmp_path, mcp_server.checkout_revision(tmp_path)).split(" · start ")
    assert version == "Silnik 9e7edf4"
    assert datetime.fromisoformat(started).utcoffset() is not None


@pytest.mark.parametrize("linked_worktree", [False, True])
@pytest.mark.parametrize("loose_ref", [False, True])
def test_branch_resolution_uses_exact_ref_and_prefers_loose_value(
        tmp_path, linked_worktree, loose_ref):
    root = tmp_path / "checkout"
    root.mkdir()
    if linked_worktree:
        common = tmp_path / "repository"
        git_dir = common / "worktrees" / "test"
        git_dir.mkdir(parents=True)
        (root / ".git").write_text("gitdir: ../repository/worktrees/test\n")
        (git_dir / "commondir").write_text("../..\n")
    else:
        common = git_dir = root / ".git"
        git_dir.mkdir()
    ref = "refs/heads/codex/update"
    (git_dir / "HEAD").write_text("ref: " + ref + "\n")
    packed = OLDER if loose_ref else REVISION
    (common / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{OLDER} {ref}-old\n{packed} {ref}\n")
    if loose_ref:
        (common / ref).parent.mkdir(parents=True)
        (common / ref).write_text(REVISION + "\n")
    assert read_head_revision(root) == REVISION


@pytest.mark.parametrize("head", ["", "short", "ref: refs/heads/missing", "ref: refs/../secret"])
def test_invalid_or_unresolved_head_is_unknown(tmp_path, head):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(head)
    (git_dir / "secret").write_text(REVISION)
    assert read_head_revision(tmp_path) is None
