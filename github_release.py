"""Install releases published on GitHub into a downloaded (non-Git) installation.

A release package is a zip with one top folder. It carries ``release.json``
(version and commit) and ``release-manifest.json`` (SHA-256 and size of every
program file). Updating replaces only program files listed in a manifest and
never touches the player's own data: settings, routes, map data, logs.

Files are swapped by renaming: Windows allows renaming a running .exe or a
loaded DLL, so the launcher's own runtime can be replaced while it runs. The
renamed old copies are removed at the next start. A journal makes an
interrupted swap roll back to the previous version.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import urllib.error
import urllib.request
from uuid import uuid4
import zipfile

from dbv_revision import RELEASE_FILE, read_release, revision_label

MANIFEST_FILE = "release-manifest.json"
DEFAULT_REPO = "Xavizo/dbvictory-bot"
PACKAGE_ROOT = "DBVictory-Bot"
ASSET_PATTERN = r"DBVictory-Bot-v\d+\.\d+\.\d+\.zip"
# Only the files that changed since the release named after "patch-".
PATCH_PATTERN = r"DBVictory-Bot-v\d+\.\d+\.\d+-patch-v\d+\.\d+\.\d+\.zip"
USER_AGENT = "DBVictory-Launcher"
TIMEOUT = 10
MAX_PACKAGE = 2 * 1024 ** 3
MAX_MANIFEST = 32 * 1024 ** 2

# Never written, replaced or removed by an update: the player's own data.
PROTECTED_PREFIXES = (".codex/", ".git/", ".venv/", "local_data/", "dashboard/public/local-spells/")
PROTECTED_FILES = {"bot_settings.json", ".env", ".mcp.json"}
# Shipped as examples: added when missing, never overwritten or removed.
SEED_PREFIXES = ("recordings/",)


def _long(root):
    """Extended-length form on Windows: Electron's node_modules plus the stage folder can pass 260 characters."""
    root = Path(root).resolve()
    text = str(root)
    if os.name == "nt" and not text.startswith("\\\\?\\") and not text.startswith("\\\\"):
        return Path("\\\\?\\" + text)
    return root


def is_release_install(root):
    return read_release(root) is not None


def _feed(root):
    return Path(root) / ".codex" / "updates" / "github"


def _version(label):
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", label or "")
    return tuple(map(int, match.groups())) if match else None


def _key(path):
    return path.casefold()


def _protected(path):
    key = _key(path)
    return key in PROTECTED_FILES or key.startswith(PROTECTED_PREFIXES)


def _seed(path):
    return _key(path).startswith(SEED_PREFIXES)


def _clean_path(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value
            or value.startswith("/") or len(value) > 400):
        raise ValueError(f"Niedozwolona ścieżka w paczce: {value!r}")
    parts = PurePosixPath(value).parts
    if any(part in ("", ".", "..") for part in parts) or PurePosixPath(*parts).as_posix() != value:
        raise ValueError(f"Niedozwolona ścieżka w paczce: {value!r}")
    return value


def parse_manifest(data):
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError("Nieprawidłowa lista plików wydania.")
    files, seen = {}, set()
    for path, entry in data["files"].items():
        _clean_path(path)
        if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry[0]) or type(entry[1]) is not int or entry[1] < 0):
            raise ValueError(f"Nieprawidłowy wpis listy plików: {path}")
        if _key(path) in seen or path == MANIFEST_FILE:
            raise ValueError(f"Powtórzony plik w liście wydania: {path}")
        seen.add(_key(path))
        files[path] = (entry[0], entry[1])
    if RELEASE_FILE not in files:
        raise ValueError("W paczce brakuje opisu wersji.")
    return files


def _installed_manifest(root):
    path = Path(root) / MANIFEST_FILE
    try:
        if path.stat().st_size > MAX_MANIFEST:
            return {}
        return parse_manifest(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _repo(release):
    repo = release.get("repo") or DEFAULT_REPO
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+", repo):
        raise ValueError("Nieprawidłowe repozytorium aktualizacji.")
    return repo


def _tls():
    """Windows roots plus certifi's: Python, unlike Windows itself, does not fetch missing roots on demand."""
    import ssl
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return context


def _open(request, timeout):
    return urllib.request.urlopen(request, timeout=timeout, context=_tls())


def _get(url, *, limit, accept="application/octet-stream"):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with _open(request, TIMEOUT) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Odpowiedź serwera aktualizacji jest za duża.")
    return data


def _download_prefix(repo):
    return f"https://github.com/{repo}/releases/download/"


def fetch_latest(repo, get=_get):
    """Describe the latest published release: its release.json plus the package URL."""
    info = json.loads(get(f"https://api.github.com/repos/{repo}/releases/latest", limit=4 * 1024 ** 2,
                          accept="application/vnd.github+json"))
    assets = {a.get("name"): a.get("browser_download_url") for a in info.get("assets", [])
              if isinstance(a, dict)}
    meta_url = assets.get(RELEASE_FILE)
    if not isinstance(meta_url, str) or not meta_url.startswith(_download_prefix(repo)):
        raise ValueError("Najnowsze wydanie nie zawiera opisu wersji.")
    meta = json.loads(get(meta_url, limit=8192))
    if (not isinstance(meta, dict) or meta.get("format") != 1 or not _version(meta.get("version"))
            or not isinstance(meta.get("revision"), str) or not re.fullmatch(r"[0-9a-f]{40}", meta["revision"])
            or not isinstance(meta.get("asset"), str) or not re.fullmatch(ASSET_PATTERN, meta["asset"])
            or not isinstance(meta.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", meta["sha256"])
            or type(meta.get("size")) is not int or not 0 < meta["size"] <= MAX_PACKAGE):
        raise ValueError("Opis najnowszego wydania jest nieprawidłowy.")
    url = assets.get(meta["asset"])
    if not isinstance(url, str) or not url.startswith(_download_prefix(repo)):
        raise ValueError("Najnowsze wydanie nie zawiera paczki programu.")
    latest = {**meta, "url": url}
    latest.pop("patch", None)
    patch = _patch(meta.get("patch"), assets, repo)
    if patch:
        latest["patch"] = patch
    return latest


def _patch(patch, assets, repo):
    """The release's patch package, or None: a missing or odd patch only means a full download."""
    if (not isinstance(patch, dict) or not _version(patch.get("from"))
            or not isinstance(patch.get("asset"), str) or not re.fullmatch(PATCH_PATTERN, patch["asset"])
            or not isinstance(patch.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", patch["sha256"])
            or type(patch.get("size")) is not int or not 0 < patch["size"] <= MAX_PACKAGE):
        return None
    url = assets.get(patch["asset"])
    if not isinstance(url, str) or not url.startswith(_download_prefix(repo)):
        return None
    return {key: patch[key] for key in ("from", "asset", "sha256", "size")} | {"url": url}


def _usable_patch(root, latest):
    patch = latest.get("patch")
    release = read_release(root) or {}
    if patch and patch["from"] == release.get("version") and _installed_manifest(root):
        return patch
    return None


def _deferred(feed):
    try:
        data = json.loads((feed / "deferred.json").read_text(encoding="utf-8"))
        return data.get("revision") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _result(root, state, message, *, latest=None, updated=False, can_defer=False):
    release = read_release(root) or {}
    installed = release.get("revision")
    pending = latest["revision"] if latest and state == "available" else None
    return dict(state=state, source="github", installed=installed, pending=pending,
                installed_label=revision_label(installed, release.get("version")),
                pending_label=revision_label(pending, latest["version"]) if pending else None,
                message=message, running=state == "busy", updated=updated, can_defer=can_defer,
                restart=updated)


def _busy(root):
    from dbv_release import _ports_busy
    return _ports_busy()


def _lock(root):
    from dbv_release import _file_lock
    return _file_lock(Path(root) / ".codex" / "standalone.lock")


def status(root, check_only=False, *, fetch=fetch_latest):
    root = _long(root)
    release = read_release(root)
    if release is None:
        raise ValueError("To nie jest instalacja z paczki wydania.")
    feed = _feed(root)
    label = revision_label(release["revision"], release["version"])
    if (feed / "transaction.json").exists():
        if check_only:
            return _result(root, "recovery", "Poprzednia aktualizacja nie została dokończona.")
        with _lock(root):
            recover(root)
    if not check_only:
        cleanup(root)
    try:
        latest = fetch(_repo(release))
    except (OSError, ValueError, urllib.error.URLError) as error:
        reason = getattr(error, "reason", None) or error
        if isinstance(error, urllib.error.HTTPError) and error.code == 404:
            reason = "na GitHubie nie ma jeszcze opublikowanego wydania"
        return _result(root, "current", f"Zainstalowana wersja {label}. "
                       f"Nie udało się sprawdzić aktualizacji ({reason}). Możesz grać na tej wersji.")
    if _version(latest["version"]) <= _version(release["version"]):
        return _result(root, "current", f"Zainstalowana wersja {label}. To najnowsze wydanie.")
    new_label = revision_label(latest["revision"], latest["version"])
    if _deferred(feed) == latest["revision"]:
        return _result(root, "current", f"Zainstalowana wersja {label}. Wydanie {new_label} odłożone.")
    download = (_usable_patch(root, latest) or latest)["size"]
    size = f"{max(download / 1024 ** 2, 1):.0f} MB"
    return _result(root, "available", f"Dostępne wydanie {new_label} ({size}). "
                   "Zainstaluj je teraz albo odłóż i graj na obecnej wersji.", latest=latest, can_defer=True)


def defer(root, revision, *, fetch=fetch_latest):
    root = _long(root)
    report = status(root, check_only=True, fetch=fetch)
    if report["state"] != "available" or report["pending"] != revision:
        raise ValueError("Oczekujące wydanie zmieniło się. Sprawdź aktualizacje ponownie.")
    _write_json(_feed(root) / "deferred.json", {"revision": revision})
    result = _result(root, "current", f"Wydanie {report['pending_label']} odłożone. "
                     "Launcher zaproponuje je ponownie dopiero, gdy pojawi się nowsze.")
    result["deferred"] = revision
    return result


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(latest, target, opener=_open):
    if target.is_file() and target.stat().st_size == latest["size"] and _hash_file(target) == latest["sha256"]:
        return target
    part = target.with_name(target.name + ".part")
    request = urllib.request.Request(latest["url"], headers={"User-Agent": USER_AGENT,
                                                             "Accept": "application/octet-stream"})
    digest, size = hashlib.sha256(), 0
    try:
        with opener(request, timeout=60) as response, part.open("wb") as out:
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                size += len(chunk)
                if size > latest["size"]:
                    raise ValueError("Pobrana paczka jest większa niż zapowiedziano.")
                digest.update(chunk)
                out.write(chunk)
        if size != latest["size"] or digest.hexdigest() != latest["sha256"]:
            raise ValueError("Suma kontrolna pobranej paczki jest niezgodna. Spróbuj ponownie.")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


def _extract(package, stage, latest, *, partial=False):
    """Unpack only files listed in the package's manifest, verifying every hash.

    A partial (patch) package carries the full manifest but only some of its files.
    """
    with zipfile.ZipFile(package) as archive:
        members = {}
        for info in archive.infolist():
            name = info.filename
            if not name.startswith(PACKAGE_ROOT + "/") or info.is_dir():
                continue
            members[name[len(PACKAGE_ROOT) + 1:]] = info
        manifest_info = members.get(MANIFEST_FILE)
        if manifest_info is None or manifest_info.file_size > MAX_MANIFEST:
            raise ValueError("W paczce brakuje listy plików.")
        raw = archive.read(manifest_info)
        files = parse_manifest(json.loads(raw))
        for path, (sha, size) in files.items():
            info = members.get(path)
            if info is None and partial:
                continue
            if info is None or info.file_size != size:
                raise ValueError(f"Paczka jest niekompletna: {path}")
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(info) as src, target.open("wb") as out:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != sha:
                raise ValueError(f"Suma kontrolna pliku jest niezgodna: {path}")
        (stage / MANIFEST_FILE).write_bytes(raw)
    staged = json.loads((stage / RELEASE_FILE).read_text(encoding="utf-8"))
    if staged.get("version") != latest["version"] or staged.get("revision") != latest["revision"]:
        raise ValueError("Paczka zawiera inną wersję niż opis wydania.")
    return files


def plan(root, old, new):
    """Paths to write from the stage and paths to remove from the installation."""
    root = _long(root)
    write, remove = [], []
    for path, entry in new.items():
        if _protected(path):
            continue
        exists = (root / path).is_file()
        if _seed(path):
            if not exists:
                write.append(path)
        elif not exists or old.get(path) != entry:
            write.append(path)
    known = {_key(p) for p in new}
    for path in old:
        if _key(path) not in known and not _protected(path) and not _seed(path) and (root / path).is_file():
            remove.append(path)
    return write, remove


def _aside(path, tx):
    return path.with_name(f"{path.name}.dbvold-{tx}")


def _apply(root, stage, write, remove, tx):
    """Swap files, recording each step so that a crash can be rolled back."""
    feed = _feed(root)
    ops = [{"path": p, "write": True} for p in write] + [{"path": p, "write": False} for p in remove]
    ops.append({"path": MANIFEST_FILE, "write": True})
    for op in ops:
        op["existed"] = (root / op["path"]).exists()
    journal = {"tx": tx, "ops": ops}
    _write_json(feed / "transaction.json", journal)
    for op in ops:
        target = root / op["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            os.replace(target, _aside(target, tx))
        if op["write"]:
            os.replace(stage / op["path"], target)


def recover(root):
    """Roll back an interrupted swap. Safe to run more than once."""
    root = _long(root)
    feed = _feed(root)
    try:
        journal = json.loads((feed / "transaction.json").read_text(encoding="utf-8"))
        tx, ops = journal["tx"], journal["ops"]
        if not re.fullmatch(r"[0-9a-f]{32}", tx):
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("Dziennik aktualizacji jest uszkodzony. Pobierz paczkę ponownie.") from error
    leftovers = []
    for op in reversed(ops):
        target = root / _clean_path(op["path"])
        old = _aside(target, tx)
        # A written file is new when its original was moved aside, or when
        # there was no original. Otherwise the step never ran.
        if op["write"] and target.exists() and (old.exists() or not op["existed"]):
            # Running files cannot be deleted, but they can be renamed away.
            failed = target.with_name(f"{target.name}.dbvfail-{tx}")
            os.replace(target, failed)
            leftovers.append(failed.relative_to(root).as_posix())
        if old.exists():
            os.replace(old, target)
    _remember_leftovers(root, leftovers)
    (feed / "transaction.json").unlink()


def _remember_leftovers(root, paths):
    if not paths:
        return
    feed = _feed(root)
    path = feed / "cleanup.json"
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = []
    _write_json(path, sorted(set(known if isinstance(known, list) else []) | set(paths)))


# Folders that hold only program files. An update removes files, never folders,
# so a release that ships fewer files leaves empty folders here.
PROGRAM_TREES = ("dashboard/node_modules", "runtime/node", "venv/Scripts")


def _prune_empty_folders(root):
    """Once per installed version, since the walk touches thousands of folders."""
    version = (read_release(root) or {}).get("version")
    marker = _feed(root) / "pruned.json"
    try:
        if json.loads(marker.read_text(encoding="utf-8")).get("version") == version:
            return
    except (OSError, ValueError, AttributeError):
        pass
    for top in PROGRAM_TREES:
        base = root / top
        if not base.is_dir():
            continue
        for folder, _dirs, _names in os.walk(base, topdown=False):
            if Path(folder) != base:
                try:
                    os.rmdir(folder)  # only succeeds when empty
                except OSError:
                    pass
    try:
        _write_json(marker, {"version": version})
    except OSError:
        pass


def cleanup(root):
    """Delete old copies left by a previous update. Files still in use stay listed."""
    root = _long(root)
    path = _feed(root) / "cleanup.json"
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _prune_empty_folders(root)
        return
    remaining = []
    for item in items if isinstance(items, list) else []:
        try:
            name = _clean_path(item)
        except ValueError:
            continue
        if not re.search(r"\.dbv(?:old|fail)-[0-9a-f]{32}$", name):
            continue
        try:
            (root / name).unlink(missing_ok=True)
        except OSError:
            remaining.append(name)
    if remaining:
        _write_json(path, remaining)
    else:
        path.unlink(missing_ok=True)
    _prune_empty_folders(root)


def _unpack(root, feed, latest, source, opener, *, partial):
    package = _download(source, feed / source["asset"], opener)
    tx = uuid4().hex
    stage = feed / f"stage-{tx}"
    try:
        new = _extract(package, stage, latest, partial=partial)
        write, remove = plan(root, _installed_manifest(root), new)
        if partial:
            # Shipped examples the player deleted stay deleted rather than forcing a full download.
            write = [p for p in write if (stage / p).is_file() or not _seed(p)]
            if not all((stage / p).is_file() for p in write):
                raise ValueError("Łatka nie obejmuje tej instalacji.")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return tx, stage, package, write, remove


def _prepare(root, feed, latest, opener):
    """Download and unpack the update: the patch when it covers this installation, else the full package."""
    patch = _usable_patch(root, latest)
    if patch:
        try:
            return _unpack(root, feed, latest, patch, opener, partial=True)
        except (OSError, ValueError, zipfile.BadZipFile):
            (feed / patch["asset"]).unlink(missing_ok=True)
    return _unpack(root, feed, latest, latest, opener, partial=False)


def install(root, revision, *, fetch=fetch_latest, opener=_open):
    root = _long(root)
    release = read_release(root)
    if release is None:
        raise ValueError("To nie jest instalacja z paczki wydania.")
    feed = _feed(root)
    with ExitStack() as stack:
        try:
            stack.enter_context(_lock(root))
        except RuntimeError:
            return _result(root, "busy", "Bot działa lub trwa inna aktualizacja. Zamknij bota i spróbuj ponownie.")
        if _busy(root):
            return _result(root, "busy", "Bot działa lub porty są zajęte. Aktualizacja poczeka.")
        if (feed / "transaction.json").exists():
            recover(root)
        latest = fetch(_repo(release))
        if latest["revision"] != revision:
            raise ValueError("Na GitHubie pojawiło się inne wydanie. Sprawdź aktualizacje ponownie.")
        if _version(latest["version"]) <= _version(release["version"]):
            return _result(root, "current", "Ta wersja jest już zainstalowana.")
        feed.mkdir(parents=True, exist_ok=True)
        tx, stage, package, write, remove = _prepare(root, feed, latest, opener)
        try:
            try:
                _apply(root, stage, write, remove, tx)
            except BaseException:
                if (feed / "transaction.json").exists():
                    recover(root)
                raise
            (feed / "transaction.json").unlink()
            leftovers = [_aside(root / p, tx).relative_to(root).as_posix() for p in [*write, *remove, MANIFEST_FILE]
                         if _aside(root / p, tx).exists()]
            _remember_leftovers(root, leftovers)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        package.unlink(missing_ok=True)
        (feed / "deferred.json").unlink(missing_ok=True)
        cleanup(root)
        label = revision_label(latest["revision"], latest["version"])
        return _result(root, "updated", f"Zainstalowano wydanie {label}. Launcher uruchomi się ponownie.",
                       updated=True)
