import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import dbv_release as release
import dbv_revision


def git(root, *args):
    return subprocess.check_output([release.git_executable(), "-c", f"safe.directory={root.as_posix()}",
                                    "-C", str(root), *args], text=True, encoding="utf-8").strip()


def commit(source, name, body):
    path = source / name
    path.parent.mkdir(exist_ok=True, parents=True)
    path.write_text(body, encoding="utf-8")
    git(source, "add", name)
    git(source, "-c", "user.name=Test", "-c", "user.email=local@example.invalid", "commit", "-qm", name)
    return git(source, "rev-parse", "HEAD")


def panel(path, build):
    path.mkdir(parents=True)
    for name in ("server/main.js", "static/chunks/shell.js", "routes-manifest.json", "build-manifest.json",
                 "required-server-files.json", "BUILD_ID"):
        file = path / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(build if name == "BUILD_ID" else "{}", encoding="utf-8")
    (path / "required-server-files.json").write_text(json.dumps(
        {"appDir": str(path.parent), "files": [".next/server/main.js", ".next/BUILD_ID"]}), encoding="utf-8")
    return path


@pytest.fixture
def repos(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "_ports_busy", lambda: False)
    source, installed = tmp_path / "source", tmp_path / "installation"
    source.mkdir()
    git(source, "init", "-qb", "master")
    commit(source, "engine.py", "old")
    base = commit(source, "recordings/route.json", "recording")
    git(tmp_path, "clone", "-q", str(source), str(installed))
    (installed / "recordings/route.json").write_text("owned", encoding="utf-8")
    return installed, source, base


def test_semver_across_branches_pending_and_installed(repos, monkeypatch):
    installed, source, base = repos
    monkeypatch.setattr(dbv_revision, "V1_COMMIT", base)
    git(source, "config", "user.name", "Version test")
    git(source, "config", "user.email", "local@example.invalid")

    def bump(subject, *, body=None):
        args = ["commit", "-q", "--allow-empty", "-m", subject]
        if body:
            args.extend(["-m", body])
        git(source, *args)
        return dbv_revision.version_for(source, git(source, "rev-parse", "HEAD"))

    assert dbv_revision.version_for(source, base) == "v1.0.0"
    assert bump("fix(ui): align text") == "v1.0.1"
    assert bump("feat(ui): show history") == "v1.1.0"
    assert bump("docs: document history") == "v1.1.1"
    git(source, "switch", "-qc", "upgrade", base)
    assert bump("feat(api)!: change contract") == "v2.0.0"
    assert bump("refactor(api): revise schema", body="BREAKING CHANGE: clients must migrate") == "v3.0.0"
    git(source, "switch", "-q", "master")
    git(source, "merge", "-q", "--no-ff", "-m", "merge upgrade", "upgrade")
    revision = git(source, "rev-parse", "HEAD")
    expected = f"v3.0.1 ({revision[:7]})"
    report = release.publish(installed, source, revision)
    assert report["pending_label"] == expected
    assert report["installed_label"] == f"v1.0.0 ({base[:7]})"
    source.rename(source.with_name("source-moved"))
    assert release.status(installed)["pending_label"] == expected
    assert release.apply_pending(installed)["installed_label"] == expected
    assert release.status(installed)["pending_label"] is None


@pytest.mark.parametrize("detached", [True, False])
def test_publish_then_apply_preserves_history_and_recordings(repos, detached):
    installed, source, base = repos
    if detached:
        git(installed, "checkout", "--detach", "-q", base)
    version = commit(source, "engine.py", "new")
    assert release.publish(installed, source, version)["pending"] == version
    assert (installed / "engine.py").read_text() == "old"
    source.rename(source.with_name("source-moved"))
    assert release.apply_pending(installed)["updated"]
    assert release.status(installed)["installed"] == version
    assert not release.apply_pending(installed)["updated"]
    assert (installed / "recordings/route.json").read_text() == "owned"
    assert git(installed, "branch", "--show-current") == ("" if detached else "master")
    assert git(installed, "rev-parse", "HEAD^") == base


def test_busy_does_not_change_files_or_consume_pending(repos, monkeypatch):
    installed, source, base = repos
    monkeypatch.setattr(release, "_ports_busy", lambda: True)
    assert release.status(installed)["state"] == "busy"
    assert release.apply_pending(installed)["state"] == "busy"
    version = commit(source, "engine.py", "new")
    release.publish(installed, source, version)
    assert release.apply_pending(installed)["pending"] == version
    assert git(installed, "rev-parse", "HEAD") == base
    monkeypatch.setattr(release, "_ports_busy", lambda: False)
    with release._file_lock(installed / ".codex/standalone.lock"):
        assert release.apply_pending(installed)["state"] == "busy"
    assert release.apply_pending(installed)["updated"]


def test_changes_overlapping_recordings_stop_git_and_restore_panel(repos):
    installed, source, base = repos
    old = panel(installed / "dashboard/.next", "old")
    build = panel(source / "dashboard/.next", "new")
    commit(source, "dashboard/app/page.tsx", "new")
    version = commit(source, "recordings/route.json", "upstream")
    release.publish(installed, source, version, build)
    with pytest.raises(RuntimeError):
        release.apply_pending(installed)
    assert git(installed, "rev-parse", "HEAD") == base
    assert (old / "BUILD_ID").read_text() == "old"
    assert (installed / "recordings/route.json").read_text() == "owned"
    assert release.status(installed)["pending"] == version


def test_bundle_integrity_is_checked_before_applying(repos):
    installed, source, base = repos
    version = commit(source, "engine.py", "new")
    release.publish(installed, source, version)
    bundle = next((installed / ".codex/updates").glob("*/code.bundle"))
    with bundle.open("ab") as handle:
        handle.write(b"oops")
    with pytest.raises(ValueError, match="Suma kontrolna"):
        release.apply_pending(installed)
    assert release.status(installed)["installed"] == base


@pytest.mark.parametrize("changed", ["dll/dbvbot.cpp", "requirements.txt", "dashboard/package-lock.json"])
def test_unsupported_dependencies_cannot_leave_half_an_update(repos, changed):
    installed, source, base = repos
    version = commit(source, changed, "unsupported")
    with pytest.raises(ValueError, match="zależności lub DLL"):
        release.publish(installed, source, version)
    assert release.status(installed)["pending"] is None
    assert git(installed, "rev-parse", "HEAD") == base


def test_renderer_requires_complete_build_and_rejects_corruption(repos):
    installed, source, base = repos
    version = commit(source, "dashboard/app/page.tsx", "new")
    with pytest.raises(ValueError, match="dołącz zbudowany panel"):
        release.publish(installed, source, version)
    old = panel(installed / "dashboard/.next", "old")
    build = panel(source / "dashboard/.next", "new")
    release.publish(installed, source, version, build)
    (next((installed / ".codex/updates").glob("*/panel")) / "static/chunks/shell.js").write_text("tampered")
    with pytest.raises(ValueError, match="Suma kontrolna panelu"):
        release.apply_pending(installed)
    assert git(installed, "rev-parse", "HEAD") == base
    assert (old / "BUILD_ID").read_text() == "old"


@pytest.mark.parametrize("head_updated", [True, False])
def test_interruption_between_panel_swap_and_commit_is_recoverable(repos, monkeypatch, head_updated):
    installed, source, base = repos
    old = panel(installed / "dashboard/.next", "old")
    build = panel(source / "dashboard/.next", "new")
    version = commit(source, "dashboard/app/page.tsx", "new")
    release.publish(installed, source, version, build)
    original = release._git

    def interrupt(root, *args):
        if args[0] == "merge":
            if head_updated:
                original(root, *args)
            raise KeyboardInterrupt("power interrupted")
        return original(root, *args)

    monkeypatch.setattr(release, "_git", interrupt)
    with pytest.raises(KeyboardInterrupt):
        release.apply_pending(installed)
    assert release.status(installed)["state"] == "recovery"
    monkeypatch.setattr(release, "_git", original)
    assert release.apply_pending(installed)["installed"] == version
    assert (old / "BUILD_ID").read_text() == "new"
    assert release.status(installed)["state"] == "current"
    assert not list(old.parent.glob(".next-backup-*"))


def test_invalid_manifest_path_is_rejected(repos):
    installed, source, base = repos
    feed = installed / ".codex/updates"
    feed.mkdir(parents=True)
    release._write_json(feed / "pending.json", dict(format=1, package="../../outside", base=base,
                        revision=base, panel=None, bundle="1" * 64))
    with pytest.raises(ValueError, match="Nieprawidłowy manifest"):
        release.apply_pending(installed)


def test_release_refuses_to_overwrite_ignored_files_or_install_reserved_data(repos):
    installed, source, base = repos
    with (installed / ".git/info/exclude").open("a") as out:
        out.write("\nlocal-plugin.py\n")
    (installed / "local-plugin.py").write_text("user content")
    version = commit(source, "local-plugin.py", "release content")
    release.publish(installed, source, version)
    with pytest.raises(RuntimeError):
        release.apply_pending(installed)
    assert (installed / "local-plugin.py").read_text() == "user content"
    assert git(installed, "rev-parse", "HEAD") == base
    blocked = commit(source, "bot_settings.json", "do not publish")
    with pytest.raises(ValueError, match="lokalne dane"):
        release.publish(installed, source, blocked)
    assert release.status(installed)["pending"] == version


def test_only_canonical_clean_renderer_is_packaged(repos, tmp_path):
    installed, source, _ = repos
    version = commit(source, "dashboard/app/page.tsx", "new")
    build = panel(source / "dashboard/.next", "new")
    outside = panel(tmp_path / "other/.next", "other")
    with pytest.raises(ValueError, match="czystej kopii"):
        release.publish(installed, source, version, outside)
    (source / "dashboard/app/page.tsx").write_text("changed after commit")
    with pytest.raises(ValueError, match="czystej kopii"):
        release.publish(installed, source, version, build)
    git(source, "restore", "--", "dashboard/app/page.tsx")
    (build / "required-server-files.json").write_text((outside / "required-server-files.json").read_text())
    with pytest.raises(ValueError, match="innego katalogu"):
        release.publish(installed, source, version, build)


@pytest.mark.parametrize("crash_state", ["staged", "backup", "moved", "rollback_after_first_rename",
                                         "rollback_after_second_rename"])
def test_all_rename_boundaries_preserve_original_panel(repos, crash_state):
    installed, source, _ = repos
    old = panel(installed / "dashboard/.next", "old")
    version = commit(source, "dashboard/app/page.tsx", "new")
    build = panel(source / "dashboard/.next", "new")
    release.publish(installed, source, version, build)
    feed = release._updates(installed)
    tx = release._manifest(feed / "pending.json")
    release._write_json(feed / "transaction.json", tx)
    current, stage, backup = release._panel_paths(installed, tx)
    release._panel_tree(feed / tx["package"] / "panel", stage)
    if crash_state != "staged":
        current.rename(backup)
    if crash_state in ("moved", "rollback_after_first_rename", "rollback_after_second_rename"):
        stage.rename(current)
    if crash_state.startswith("rollback_"):
        current.rename(stage)
    if crash_state == "rollback_after_second_rename":
        backup.rename(current)
    assert not release._recover(installed, feed, tx)
    assert (old / "BUILD_ID").read_text() == "old"
    assert not backup.exists()
    assert not stage.exists()
    assert release.status(installed)["pending"] == version


def test_defer_keeps_the_package_and_only_removes_exact_queue_entry(repos, monkeypatch):
    installed, source, base = repos
    version = commit(source, "engine.py", "new")
    release.publish(installed, source, version)
    feed = release._updates(installed)
    manifest = release._manifest(feed / "pending.json")
    with pytest.raises(ValueError, match="uległy zmianie"):
        release.defer_pending(installed, base)
    release._write_json(feed / "transaction.json", manifest)
    assert not release.can_defer(installed, version)
    with pytest.raises(ValueError, match="rozpoczętą aktualizację"):
        release.defer_pending(installed, version)
    (feed / "transaction.json").unlink()
    (installed / "engine.py").write_text("modified")
    assert not release.can_defer(installed, version)
    with pytest.raises(ValueError, match="lokalne zmiany"):
        release.defer_pending(installed, version)
    git(installed, "restore", "engine.py")
    monkeypatch.setattr(release, "_ports_busy", lambda: True)
    with pytest.raises(RuntimeError, match="zajęte"):
        release.defer_pending(installed, version)
    monkeypatch.setattr(release, "_ports_busy", lambda: False)
    assert release.can_defer(installed, version)
    result = release.defer_pending(installed, version)
    assert result["installed"] == base and result["deferred"] == version
    assert result["state"] == "current" and result["pending"] is None
    assert (feed / manifest["package"] / "code.bundle").is_file()
    assert release._manifest(feed / "deferred.json") == manifest
    assert not release.apply_pending(installed)["updated"]
    assert (installed / "recordings/route.json").read_text() == "owned"


def test_defer_refuses_missing_initial_panel(repos):
    installed, source, _ = repos
    version = commit(source, "dashboard/app/page.tsx", "new")
    release.publish(installed, source, version, panel(source / "dashboard/.next", "new"))
    with pytest.raises((ValueError, FileNotFoundError)):
        release.defer_pending(installed, version)
    assert release.status(installed)["pending"] == version


@pytest.mark.parametrize("prebuilt", [True, False])
def test_publication_cli_builds_or_checks_stamp_before_publishing(repos, monkeypatch, prebuilt):
    installed, source, _ = repos
    version = commit(source, "dashboard/app/page.tsx", "new")
    build = panel(source / "dashboard/.next", "verified")
    calls = []
    def build_for_release(root, repo, sha):
        calls.append(("build", root, repo, sha))
        return build
    def verify_prebuilt(repo, sha, artifact, *, copied_digest=None):
        calls.append(("verify-copy" if copied_digest else "verify", repo, sha, artifact))
        return build
    monkeypatch.setitem(sys.modules, "launcher_panel_build",
                        SimpleNamespace(build_for_release=build_for_release, verify_prebuilt=verify_prebuilt))
    argv = ["dbv_release.py", "--installation", str(installed), "publish", str(source), version]
    if prebuilt:
        argv.extend(["--dashboard", str(build)])
    monkeypatch.setattr(sys, "argv", argv)
    release.main()
    first_call = ("verify", source, version, build) if prebuilt else ("build", installed, source, version)
    assert calls == [first_call, ("verify-copy", source, version, build)]
    assert release.status(installed)["pending"] == version
