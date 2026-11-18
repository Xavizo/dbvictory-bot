"""Publish tested local commits and install them when the normal launcher is idle."""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
from uuid import uuid4

from dbv_revision import VERSION_PATTERN, git_executable, git_output as _git, revision_label, version_for


def _path(path):
    path = Path(path).resolve(strict=True)
    if str(path).startswith(("\\\\", "//")) or not path.is_dir():
        raise ValueError("Wybierz lokalny katalog na tym komputerze.")
    return path


def _repo(path):
    path = _path(path)
    if Path(_git(path, "rev-parse", "--show-toplevel")).resolve() != path:
        raise ValueError("Wskaż katalog główny lokalnego repozytorium.")
    return path


def _sha(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{40}", value):
        raise ValueError("Wydanie wymaga pełnego SHA commita.")
    return value


def _head(repo):
    return _sha(_git(repo, "rev-parse", "--verify", "HEAD"))


def _changes(root, base, revision):
    try:
        _git(root, "merge-base", "--is-ancestor", base, revision)
    except RuntimeError as exc:
        raise ValueError("Wydanie nie jest kontynuacją zainstalowanej wersji.") from exc
    return set(_git(root, "diff", "--name-only", "--no-renames", "-z", base, revision).split("\0")) - {""}


def _needs_panel(changes):
    return any(p.startswith("dashboard/") and not p.startswith(("dashboard/electron/", "dashboard/public/"))
               and not p.endswith((".md", ".test.js", ".test.ts")) for p in changes)


def _check_supported(changes):
    for path in sorted(changes):
        key = path.casefold()
        name = Path(key).name
        if (key in {".env", "bot_settings.json"} or key.startswith((
                ".codex/", "local_data/", ".venv/", "venv/", "dashboard/public/local-spells/",
                "dashboard/.next", "dashboard/node_modules/", "recordings/atlas_", "recordings/exp_"))):
            raise ValueError(f"Wydanie próbuje zmienić lokalne dane instalacji ({path}).")
        if (key.startswith("dll/") and not name.startswith("test") and not name.endswith(".md")
                or key in {"build_dll.ps1", "requirements.txt", "pyproject.toml", "poetry.lock", "uv.lock",
                           "dashboard/package-lock.json", "dashboard/package.json",
                           "dashboard/pnpm-lock.yaml", "dashboard/yarn.lock"}
                or not key.startswith(("tests/", "docs/")) and name.endswith((".dll", ".exe", ".pyd", ".so", ".dylib"))):
            raise ValueError(f"To wydanie wymaga osobnej aktualizacji zależności lub DLL ({path}).")


def _lock_path(root):
    return _local_child(root / ".codex", "standalone.lock")


@contextmanager
def _file_lock(path):
    if os.name == "nt":
        from launcher_lock import launcher_lock
        with launcher_lock(path):
            yield
    else:
        import fcntl
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("Inna operacja zajmuje blokadę.") from exc
            yield


def _ports_busy():
    ports = {4747, 8089, 8090, int(os.environ.get("DBV_LOGIN_PORT", 7171)),
             int(os.environ.get("DBV_GAME_PORT", 7475))}
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return True
    return False


def _updates(root):
    return _local_child(_local_child(root, ".codex"), "updates")


def _write_json(path, data):
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _manifest(path):
    _local_child(path.parent, path.name)
    try:
        if path.stat().st_size > 8192:
            raise ValueError("Manifest lokalnego wydania jest za duży.")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (not isinstance(data, dict) or data.keys() != {"format", "package", "base", "revision", "bundle", "panel"}
            or type(data["format"]) is not int or data["format"] != 1
            or not isinstance(data["package"], str) or not re.fullmatch("[a-f0-9]{32}", data["package"])
            or not isinstance(data["bundle"], str) or not re.fullmatch("[a-f0-9]{64}", data["bundle"])
            or data["panel"] is not None and (not isinstance(data["panel"], str)
                                              or not re.fullmatch("[a-f0-9]{64}", data["panel"]))):
        raise ValueError("Nieprawidłowy manifest lokalnego wydania.")
    _sha(data.get("base"))
    _sha(data.get("revision"))
    return data


def _file_hash(path):
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _linked(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                              & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _local_child(parent, name):
    directory = parent / name
    try:
        if _linked(directory) or directory.resolve().parent != parent.resolve():
            raise ValueError(f"Plik lub katalog instalacji prowadzi poza oczekiwane miejsce: {directory}")
    except FileNotFoundError:
        pass
    return directory


def _remove_folder(directory):
    path = _local_child(directory.parent, directory.name)
    if path.is_dir():
        shutil.rmtree(path)


def _raise_os_error(error):
    raise error


def _panel_tree(directory, destination=None, *, build_source=None):
    if _linked(directory) or not directory.is_dir():
        raise ValueError("Brak zwykłego katalogu ze zbudowanym panelem.")
    digest = hashlib.sha256()
    entries = set()
    for root, dirs, files in os.walk(directory, onerror=_raise_os_error):
        parent = Path(root)
        if parent == directory:
            dirs[:] = [d for d in dirs if d not in {"cache", "diagnostics", "types"}]
            files = [f for f in files if f not in {"trace", "trace-build"}]
        dirs.sort()
        for name in dirs:
            if _linked(parent / name):
                raise ValueError("Katalog panelu zawiera łącze.")
        for name in sorted(files):
            src = parent / name
            rel = src.relative_to(directory)
            if _linked(src) or not src.is_file():
                raise ValueError(f"Niedozwolony plik panelu: {rel}")
            if destination is not None:
                target = destination / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                src = target
            key = rel.as_posix()
            digest.update(key.encode("utf-8") + b"\0" + _file_hash(src).encode("ascii") + b"\n")
            entries.add(key)
    required = {"BUILD_ID", "routes-manifest.json", "build-manifest.json", "required-server-files.json"}
    if (not required <= entries or not any(key.startswith("server/") for key in entries)
            or not any(key.startswith("static/") for key in entries)
            or not (directory / "BUILD_ID").read_text(encoding="utf-8").strip()):
        raise ValueError("Niekompletny build panelu. Zbuduj panel dla tego wydania.")
    snapshot = destination or directory
    for name in required - {"BUILD_ID"}:
        metadata = json.loads((snapshot / name).read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError(f"Błędny manifest panelu {name}.")
        if name == "required-server-files.json":
            if build_source and Path(str(metadata.get("appDir", ""))).resolve() != build_source:
                raise ValueError("Build Next pochodzi z innego katalogu źródłowego.")
            required_files = metadata.get("files")
            built_files = {".next/" + path for path in entries}
            if not isinstance(required_files, list) or not required_files or any(
                    not isinstance(f, str) or f.replace("\\", "/") not in built_files for f in required_files):
                raise ValueError("Paczka nie zawiera wszystkich plików wymaganych przez Next.")
    return digest.hexdigest()


def _pending_version(root, manifest):
    if not manifest:
        return None
    # The candidate need not be in the installation's Git object database yet.
    # Keep the optional display metadata outside the existing package manifest
    # so already installed launchers can consume the same release bundles.
    try:
        package = _local_child(_updates(root), manifest["package"])
        path = _local_child(package, "version.txt")
        with path.open(encoding="ascii") as stream:
            line = stream.read(160)
        sha, _, version = line.strip().partition(" ")
        if sha == manifest["revision"] and re.fullmatch(VERSION_PATTERN, version):
            return version
    except (OSError, ValueError):
        pass
    return version_for(root, manifest["revision"])


def _result(root, head, pending, *, busy=False, recovering=False, updated=False):
    revision = pending["revision"] if pending else None
    installed_label = revision_label(head, version_for(root, head))
    pending_label = revision_label(revision, _pending_version(root, pending)) if revision else None
    if busy:
        state, message = "busy", "Bot działa lub porty są zajęte. Aktualizacja poczeka."
    elif recovering:
        state, message = "recovery", "Do zakończenia pozostała poprzednia aktualizacja."
    elif revision:
        state, message = "available", f"Gotowa aktualizacja {pending_label}."
    else:
        state, message = "current", f"Zainstalowana wersja {installed_label}."
    return dict(state=state, installed=head, pending=revision, installed_label=installed_label,
                pending_label=pending_label, message=message, running=busy, updated=updated)


def status(installation):
    root = _repo(installation)
    feed = _updates(root)
    try:
        with _file_lock(_lock_path(root)):
            busy = _ports_busy()
    except RuntimeError:
        busy = True
    return _result(root, _head(root), _manifest(feed / "pending.json"), busy=busy,
                   recovering=(feed / "transaction.json").is_file())


def _dirty_program(root):
    return _git(root, "diff", "--name-only", "HEAD", "--", ".", ":(exclude)recordings/**")


def can_defer(installation, expected_revision):
    """Cheap advisory check. defer_pending rechecks the actual state under lock."""
    root = _repo(installation)
    state = status(root)
    pending = _manifest(_updates(root) / "pending.json")
    return bool(state["state"] == "available" and pending and pending["revision"] == expected_revision
                and pending["base"] == state["installed"] and not _dirty_program(root))


def defer_pending(installation, expected_revision):
    """Atomically clear the queue and retain the exact declined release and its contents."""
    root, expected = _repo(installation), _sha(expected_revision)
    feed = _updates(root)
    with _file_lock(_lock_path(root)):
        if _ports_busy():
            raise RuntimeError("Sesja lub porty są zajęte. Nie można odłożyć aktualizacji.")
        with _file_lock(_local_child(feed, "publish.lock")):
            head = _head(root)
            if (feed / "transaction.json").exists():
                raise ValueError("Najpierw dokończ lub odzyskaj rozpoczętą aktualizację.")
            pending = _manifest(feed / "pending.json")
            if not pending or pending["revision"] != expected or pending["base"] != head:
                raise ValueError("Oczekujące wydanie albo wersja instalacji uległy zmianie.")
            if _dirty_program(root):
                raise ValueError("Główna instalacja ma lokalne zmiany programu.")
            if pending["panel"]:
                _panel_tree(_local_child(_local_child(root, "dashboard"), ".next"))
            deferred_path = _local_child(feed, "deferred.json")
            previous = _manifest(deferred_path)
            if previous:
                _write_json(_local_child(feed, f"deferred-{previous['package']}.json"), previous)
            os.replace(feed / "pending.json", deferred_path)
            result = _result(root, head, None)
            result["deferred"] = expected
            return result


def publish(installation, source, revision, dashboard=None, *, verify_build=False):
    """Queue the exact tested checkout. Publishing never changes running application files."""
    root, source, revision = _repo(installation), _repo(source), _sha(revision)
    feed = _updates(root)
    feed.mkdir(parents=True, exist_ok=True)
    with _file_lock(_local_child(feed, "publish.lock")):
        if (feed / "transaction.json").exists():
            raise ValueError("Najpierw dokończ przerwaną aktualizację.")
        base = _head(root)
        if _head(source) != revision:
            raise ValueError("Repozytorium wydania musi wskazywać dokładnie przetestowany commit.")
        changes = _changes(source, base, revision)
        _check_supported(changes)
        old = _manifest(feed / "pending.json")
        if revision == base:
            if old:
                raise ValueError("To wersja zainstalowana. W kolejce jest już inne wydanie.")
            return _result(root, base, None)
        if _needs_panel(changes) and dashboard is None:
            raise ValueError("Do tego wydania dołącz zbudowany panel.")
        package_id = uuid4().hex
        package = feed / package_id
        package.mkdir()
        try:
            panel = None
            if dashboard is not None:
                source_ui = _local_child(source, "dashboard")
                if _path(dashboard) != _local_child(source_ui, ".next") or _git(
                    source, "status", "--porcelain", "--untracked-files=all", "--", "dashboard",
                    ":(exclude)dashboard/.next/**", ":(exclude)dashboard/node_modules/**",
                    ":(exclude)dashboard/electron/**", ":(exclude)dashboard/public/local-spells/**",
                    ":(exclude)dashboard/tsconfig.tsbuildinfo",
                ):
                    raise ValueError("Spakuj dashboard/.next z czystej kopii źródłowej tego commita.")
                panel = _panel_tree(_path(dashboard), package / "panel", build_source=source_ui)
                if verify_build:
                    from launcher_panel_build import verify_prebuilt
                    verify_prebuilt(source, revision, dashboard, copied_digest=panel)
            bundle = package / "code.bundle"
            _git(source, "bundle", "create", str(bundle), "HEAD", "^" + base)
            if _git(source, "bundle", "list-heads", str(bundle), "HEAD") != f"{revision} HEAD":
                raise ValueError("Źródło zmieniło się podczas przygotowania aktualizacji.")
            manifest = dict(format=1, package=package_id, base=base, revision=revision,
                            bundle=_file_hash(bundle), panel=panel)
            version = version_for(source, revision)
            if version:
                (package / "version.txt").write_text(f"{revision} {version}\n", encoding="ascii")
            _write_json(feed / "pending.json", manifest)
        except Exception:
            _remove_folder(package)
            raise
        if old:
            _remove_folder(feed / old["package"])
        return _result(root, base, manifest)


def _cleanup(feed, manifest):
    (feed / "pending.json").unlink(missing_ok=True)
    (feed / "transaction.json").unlink(missing_ok=True)
    _remove_folder(feed / manifest["package"])


def _panel_paths(root, manifest):
    dashboard = _local_child(root, "dashboard")
    suffix = manifest["package"]
    return tuple(_local_child(dashboard, name)
                 for name in (".next", f".next-stage-{suffix}", f".next-backup-{suffix}"))


def _recover(root, feed, tx):
    head = _head(root)
    current, stage, backup = _panel_paths(root, tx)
    if head not in (tx["base"], tx["revision"]):
        raise ValueError("HEAD zmienił się podczas aktualizacji. Zachowano poprzedni panel i pakiet.")
    if tx.get("panel"):
        if head == tx["base"] and backup.is_dir():
            if current.exists():
                if stage.exists():
                    raise ValueError("Konflikt katalogów przy odzyskiwaniu panelu.")
                current.rename(stage)
            backup.rename(current)
        elif head == tx["base"] and not current.is_dir():
            raise ValueError("Nie znaleziono poprzedniego panelu. Zachowano pliki do odzyskania.")
        elif head == tx["revision"]:
            if _panel_tree(current) != tx["panel"]:
                raise ValueError("Nowy panel wymaga sprawdzenia. Zachowano kopię poprzedniego panelu.")
            if backup.is_dir():
                _remove_folder(backup)
        if stage.is_dir():
            _remove_folder(stage)
    if head == tx["revision"]:
        _cleanup(feed, tx)
        return True
    (feed / "transaction.json").unlink()
    return False


def _apply(root, feed, release):
    head = _head(root)
    revision = release["revision"]
    if head != release["base"]:
        raise ValueError("Wersja bazowa instalacji zmieniła się. Opublikuj wydanie ponownie.")
    package = feed / release["package"]
    bundle = package / "code.bundle"
    if _linked(package) or _linked(bundle) or _file_hash(bundle) != release["bundle"]:
        raise ValueError("Suma kontrolna paczki programu jest niezgodna.")
    _git(root, "bundle", "verify", str(bundle))
    if _git(root, "bundle", "list-heads", str(bundle), "HEAD") != f"{revision} HEAD":
        raise ValueError("Paczka zawiera inną wersję programu.")
    _git(root, "fetch", "--no-tags", "--no-recurse-submodules", str(bundle), "HEAD")
    changes = _changes(root, head, revision)
    _check_supported(changes)
    if _needs_panel(changes) and not release["panel"]:
        raise ValueError("W paczce brakuje wymaganego panelu.")
    if _dirty_program(root):
        raise ValueError("Główna instalacja ma lokalne zmiany programu. Zachowano istniejące pliki.")
    current, stage, backup = _panel_paths(root, release)
    if release["panel"]:
        if not current.is_dir() or _linked(current) or stage.exists() or backup.exists():
            raise ValueError("Nie można bezpiecznie wymienić obecnego panelu.")
        if (current.parent / f".next-ready-{revision[:7]}").exists():
            raise ValueError("Inny panel dla tej samej wersji czeka na aktywację.")
    _write_json(feed / "transaction.json", release)
    try:
        if release["panel"]:
            if _panel_tree(package / "panel", stage) != release["panel"]:
                raise ValueError("Suma kontrolna panelu jest niezgodna.")
            current.rename(backup)
            stage.rename(current)
        _git(root, "merge", "--ff-only", "--no-overwrite-ignore", revision)
    except Exception:
        _recover(root, feed, release)
        raise
    if _head(root) != revision:
        raise RuntimeError("Nie udało się potwierdzić wersji na dysku.")
    _recover(root, feed, release)


def apply_pending(installation):
    root = _repo(installation)
    feed = _updates(root)
    with ExitStack() as locks:
        try:
            locks.enter_context(_file_lock(_lock_path(root)))
        except RuntimeError:
            return _result(root, _head(root), _manifest(feed / "pending.json"), busy=True)
        if _ports_busy():
            return _result(root, _head(root), _manifest(feed / "pending.json"), busy=True)
        locks.enter_context(_file_lock(_local_child(feed, "publish.lock")))
        tx = _manifest(feed / "transaction.json")
        pending = _manifest(feed / "pending.json")
        if tx and pending and tx != pending:
            raise ValueError("Manifest oczekującego wydania różni się od dziennika aktualizacji.")
        updated = _recover(root, feed, tx) if tx else False
        release = _manifest(feed / "pending.json")
        if release:
            _apply(root, feed, release)
            updated = True
        return _result(root, _head(root), None, updated=updated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    publish_cmd = commands.add_parser("publish")
    publish_cmd.add_argument("source", type=Path)
    publish_cmd.add_argument("revision")
    publish_cmd.add_argument("--dashboard", type=Path)
    commands.add_parser("check")
    commands.add_parser("apply")
    args = parser.parse_args()
    try:
        if args.command == "publish":
            root, source, revision = _repo(args.installation), _repo(args.source), _sha(args.revision)
            if _head(source) != revision:
                raise ValueError("Repozytorium wydania musi wskazywać dokładnie przetestowany commit.")
            changes = _changes(source, _head(root), revision)
            _check_supported(changes)
            dashboard = None
            if args.dashboard is not None:
                from launcher_panel_build import verify_prebuilt
                dashboard = verify_prebuilt(source, revision, args.dashboard)
            elif _needs_panel(changes):
                from launcher_panel_build import build_for_release
                dashboard = build_for_release(root, source, revision)
            result = publish(root, source, revision, dashboard, verify_build=True)
        elif args.command == "apply":
            result = apply_pending(args.installation)
        else:
            result = status(args.installation)
        print(result["message"])
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
