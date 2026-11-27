"""Fetch the file list of the latest published release, the base of this build's patch.

Usage: python -I tools/release_previous.py --repo OWNER/NAME --out FILE

Writes the latest release's release-manifest.json to FILE. Releases published
before patches existed only carry it inside their zip, so that is read as a
fallback. Exits 0 without writing FILE when there is no published release or it
cannot be read: the build then publishes the full package only.
GH_TOKEN or GITHUB_TOKEN, when set, avoids the anonymous API rate limit.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.error
import urllib.request
import zipfile

PACKAGE_ROOT = "DBVictory-Bot"
MANIFEST = "release-manifest.json"


def _request(url, accept):
    headers = {"User-Agent": "DBVictory-Release-Build", "Accept": accept}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def latest_manifest(repo):
    with _request(f"https://api.github.com/repos/{repo}/releases/latest", "application/vnd.github+json") as r:
        info = json.load(r)
    assets = {a["name"]: a["browser_download_url"] for a in info.get("assets", [])}
    if MANIFEST in assets:
        with _request(assets[MANIFEST], "application/octet-stream") as r:
            return r.read()
    with _request(assets["release.json"], "application/octet-stream") as r:
        asset = json.load(r)["asset"]
    with tempfile.TemporaryFile() as package:
        with _request(assets[asset], "application/octet-stream") as r:
            shutil.copyfileobj(r, package, 1024 * 1024)
        with zipfile.ZipFile(package) as archive:
            return archive.read(f"{PACKAGE_ROOT}/{MANIFEST}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        data = latest_manifest(args.repo)
        version = json.loads(data)["version"]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"No previous release to patch from ({error}); building the full package only")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(data)
    print(f"Patch base: {version}")


if __name__ == "__main__":
    main()
