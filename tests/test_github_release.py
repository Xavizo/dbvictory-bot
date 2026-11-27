import hashlib
import io
import json
from contextlib import nullcontext
import shutil
import zipfile

import pytest

import dbv_revision
import github_release as gr

OLD = "a" * 40
NEW = "b" * 40


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest(files):
    return {"format": 1, "files": {p: [sha(d), len(d)] for p, d in files.items()}}


def install_old(root, files):
    files = {**files, "release.json": json.dumps({"version": "v1.0.0", "revision": OLD}).encode()}
    for path, data in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(data)
    (root / gr.MANIFEST_FILE).write_text(json.dumps(manifest(files)))


def package(files, version="v1.1.0", revision=NEW, extra=None):
    files = {**files, "release.json": json.dumps({"version": version, "revision": revision}).encode()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(f"{gr.PACKAGE_ROOT}/{path}", data)
        archive.writestr(f"{gr.PACKAGE_ROOT}/{gr.MANIFEST_FILE}", json.dumps(manifest(files)))
        for path, data in (extra or {}).items():
            archive.writestr(path, data)
    data = buffer.getvalue()
    latest = {"format": 1, "version": version, "revision": revision, "asset": f"DBVictory-Bot-{version}.zip",
              "sha256": sha(data), "size": len(data),
              "url": f"https://github.com/{gr.DEFAULT_REPO}/releases/download/{version}/x.zip"}
    return latest, data


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "_lock", lambda root: nullcontext())
    monkeypatch.setattr(gr, "_busy", lambda root: False)
    return tmp_path


def test_release_metadata_replaces_git_version(root):
    install_old(root, {})
    assert gr.is_release_install(root)
    assert dbv_revision.installed_revision(root) == OLD
    assert dbv_revision.version_for(root, OLD) == "v1.0.0"
    assert dbv_revision.checkout_revision(root) == OLD
    (root / ".git").mkdir()
    assert not gr.is_release_install(root)


def test_offline_check_never_blocks_play(root):
    install_old(root, {})

    def offline(repo):
        raise OSError("brak sieci")
    report = gr.status(root, fetch=offline)
    assert report["state"] == "current" and "brak sieci" in report["message"]


def test_available_then_deferred_until_a_newer_release(root):
    install_old(root, {})
    latest, _ = package({})
    report = gr.status(root, fetch=lambda repo: latest)
    assert report["state"] == "available" and report["pending"] == NEW and report["can_defer"]
    gr.defer(root, NEW, fetch=lambda repo: latest)
    assert gr.status(root, fetch=lambda repo: latest)["state"] == "current"
    newer, _ = package({}, version="v1.2.0", revision="c" * 40)
    assert gr.status(root, fetch=lambda repo: newer)["state"] == "available"


def test_install_replaces_program_files_and_keeps_player_data(root):
    install_old(root, {"bot.py": b"old", "gone.py": b"x", "recordings/route.json": b"shipped"})
    (root / "bot_settings.json").write_text("mine")
    (root / "local_data").mkdir()
    (root / "local_data" / "atlas.sqlite3").write_text("map")
    (root / "recordings" / "route.json").write_text("edited by player")
    latest, data = package({"bot.py": b"new", "launcher/new.js": b"js", "recordings/route.json": b"shipped2",
                            "recordings/extra.json": b"seed", "bot_settings.json": b"default"})
    report = gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))
    assert report["state"] == "updated" and report["restart"]
    assert (root / "bot.py").read_bytes() == b"new"
    assert (root / "launcher" / "new.js").read_bytes() == b"js"
    assert not (root / "gone.py").exists()
    assert (root / "bot_settings.json").read_text() == "mine"
    assert (root / "local_data" / "atlas.sqlite3").read_text() == "map"
    assert (root / "recordings" / "route.json").read_text() == "edited by player"
    assert (root / "recordings" / "extra.json").read_bytes() == b"seed"
    assert dbv_revision.read_release(root)["version"] == "v1.1.0"
    assert not list(root.rglob("*.dbvold-*")) and not (gr._feed(root) / "transaction.json").exists()


def test_corrupted_download_changes_nothing(root):
    install_old(root, {"bot.py": b"old"})
    latest, data = package({"bot.py": b"new"})
    with pytest.raises(ValueError, match="Suma kontrolna"):
        gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data[:-1] + b"!"))
    assert (root / "bot.py").read_bytes() == b"old"


def test_paths_outside_the_package_are_rejected():
    for path in ("../evil.py", "/abs.py", "C:/x.py", "a\\b.py", "a/./b.py"):
        with pytest.raises(ValueError):
            gr._clean_path(path)


def test_interrupted_swap_rolls_back(root, monkeypatch):
    install_old(root, {"a.py": b"old-a", "b.py": b"old-b"})
    latest, data = package({"a.py": b"new-a", "b.py": b"new-b", "c.py": b"new-c"})
    real = gr.os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 4:
            raise OSError("dysk")
        return real(src, dst)
    monkeypatch.setattr(gr.os, "replace", flaky)
    with pytest.raises(OSError):
        gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))
    monkeypatch.setattr(gr.os, "replace", real)
    assert (root / "a.py").read_bytes() == b"old-a"
    assert (root / "b.py").read_bytes() == b"old-b"
    assert not (root / "c.py").exists()
    assert dbv_revision.read_release(root)["version"] == "v1.0.0"


def test_journal_left_by_a_crash_is_recovered_at_next_check(root):
    install_old(root, {"a.py": b"old"})
    tx = "f" * 32
    (root / "a.py").rename(root / f"a.py.dbvold-{tx}")
    (root / "a.py").write_bytes(b"new")
    (root / "n.py").write_bytes(b"added")
    gr._write_json(gr._feed(root) / "transaction.json", {"tx": tx, "ops": [
        {"path": "a.py", "write": True, "existed": True}, {"path": "n.py", "write": True, "existed": False},
        {"path": "z.py", "write": True, "existed": False}]})
    assert gr.status(root, check_only=True, fetch=lambda r: (_ for _ in ()).throw(OSError()))["state"] == "recovery"
    gr.status(root, fetch=lambda r: (_ for _ in ()).throw(OSError()))
    assert (root / "a.py").read_bytes() == b"old"
    assert not (root / "n.py").exists()
    assert not (gr._feed(root) / "transaction.json").exists()


def test_latest_release_needs_assets_from_its_own_repository():
    meta = {"format": 1, "version": "v1.1.0", "revision": NEW, "asset": "DBVictory-Bot-v1.1.0.zip",
            "sha256": "0" * 64, "size": 10}
    base = f"https://github.com/{gr.DEFAULT_REPO}/releases/download/v1.1.0/"
    info = {"assets": [{"name": "release.json", "browser_download_url": base + "release.json"},
                       {"name": meta["asset"], "browser_download_url": base + meta["asset"]}]}

    def get(url, **kw):
        return json.dumps(info if "api.github.com" in url else meta).encode()
    assert gr.fetch_latest(gr.DEFAULT_REPO, get)["url"] == base + meta["asset"]
    info["assets"][1]["browser_download_url"] = "https://example.com/x.zip"
    with pytest.raises(ValueError):
        gr.fetch_latest(gr.DEFAULT_REPO, get)


def test_update_that_ships_fewer_files_leaves_no_empty_program_folders(root):
    install_old(root, {"bot.py": b"old", "dashboard/node_modules/typescript/lib/tsc.js": b"x",
                       "dashboard/node_modules/next/dist/server.js": b"s", "venv/Scripts/tcl/init.tcl": b"t",
                       "venv/Scripts/python.exe": b"py"})
    (root / "dashboard" / "node_modules" / "keep-empty-elsewhere").mkdir()
    latest, data = package({"bot.py": b"new", "dashboard/node_modules/next/dist/server.js": b"s",
                            "venv/Scripts/python.exe": b"py"})
    assert gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))["updated"]
    assert not (root / "dashboard" / "node_modules" / "typescript").exists()
    assert not (root / "venv" / "Scripts" / "tcl").exists()
    assert (root / "dashboard" / "node_modules" / "next" / "dist" / "server.js").is_file()
    assert (root / "venv" / "Scripts" / "python.exe").is_file()


def test_empty_folders_left_by_an_older_updater_go_at_the_next_check(root):
    install_old(root, {"bot.py": b"old", "dashboard/node_modules/next/package.json": b"{}"})
    (root / "dashboard" / "node_modules" / "@next" / "swc-win32-x64-msvc").mkdir(parents=True)
    gr.status(root, fetch=lambda repo: (_ for _ in ()).throw(OSError("offline")))
    assert not (root / "dashboard" / "node_modules" / "@next").exists()
    assert (root / "dashboard" / "node_modules" / "next" / "package.json").is_file()


def patch_package(full_files, changed, version="v1.1.0", revision=NEW, base="v1.0.0"):
    """A full release plus its patch against `base`, as fetch_latest returns them."""
    latest, data = package(full_files, version, revision)
    files = {**full_files, "release.json": json.dumps({"version": version, "revision": revision}).encode()}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path in [*changed, "release.json"]:
            archive.writestr(f"{gr.PACKAGE_ROOT}/{path}", files[path])
        archive.writestr(f"{gr.PACKAGE_ROOT}/{gr.MANIFEST_FILE}", json.dumps(manifest(files)))
    patch = buffer.getvalue()
    asset = f"DBVictory-Bot-{version}-patch-{base}.zip"
    latest["patch"] = {"from": base, "asset": asset, "sha256": sha(patch), "size": len(patch),
                       "url": f"https://github.com/{gr.DEFAULT_REPO}/releases/download/{version}/{asset}"}
    return latest, data, patch


def opener_for(latest, data, patch, log):
    def opener(request, timeout):
        url = request.full_url
        log.append(url.rsplit("/", 1)[-1])
        return Response(patch if url == latest["patch"]["url"] else data)
    return opener


def test_update_downloads_only_the_patch_when_installed_release_is_its_base(root):
    install_old(root, {"bot.py": b"old", "big.dll": b"same", "gone.py": b"x"})
    latest, data, patch = patch_package({"bot.py": b"new", "big.dll": b"same"}, ["bot.py"])
    report = gr.status(root, fetch=lambda repo: latest)
    assert report["state"] == "available"
    log = []
    assert gr.install(root, NEW, fetch=lambda repo: latest, opener=opener_for(latest, data, patch, log))["updated"]
    assert log == [latest["patch"]["asset"]]
    assert (root / "bot.py").read_bytes() == b"new" and (root / "big.dll").read_bytes() == b"same"
    assert not (root / "gone.py").exists()
    assert dbv_revision.read_release(root)["version"] == "v1.1.0"
    assert json.loads((root / gr.MANIFEST_FILE).read_text())["files"].keys() == {"bot.py", "big.dll", "release.json"}
    assert not list(gr._feed(root).glob("*.zip")) and not list(gr._feed(root).glob("stage-*"))


def test_patch_falls_back_to_the_full_package(root):
    # Another base version, a program file missing from the installation, and a corrupt patch.
    for case in ("other-base", "missing-file", "corrupt"):
        for child in list(root.iterdir()):
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        install_old(root, {"bot.py": b"old", "big.dll": b"same"})
        base = "v0.9.0" if case == "other-base" else "v1.0.0"
        latest, data, patch = patch_package({"bot.py": b"new", "big.dll": b"same"}, ["bot.py"], base=base)
        if case == "missing-file":
            (root / "big.dll").unlink()
        if case == "corrupt":
            patch = patch[:-1] + b"!"
        log = []
        assert gr.install(root, NEW, fetch=lambda repo: latest, opener=opener_for(latest, data, patch, log))["updated"]
        assert log == ([latest["patch"]["asset"]] if case == "missing-file" or case == "corrupt" else []) + ["x.zip"], case
        assert (root / "bot.py").read_bytes() == b"new" and (root / "big.dll").read_bytes() == b"same", case


def test_patch_skips_examples_the_player_deleted(root):
    install_old(root, {"bot.py": b"old", "recordings/route.json": b"example"})
    (root / "recordings" / "route.json").unlink()
    latest, data, patch = patch_package({"bot.py": b"new", "recordings/route.json": b"example"}, ["bot.py"])
    log = []
    assert gr.install(root, NEW, fetch=lambda repo: latest, opener=opener_for(latest, data, patch, log))["updated"]
    assert log == [latest["patch"]["asset"]] and not (root / "recordings" / "route.json").exists()


def test_bad_patch_metadata_only_means_a_full_download():
    meta = {"format": 1, "version": "v1.1.0", "revision": NEW, "asset": "DBVictory-Bot-v1.1.0.zip",
            "sha256": "0" * 64, "size": 10,
            "patch": {"from": "v1.0.0", "asset": "DBVictory-Bot-v1.1.0-patch-v1.0.0.zip", "sha256": "1" * 64, "size": 5}}
    base = f"https://github.com/{gr.DEFAULT_REPO}/releases/download/v1.1.0/"
    names = ["release.json", meta["asset"], meta["patch"]["asset"]]
    info = {"assets": [{"name": n, "browser_download_url": base + n} for n in names]}

    def get(url, **kw):
        return json.dumps(info if "api.github.com" in url else meta).encode()
    assert gr.fetch_latest(gr.DEFAULT_REPO, get)["patch"]["url"] == base + meta["patch"]["asset"]
    info["assets"][2]["browser_download_url"] = "https://example.com/p.zip"
    assert "patch" not in gr.fetch_latest(gr.DEFAULT_REPO, get)
    meta["patch"]["asset"] = "../evil.zip"
    assert "patch" not in gr.fetch_latest(gr.DEFAULT_REPO, get)


def test_a_removed_file_held_open_does_not_roll_back_the_update(root, monkeypatch):
    pak = "dashboard/node_modules/electron/dist/resources.pak"
    install_old(root, {"bot.py": b"old", pak: b"pak"})
    latest, data = package({"bot.py": b"new"})
    real_replace = gr.os.replace

    def replace(src, dst):
        if str(src).endswith("resources.pak"):
            raise PermissionError("in use by the running launcher")
        return real_replace(src, dst)
    real_unlink = type(root).unlink

    def unlink(path, missing_ok=False):
        if path.name == "resources.pak":
            raise PermissionError("in use by the running launcher")
        return real_unlink(path, missing_ok=missing_ok)
    with monkeypatch.context() as patch:
        patch.setattr(gr.os, "replace", replace)
        patch.setattr(type(root), "unlink", unlink)
        assert gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))["updated"]
    assert (root / "bot.py").read_bytes() == b"new"
    assert (root / pak).exists()  # still open: stays for now
    assert pak not in json.loads((root / gr.MANIFEST_FILE).read_text())["files"]
    gr.status(root, fetch=lambda repo: (_ for _ in ()).throw(OSError("offline")))
    assert not (root / "dashboard" / "node_modules" / "electron").exists()  # deleted at the next start, folders pruned
    assert not (gr._feed(root) / "obsolete.json").exists()


def test_a_changed_file_held_open_still_rolls_back(root, monkeypatch):
    install_old(root, {"bot.py": b"old", "app.pak": b"pak"})
    latest, data = package({"bot.py": b"new", "app.pak": b"pak2"})
    real_replace = gr.os.replace

    def replace(src, dst):
        if str(src).endswith("app.pak") and "stage-" not in str(src):
            raise PermissionError("in use")
        return real_replace(src, dst)
    monkeypatch.setattr(gr.os, "replace", replace)
    with pytest.raises(PermissionError):
        gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))
    assert (root / "bot.py").read_bytes() == b"old" and (root / "app.pak").read_bytes() == b"pak"


def test_install_reports_download_extract_and_swap_progress_then_clears_it(root, monkeypatch):
    install_old(root, {"a.py": b"old-a"})
    latest, data = package({"a.py": b"new-a", "b.py": b"new-b"})
    seen = []
    real = gr.Progress.__call__

    def spy(self, stage, done, total, **kw):
        seen.append((stage, done, total))
        return real(self, stage, done, total, **kw)
    monkeypatch.setattr(gr.Progress, "__call__", spy)
    gr.install(root, NEW, fetch=lambda repo: latest, opener=lambda req, timeout: Response(data))
    stages = [s for s, _, _ in seen]
    assert stages.index("download") < stages.index("extract") < stages.index("apply")
    assert ("download", len(data), len(data)) in seen
    extract = [(d, t) for s, d, t in seen if s == "extract"]
    assert extract[-1][0] == extract[-1][1]
    assert not (root / ".dbvictory" / "updates" / "github" / "progress.json").exists()


def test_progress_is_throttled_but_always_writes_the_end(tmp_path):
    clock = [100.0]
    progress = gr.Progress(tmp_path / "progress.json", clock=lambda: clock[0])
    progress("download", 1, 10, unit="bytes", kind="patch")
    first = json.loads((tmp_path / "progress.json").read_text())
    progress("download", 5, 10, unit="bytes", kind="patch")
    assert json.loads((tmp_path / "progress.json").read_text()) == first  # within 0.25 s
    progress("download", 10, 10, unit="bytes", kind="patch")
    last = json.loads((tmp_path / "progress.json").read_text())
    assert last == {"stage": "download", "done": 10, "total": 10, "unit": "bytes", "kind": "patch", "started": 100.0}


def test_a_progress_file_that_cannot_be_removed_does_not_fail_the_install(tmp_path, monkeypatch):
    progress = gr.Progress(tmp_path / "progress.json")
    progress("apply", 1, 1)

    def locked(self, missing_ok=False):
        raise PermissionError("in use")
    monkeypatch.setattr(type(tmp_path), "unlink", locked)
    progress.clear()


TEST = "c" * 40


def published(latest, when):
    return {**latest, "published": when}


def test_a_test_build_sorts_before_its_release_and_after_older_ones():
    assert gr._version("v1.2.0-test.abcdef0") < gr._version("v1.2.0") < gr._version("v1.2.1-test.abcdef0")
    assert gr._version("v1.2.0-test.ABCDEF0") is None and gr._version("v1.2.0-beta") is None
    assert gr.is_test_version("v1.2.0-test.abcdef0") and not gr.is_test_version("v1.2.0")


def test_default_channel_never_looks_at_test_builds(root):
    install_old(root, {})
    assert gr.read_channel(root) == "stable"
    stable = published(package({}, version="v1.0.0", revision=OLD)[0], "2026-11-01T00:00:00Z")

    def no_tests(repo):
        raise AssertionError("the default channel must not ask for test builds")
    report = gr.status(root, fetch=lambda repo: stable, fetch_tests=no_tests)
    assert report["state"] == "current" and report["channel"] == "stable"


def test_test_channel_offers_the_newest_build_and_installs_it(root):
    install_old(root, {"bot.py": b"old"})
    stable = published(package({}, version="v1.0.0", revision=OLD)[0], "2026-11-01T00:00:00Z")
    build, data = package({"bot.py": b"test"}, version="v1.1.0-test.ccccccc", revision=TEST)
    build = published(build, "2026-11-02T00:00:00Z")
    assert gr.set_channel(root, "test") == {"channel": "test"}
    kw = {"fetch": lambda repo: stable, "fetch_tests": lambda repo: build}
    report = gr.status(root, **kw)
    assert report["state"] == "available" and report["pending"] == TEST and report["channel"] == "test"
    assert "wersja testowa" in report["message"]
    assert gr.install(root, TEST, opener=lambda req, timeout: Response(data), **kw)["updated"]
    assert (root / "bot.py").read_bytes() == b"test"
    assert dbv_revision.read_release(root)["version"] == "v1.1.0-test.ccccccc"
    assert gr.status(root, **kw)["state"] == "current"


def test_test_channel_prefers_a_regular_release_published_after_the_test_build(root):
    install_old(root, {})
    gr.set_channel(root, "test")
    stable = published(package({}, version="v1.1.0", revision=NEW)[0], "2026-11-03T00:00:00Z")
    build = published(package({}, version="v1.1.0-test.ccccccc", revision=TEST)[0], "2026-11-02T00:00:00Z")
    report = gr.status(root, fetch=lambda repo: stable, fetch_tests=lambda repo: build)
    assert report["pending"] == NEW
    assert gr.status(root, fetch=lambda repo: stable, fetch_tests=lambda repo: None)["pending"] == NEW


def test_leaving_the_test_channel_returns_to_the_regular_release_even_if_older(root):
    install_old(root, {"bot.py": b"test"})
    release = root / "release.json"
    release.write_text(json.dumps({"version": "v1.2.0-test.ccccccc", "revision": TEST}))
    (root / gr.MANIFEST_FILE).write_text(json.dumps(manifest({"bot.py": b"test", "release.json": release.read_bytes()})))
    stable, data = package({"bot.py": b"stable"}, version="v1.1.0", revision=NEW)
    report = gr.status(root, fetch=lambda repo: stable)
    assert report["state"] == "available" and report["pending"] == NEW
    assert gr.install(root, NEW, fetch=lambda repo: stable, opener=lambda req, timeout: Response(data))["updated"]
    assert (root / "bot.py").read_bytes() == b"stable"


def test_unknown_channel_is_refused_and_a_broken_file_means_stable(root):
    install_old(root, {})
    with pytest.raises(ValueError):
        gr.set_channel(root, "nightly")
    (gr._feed(root)).mkdir(parents=True, exist_ok=True)
    (gr._feed(root) / "channel.json").write_text("{bad")
    assert gr.read_channel(root) == "stable"


def test_fetch_test_takes_the_newest_test_pre_release_only():
    base = f"https://github.com/{gr.DEFAULT_REPO}/releases/download/"

    def release(tag, when, prerelease=True, draft=False):
        return {"tag_name": tag, "published_at": when, "prerelease": prerelease, "draft": draft,
                "assets": [{"name": "release.json", "browser_download_url": f"{base}{tag}/release.json"},
                           {"name": f"DBVictory-Bot-{tag}.zip",
                            "browser_download_url": f"{base}{tag}/DBVictory-Bot-{tag}.zip"}]}
    listing = [release("v1.3.0", "2026-11-09T00:00:00Z", prerelease=False),
               release("v1.3.0-test.1111111", "2026-11-08T00:00:00Z", draft=True),
               release("v1.2.9-rc1", "2026-11-07T00:00:00Z"),
               release("v1.2.0-test.aaaaaaa", "2026-11-05T00:00:00Z"),
               release("v1.2.1-test.bbbbbbb", "2026-11-06T00:00:00Z")]

    def get(url, **kw):
        if "api.github.com" in url:
            assert "/releases?" in url
            return json.dumps(listing).encode()
        tag = url.split("/")[-2]
        return json.dumps({"format": 1, "version": tag, "revision": "d" * 40, "asset": f"DBVictory-Bot-{tag}.zip",
                           "sha256": "0" * 64, "size": 10}).encode()
    found = gr.fetch_test(gr.DEFAULT_REPO, get)
    assert found["version"] == "v1.2.1-test.bbbbbbb" and found["published"] == "2026-11-06T00:00:00Z"
    listing[:] = listing[:3]
    assert gr.fetch_test(gr.DEFAULT_REPO, get) is None


def test_the_latest_regular_release_must_not_be_a_test_build():
    tag = "v1.2.0-test.aaaaaaa"
    base = f"https://github.com/{gr.DEFAULT_REPO}/releases/download/{tag}/"
    info = {"assets": [{"name": "release.json", "browser_download_url": base + "release.json"},
                       {"name": f"DBVictory-Bot-{tag}.zip", "browser_download_url": base + f"DBVictory-Bot-{tag}.zip"}]}

    def get(url, **kw):
        return json.dumps(info if "api.github.com" in url else {
            "format": 1, "version": tag, "revision": "d" * 40, "asset": f"DBVictory-Bot-{tag}.zip",
            "sha256": "0" * 64, "size": 10}).encode()
    with pytest.raises(ValueError):
        gr.fetch_latest(gr.DEFAULT_REPO, get)
