import hashlib
import io
import json
from contextlib import nullcontext
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
