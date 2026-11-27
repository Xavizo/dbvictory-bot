import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_package  # noqa: E402


def test_patch_holds_only_changed_files_and_the_full_list(tmp_path):
    stage = tmp_path / "DBVictory-Bot"
    for rel, data in {"same.dll": "s", "bot.py": "new", "added.js": "a",
                      "release-manifest.json": '{"format": 1, "version": "v1.1.0", "files": {}}'}.items():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_text(data)
    files = release_package.collect(stage)
    previous = tmp_path / "previous.json"
    old = {"same.dll": files["same.dll"], "bot.py": ["0" * 64, 3], "gone.py": ["1" * 64, 1]}
    previous.write_text(json.dumps({"format": 1, "version": "v1.0.0", "files": old}))
    patch = release_package.make_patch(stage, files, previous, "v1.1.0", tmp_path)
    assert patch["from"] == "v1.0.0" and patch["asset"] == "DBVictory-Bot-v1.1.0-patch-v1.0.0.zip"
    with zipfile.ZipFile(tmp_path / patch["asset"]) as archive:
        assert sorted(archive.namelist()) == ["DBVictory-Bot/added.js", "DBVictory-Bot/bot.py",
                                              "DBVictory-Bot/release-manifest.json"]


def test_no_patch_without_a_usable_base(tmp_path):
    stage = tmp_path / "DBVictory-Bot"
    stage.mkdir()
    (tmp_path / "bad.json").write_text("{}")
    assert release_package.make_patch(stage, {}, tmp_path / "bad.json", "v1.1.0", tmp_path) is None
    assert release_package.make_patch(stage, {}, tmp_path / "missing.json", "v1.1.0", tmp_path) is None
    (tmp_path / "same.json").write_text(json.dumps({"version": "v1.1.0", "files": {}}))
    assert release_package.make_patch(stage, {}, tmp_path / "same.json", "v1.1.0", tmp_path) is None
