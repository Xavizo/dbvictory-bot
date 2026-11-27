"""Desktop update window and file-only preflight. No game or engine startup."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent


def window_executable(root):
    source = root / "launcher" / "UpdateWindow.cs"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cache = root / ".codex" / "launcher-ui" / digest
    target = cache / "UpdateWindow.exe"
    if target.is_file():
        return target
    framework = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Microsoft.NET"
    compilers = (framework / arch / "v4.0.30319" / "csc.exe" for arch in ("Framework64", "Framework"))
    compiler = next((path for path in compilers if path.is_file()), None)
    if compiler is None:
        raise RuntimeError("Brakuje kompilatora .NET Framework potrzebnego do przygotowania okna aktualizacji.")
    cache.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=cache) as temp:
        built = Path(temp) / target.name
        result = subprocess.run(
            [str(compiler), "/nologo", "/target:winexe", "/optimize+", "/langversion:5",
             "/codepage:65001", "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
             "/reference:System.Web.Extensions.dll", f"/out:{built}", str(source)],
            cwd=root, capture_output=True, text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError("Nie udało się zbudować okna aktualizacji. " + (result.stdout or result.stderr).strip())
        try:
            built.rename(target)
        except FileExistsError:
            pass  # Another launcher finished the same content-addressed build.
    return target


def preflight(root, check_only=False):
    import github_release
    if github_release.is_release_install(root):
        # Downloaded packages update from GitHub releases, and only on request.
        return github_release.status(root, check_only)

    from dbv_release import apply_pending, status

    report = status(root) if check_only else apply_pending(root)
    if report.get("updated"):
        report = {**report, "state": "updated"}
    return report


def show_window(root, check_only=False):
    return subprocess.run([str(window_executable(root)), *(["--check"] if check_only else [])],
                          cwd=root).returncode


@contextmanager
def desktop_files(root):
    """Keep the finalized checkout stable while its consumer reads and uses it."""
    from launcher_lock import launcher_lock
    feed = root / ".codex" / "updates"
    while True:
        result = show_window(root)
        if result == 2:
            yield False
            return
        if result:
            raise RuntimeError("Okno aktualizacji zakończyło się błędem.")
        with launcher_lock(root / ".codex" / "standalone.lock"):
            if (feed / "pending.json").exists() or (feed / "transaction.json").exists():
                continue  # A new release arrived while the window was closing.
            yield True
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "window", "build-window", "defer", "install",
                                              "client-check", "channel"))
    parser.add_argument("revision", nargs="?", help="Wydanie albo, dla channel, stable lub test.")
    parser.add_argument("--check", action="store_true", help="Pokaż wersje bez instalowania.")
    args = parser.parse_args()
    try:
        if args.operation == "build-window":
            print(window_executable(ROOT))
        elif args.operation == "window":
            return show_window(ROOT, args.check)
        elif args.operation == "defer":
            import github_release
            if github_release.is_release_install(ROOT):
                print(json.dumps(github_release.defer(ROOT, args.revision), ensure_ascii=False))
            else:
                from dbv_release import defer_pending
                print(json.dumps(defer_pending(ROOT, args.revision), ensure_ascii=False))
        elif args.operation == "channel":
            import github_release
            print(json.dumps(github_release.set_channel(ROOT, args.revision), ensure_ascii=False))
        elif args.operation == "client-check":
            from dbv.client_bridge.client_profiles import client_support
            print(json.dumps(client_support(Path(args.revision)), ensure_ascii=False))
        elif args.operation == "install":
            import github_release
            print(json.dumps(github_release.install(ROOT, args.revision), ensure_ascii=False))
        else:
            print(json.dumps(preflight(ROOT, args.check), ensure_ascii=False))
    except (OSError, RuntimeError, ValueError) as error:
        if args.operation == "channel":
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
            return 1
        if args.operation in ("preflight", "defer", "install"):
            info = {}
            may_defer = False
            import github_release
            if github_release.is_release_install(ROOT):
                print(json.dumps({"state": "error", "source": "github", "installed": None, "pending": None,
                                  "message": str(error)}, ensure_ascii=False))
                return 1
            if args.operation == "preflight" and not args.check:
                try:
                    from dbv_release import can_defer
                    info = preflight(ROOT, check_only=True)
                    if info.get("state") == "available":
                        may_defer = can_defer(ROOT, info["pending"])
                except (OSError, RuntimeError, ValueError):
                    pass
            print(json.dumps({"state": "error", "installed": info.get("installed"), "pending": info.get("pending"),
                              "installed_label": info.get("installed_label"), "pending_label": info.get("pending_label"),
                              "can_defer": may_defer, "message": str(error)}))
        else:
            print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
