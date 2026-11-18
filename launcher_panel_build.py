"""Prepare and identify the renderer from a tested local checkout."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from dbv_release import _file_lock, _git, _head, _local_child, _panel_tree, _repo, _sha, _write_json


def _source_ui(source, revision):
    if _head(source) != _sha(revision):
        raise ValueError("Kopia źródłowa wskazuje inne wydanie.")
    if _git(source, "status", "--porcelain", "--untracked-files=all", "--", "dashboard",
            ":(exclude)dashboard/.next/**", ":(exclude)dashboard/node_modules/**",
            ":(exclude)dashboard/public/local-spells/**"):
        raise ValueError("Przed kompilacją zapisz zmiany panelu w przetestowanym commicie.")
    return _local_child(source, "dashboard")


def _receipt(source):
    return _local_child(_local_child(source, ".codex"), "launcher-panel-build.json")


def verify_prebuilt(source, revision, directory, *, copied_digest=None):
    ui = _source_ui(source, revision)
    build = _local_child(ui, ".next")
    if directory.resolve() != build:
        raise ValueError("Wybierz dashboard/.next z przetestowanej kopii.")
    try:
        receipt = json.loads(_receipt(source).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        receipt = None
    if not isinstance(receipt, dict) or receipt.get("revision") != revision:
        raise ValueError("Brak potwierdzonej kompilacji dla tego wydania. Opublikuj bez --dashboard, aby ją wykonać.")
    digest = copied_digest if copied_digest is not None else _panel_tree(build, build_source=ui)
    if receipt.get("digest") != digest:
        raise ValueError("Pliki panelu zmieniły się od kompilacji. Opublikuj bez --dashboard.")
    return build


def build_for_release(installation, source, revision):
    if installation == source:
        raise ValueError("Zbuduj panel w oddzielnej kopii roboczej. Główna instalacja może obsługiwać działającą sesję.")
    ui = _source_ui(source, revision)
    receipt = _receipt(source)
    with _file_lock(receipt.with_name("launcher-panel-build.lock")):
        receipt.unlink(missing_ok=True)
        env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
        for runtime in sorted((installation / ".codex/runtime").glob("node-v*-win-x64"), reverse=True):
            if (runtime / "node.exe").is_file() and (runtime / "npm.cmd").is_file():
                env["PATH"] = str(runtime) + os.pathsep + env.get("PATH", "")
                break
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm", path=env.get("PATH"))
        if not npm or not (ui / "node_modules/next/package.json").is_file():
            raise ValueError("Do kompilacji potrzebne są lokalne Node.js, npm i zależności panelu w kopii roboczej.")
        if subprocess.run([npm, "run", "build"], cwd=ui, env=env).returncode:
            raise RuntimeError("Kompilacja panelu nie powiodła się. Poprzednie wydanie pozostaje dostępne.")
        _source_ui(source, revision)
        build = _local_child(ui, ".next")
        digest = _panel_tree(build, build_source=ui)
        _write_json(receipt, {"revision": revision, "digest": digest})
        return build


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("revision")
    parser.add_argument("--installation", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(build_for_release(_repo(args.installation), _repo(args.source), args.revision))
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
