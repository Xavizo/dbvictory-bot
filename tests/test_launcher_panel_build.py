import json
from types import SimpleNamespace

import pytest

import launcher_panel_build as panel


def test_compilation_receipt_rejects_changed_files_and_wrong_release(monkeypatch, tmp_path):
    source, installed = tmp_path / "source", tmp_path / "installed"
    ui = source / "dashboard"
    next_package = ui / "node_modules/next/package.json"
    next_package.parent.mkdir(parents=True)
    next_package.write_text("{}")
    built = ui / ".next"
    built.mkdir()
    revision = "a" * 40
    digest = ["1" * 64]
    monkeypatch.setattr(panel, "_head", lambda root: revision)
    monkeypatch.setattr(panel, "_git", lambda *args: "")
    monkeypatch.setattr(panel.shutil, "which", lambda *args, **kwargs: "npm")
    monkeypatch.setattr(panel, "_panel_tree", lambda *args, **kwargs: digest[0])
    calls = []

    def build(command, cwd, env):
        assert command == ["npm", "run", "build"] and cwd == ui
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(panel.subprocess, "run", build)
    with pytest.raises(ValueError, match="Brak potwierdzonej"):
        panel.verify_prebuilt(source, revision, built)
    assert panel.build_for_release(installed, source, revision) == built
    assert len(calls) == 1
    assert panel.verify_prebuilt(source, revision, built) == built
    assert json.loads(panel._receipt(source).read_text())["revision"] == revision
    digest[0] = "2" * 64
    with pytest.raises(ValueError, match="zmieniły się"):
        panel.verify_prebuilt(source, revision, built)
    with pytest.raises(ValueError, match="inne wydanie"):
        panel.verify_prebuilt(source, "b" * 40, built)
    with pytest.raises(ValueError, match="oddzielnej"):
        panel.build_for_release(source, source, revision)
    monkeypatch.setattr(panel.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match="Kompilacja"):
        panel.build_for_release(installed, source, revision)
    assert not panel._receipt(source).exists()
