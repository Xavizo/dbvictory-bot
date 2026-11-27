"""Seal a staged release folder: version file, checksum manifest, zip and its description.

Usage: python -I tools/release_package.py STAGE --version vX.Y.Z --revision SHA --repo OWNER/NAME --out DIR

STAGE must be a folder named DBVictory-Bot holding the complete, ready-to-run
installation. Writes into DIR:
  DBVictory-Bot-vX.Y.Z.zip   the package players download
  release.json               version, commit and SHA-256 of the zip (read by the updater)
  release-notes.md           this version's entry from launcher/desktop/release-notes.json
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

PACKAGE_ROOT = "DBVictory-Bot"
MANIFEST = "release-manifest.json"


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(stage):
    files = {}
    for folder, dirs, names in os.walk(stage):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            rel = path.relative_to(stage).as_posix()
            if rel == MANIFEST:
                continue
            if path.is_symlink() or not path.is_file():
                raise SystemExit(f"Not a regular file: {rel}")
            files[rel] = [file_hash(path), path.stat().st_size]
    return files


def notes(stage, version):
    try:
        releases = json.loads((stage / "launcher" / "desktop" / "release-notes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        releases = []
    entry = next((r for r in releases if isinstance(r, dict) and r.get("version") == version), None)
    lines = [f"## DBVictory Bot {version}", ""]
    if entry:
        lines += [f"- **{c['title']}** {c['detail']}" for c in entry.get("changes", [])]
    lines += ["", "Pobierz `DBVictory-Bot-" + version + ".zip`, rozpakuj do krótkiej ścieżki (np. `C:\\DBVictory-Bot`) "
              "i uruchom `DBVictory Launcher.exe`. Instrukcja: README → Instalacja dla graczy."]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if stage.name != PACKAGE_ROOT or not stage.is_dir():
        raise SystemExit(f"Stage folder must be named {PACKAGE_ROOT}")
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.version) or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("Bad version or revision")
    if os.name == "nt":
        stage = Path("\\\\?\\" + str(stage))  # node_modules paths can pass 260 characters
    for required in ("DBVictory Launcher.exe", "venv/Scripts/python.exe", "runtime/node/node.exe",
                     "dashboard/.next/BUILD_ID", "dashboard/node_modules/electron/dist/electron.exe"):
        if not (stage / required).is_file():
            raise SystemExit(f"Stage is missing {required}")
    (stage / "release.json").write_text(json.dumps({
        "format": 1, "version": args.version, "revision": args.revision, "repo": args.repo,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, indent=2) + "\n", encoding="utf-8")
    files = collect(stage)
    (stage / MANIFEST).write_text(json.dumps({"format": 1, "version": args.version, "files": files},
                                             separators=(",", ":")), encoding="utf-8")
    args.out.mkdir(parents=True, exist_ok=True)
    asset = f"{PACKAGE_ROOT}-{args.version}.zip"
    target = args.out / asset
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in [*files, MANIFEST]:
            archive.write(stage / rel, f"{PACKAGE_ROOT}/{rel}")
    (args.out / "release.json").write_text(json.dumps({
        "format": 1, "version": args.version, "revision": args.revision, "asset": asset,
        "sha256": file_hash(target), "size": target.stat().st_size}, indent=2) + "\n", encoding="utf-8")
    (args.out / "release-notes.md").write_text(notes(stage, args.version), encoding="utf-8")
    print(f"{asset}: {len(files)} files, {target.stat().st_size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()
