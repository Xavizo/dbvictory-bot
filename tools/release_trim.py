"""Remove build-only and unused files from a staged release folder.

Usage: python -I tools/release_trim.py STAGE

Runs after the panel is built and before tools/release_package.py seals the
stage. Players only run the built panel (`npm run start` and
`npm run electron:only`), so the compilers and type checkers that `next build`
needs are left out, along with Python's installer and GUI toolkit.

Existing installations lose these files on update for free: the updater
removes every program file listed in the old manifest but not in the new one.
Nothing here may be opened by the launcher while it installs an update
(the launcher's own Electron and the Python running the updater), because
Windows cannot rename such a file. That is why Electron's own files stay.
"""
import argparse
import fnmatch
import json
import os
from pathlib import Path
import shutil
import stat

# Panel packages needed at run time besides package.json "dependencies":
# the scripts players run call `wait-on` and `electron`.
RUNTIME_TOOLS = ("electron", "wait-on")
# Electron's own dependencies only download its binary on install.
LEAF_PACKAGES = {"electron"}
# Optional native helpers of `next`: the SWC compiler (build only) and sharp
# (next/image optimisation, which the panel does not use).
DROPPED_PACKAGES = ("@next/swc-*", "sharp", "@img/*")
# Inside the packages that stay: source maps, type declarations and docs.
NODE_JUNK = ("*.map", "*.d.ts", "*.d.mts", "*.d.cts", "*.md", "*.markdown", "*.flow")
NODE_KEEP_NAMES = {"license", "license.md", "licence", "licence.md", "copying"}

# Bundled Python (venv/Scripts holds the whole relocatable interpreter).
PYTHON_DROPPED = (
    "Lib/ensurepip", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/test",
    "Lib/site-packages/pip", "Lib/site-packages/pip-*.dist-info",
    "Lib/site-packages/PyWin32.chm", "Lib/site-packages/pythonwin",
    "Scripts/pip*.exe", "Scripts/idle*", "Scripts/pywin32_postinstall*", "Scripts/pywin32_testall*",
    "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "tcl", "include", "libs",
)
# Program files from `git archive` that only developers use. docs/vbot stays: MCP serves it.
SOURCE_DROPPED = ("tests", "docs/screenshots", "requirements-dev.txt")
NODE_RUNTIME_DROPPED = ("node_modules/npm/docs", "node_modules/npm/man", "node_modules/corepack",
                        "corepack", "corepack.cmd", "install_tools.bat", "CHANGELOG.md", "README.md")


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        def unlock(func, target, _):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        shutil.rmtree(path, onerror=unlock)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _parent(key):
    """Lockfile key of the folder a package's own node_modules lives in."""
    head, sep, _ = key.rpartition("/node_modules/")
    return head if sep else ""


def _name(key):
    return key.rsplit("node_modules/", 1)[-1]


def runtime_packages(lock):
    """Lockfile keys of the panel packages players need, resolved like Node does."""
    packages = lock.get("packages", {})
    root = packages.get("", {})
    wanted = [*root.get("dependencies", {}), *RUNTIME_TOOLS]

    def dropped(name):
        return any(fnmatch.fnmatchcase(name, pattern) for pattern in DROPPED_PACKAGES)

    def resolve(owner, name):
        base = owner
        while True:
            key = f"{base}/node_modules/{name}" if base else f"node_modules/{name}"
            if key in packages:
                return key
            if not base:
                return None
            base = _parent(base)

    keep, queue = set(), [(name, "") for name in wanted]
    while queue:
        name, owner = queue.pop()
        if dropped(name):
            continue
        key = resolve(owner, name)
        if key is None or key in keep:
            continue
        keep.add(key)
        if name in LEAF_PACKAGES:
            continue
        entry = packages[key]
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            queue += [(dep, key) for dep in entry.get(field, {})]
    return keep


def trim_panel(dashboard):
    lock = json.loads((dashboard / "package-lock.json").read_text(encoding="utf-8"))
    keep = runtime_packages(lock)
    if "node_modules/next" not in keep or "node_modules/electron" not in keep:
        raise SystemExit("package-lock.json no longer lists next and electron")
    for key in sorted(lock.get("packages", {}), key=len, reverse=True):
        if key and key not in keep:
            _remove(dashboard / key)
    electron = dashboard / "node_modules" / "electron"
    if electron.is_dir():
        # Leaves package.json, index.js, cli.js, path.txt and dist: what `electron .` runs.
        for name in ("node_modules", "install.js", "checksums.json", "electron.d.ts"):
            _remove(electron / name)
    for folder, dirs, names in os.walk(dashboard / "node_modules"):
        for name in names:
            if name.lower() in NODE_KEEP_NAMES:
                continue
            if any(fnmatch.fnmatch(name.lower(), pattern) for pattern in NODE_JUNK):
                _remove(Path(folder) / name)
    _prune_empty(dashboard / "node_modules")


def _globbed(root, patterns):
    for pattern in patterns:
        yield from sorted(root.glob(pattern))


def _prune_empty(top):
    for folder, dirs, names in os.walk(top, topdown=False):
        path = Path(folder)
        if path != top and not any(path.iterdir()):
            path.rmdir()


def trim(stage):
    stage = Path(stage)
    for required in ("venv/Scripts/python.exe", "dashboard/package-lock.json"):
        if not (stage / required).is_file():
            raise SystemExit(f"Stage is missing {required}")
    trim_panel(stage / "dashboard")
    for path in _globbed(stage / "venv" / "Scripts", PYTHON_DROPPED):
        _remove(path)
    for path in _globbed(stage, SOURCE_DROPPED):
        _remove(path)
    node = stage / "runtime" / "node"
    for path in _globbed(node, NODE_RUNTIME_DROPPED):
        _remove(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if os.name == "nt":
        stage = Path("\\\\?\\" + str(stage))  # node_modules paths can pass 260 characters
    before = sum(1 for _ in stage.rglob("*") if _.is_file())
    trim(stage)
    after = sum(1 for _ in stage.rglob("*") if _.is_file())
    print(f"Trimmed {before - after} files, {after} left")


if __name__ == "__main__":
    main()
