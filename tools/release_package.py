"""Seal a staged release folder: version file, checksum manifest, zip and its description.

Usage: python -I tools/release_package.py STAGE --version vX.Y.Z --revision SHA --repo OWNER/NAME --out DIR
                                          [--previous MANIFEST]

STAGE must be a folder named DBVictory-Bot holding the complete, ready-to-run
installation. Writes into DIR:
  DBVictory-Bot-vX.Y.Z.zip   the package players download
  release.json               version, commit and SHA-256 of the zip (read by the updater)
  release-manifest.json      the package's file list, so the next build can make its patch
  DBVictory-Bot-vX.Y.Z-patch-vA.B.C.zip
                             with --previous (vA.B.C's release-manifest.json): only the files
                             that changed since vA.B.C, plus the full file list. Installations
                             on vA.B.C download this instead of the whole package.
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
              "i uruchom `DBVictory.exe`. Instrukcja: README → Instalacja dla graczy."]
    return "\n".join(lines) + "\n"


def write_zip(target, stage, names):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel in names:
            archive.write(stage / rel, f"{PACKAGE_ROOT}/{rel}")


def make_patch(stage, files, previous_manifest, version, out):
    """Zip of the files that differ from the previous release, or None when there is no usable base."""
    try:
        previous = json.loads(Path(previous_manifest).read_text(encoding="utf-8"))
        base, old = previous["version"], previous["files"]
        if not re.fullmatch(r"v\d+\.\d+\.\d+", base) or base == version or not isinstance(old, dict):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        print(f"No usable previous manifest in {previous_manifest}; publishing the full package only")
        return None
    changed = [rel for rel, entry in files.items() if old.get(rel) != entry]
    asset = f"{PACKAGE_ROOT}-{version}-patch-{base}.zip"
    target = Path(out) / asset
    write_zip(target, stage, [*changed, MANIFEST])
    return {"from": base, "asset": asset, "sha256": file_hash(target), "size": target.stat().st_size,
            "files": len(changed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--previous", type=Path, help="release-manifest.json of the release this one follows")
    args = parser.parse_args()
    stage = args.stage.resolve()
    if stage.name != PACKAGE_ROOT or not stage.is_dir():
        raise SystemExit(f"Stage folder must be named {PACKAGE_ROOT}")
    if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-test\.[0-9a-f]{7})?", args.version) or not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise SystemExit("Bad version or revision")
    if os.name == "nt":
        stage = Path("\\\\?\\" + str(stage))  # long paths can pass 260 characters
    for required in ("DBVictory.exe", "DBVictory Launcher.exe", "venv/Scripts/DBVictory.exe",
                     "venv/Scripts/DBVictory Engine.exe", "venv/Scripts/python.exe", "venv/Scripts/pythonw.exe",
                     "launcher/desktop_app.py", "dbv/mcp_server/panel_server.py", "dbv/mcp_server/panel_window.py",
                     "dashboard/.next/BUILD_ID", "venv/Scripts/Lib/site-packages/webview/__init__.py"):
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
    write_zip(target, stage, [*files, MANIFEST])
    meta = {"format": 1, "version": args.version, "revision": args.revision, "asset": asset,
            "sha256": file_hash(target), "size": target.stat().st_size}
    if args.previous:
        patch = make_patch(stage, files, args.previous, args.version, args.out)
        if patch:
            meta["patch"] = patch
            print(f"{patch['asset']}: {patch['files']} changed files, {patch['size'] / 1024 ** 2:.1f} MB")
            del patch["files"]
    (args.out / "release.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    (args.out / MANIFEST).write_bytes((stage / MANIFEST).read_bytes())
    (args.out / "release-notes.md").write_text(notes(stage, args.version), encoding="utf-8")
    print(f"{asset}: {len(files)} files, {target.stat().st_size / 1024 ** 2:.0f} MB")


if __name__ == "__main__":
    main()
