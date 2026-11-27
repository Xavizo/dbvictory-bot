"""Remove build-only and unused files from a staged release folder.

Usage: python -I tools/release_trim.py STAGE

Runs after the panel is built and before tools/release_package.py seals the
stage. The engine serves the built panel (dashboard/.next) itself and shows it
in a pywebview window, so no panel package ships: Next.js, React and the
compilers were needed only by `next build`. Python's installer and GUI toolkit
are left out too.

Existing installations lose these files on update for free: the updater
removes every program file listed in the old manifest but not in the new one,
and puts off removing a file Windows still holds open until the next start.
"""
import argparse
import os
from pathlib import Path
import shutil
import stat

# Bundled Python (venv/Scripts holds the whole relocatable interpreter).
PYTHON_DROPPED = (
    "Lib/ensurepip", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/test",
    "Lib/site-packages/pip", "Lib/site-packages/pip-*.dist-info",
    "Lib/site-packages/PyWin32.chm", "Lib/site-packages/pythonwin",
    "Scripts/pip*.exe", "Scripts/idle*", "Scripts/pywin32_postinstall*", "Scripts/pywin32_testall*",
    "DLLs/_tkinter.pyd", "DLLs/tcl86t.dll", "DLLs/tk86t.dll", "tcl", "include", "libs",
    # CPython's own test modules and tools no program here imports.
    "DLLs/_test*.pyd", "DLLs/_ctypes_test.pyd", "DLLs/xxlimited*.pyd", "DLLs/_msi.pyd", "Lib/msilib",
    "Lib/venv", "Lib/pydoc_data", "Lib/lib2to3", "Lib/turtle.py",
    # cryptography (10 MB) comes with mcp only for PyJWT's optional algorithms and the MCP
    # client's OAuth helpers; PyJWT imports it guarded and the bot is an MCP server.
    "Lib/site-packages/cryptography", "Lib/site-packages/cryptography-*.dist-info",
    # pycryptodome's self-tests; the bot uses Crypto.PublicKey.RSA.
    "Lib/site-packages/Crypto/SelfTest",
    # pywin32's COM support, samples and tests; the bot uses win32api, win32gui, win32job & co.
    "Lib/site-packages/win32com", "Lib/site-packages/win32comext", "Lib/site-packages/win32/Demos",
    "Lib/site-packages/win32/test", "Lib/site-packages/win32/include", "Lib/site-packages/win32/libs",
    "Lib/site-packages/win32/scripts",
    # pywebview's Android bridge. Its WebView2 loaders stay for every architecture:
    # the EdgeChromium backend resolves all three on import.
    "Lib/site-packages/webview/lib/pywebview-android.jar",
)
# Program files from `git archive` that only developers use. docs/vbot stays: MCP serves it.
SOURCE_DROPPED = ("tests", "docs/screenshots", "requirements-dev.txt", "dashboard/electron/*.test.js",
                  "dashboard/electron/*.test.cjs", "dashboard/electron/*.cjs")
# The panel build: only the compiled output (minus its build caches) and public/ are served.
PANEL_DROPPED = ("node_modules", ".next/cache", ".next/trace", ".next/types")


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        def unlock(func, target, _):
            os.chmod(target, stat.S_IWRITE)
            func(target)
        shutil.rmtree(path, onerror=unlock)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _globbed(root, patterns):
    for pattern in patterns:
        yield from sorted(root.glob(pattern))


def trim(stage):
    stage = Path(stage)
    for required in ("venv/Scripts/python.exe", "dashboard/.next/BUILD_ID"):
        if not (stage / required).is_file():
            raise SystemExit(f"Stage is missing {required}")
    for path in _globbed(stage / "dashboard", PANEL_DROPPED):
        _remove(path)
    for path in _globbed(stage / "venv" / "Scripts", PYTHON_DROPPED):
        _remove(path)
    for path in _globbed(stage, SOURCE_DROPPED):
        _remove(path)
    _remove(stage / "runtime")  # Node.js only builds the panel; nothing ships it now


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", type=Path)
    args = parser.parse_args()
    stage = args.stage.resolve()
    if os.name == "nt":
        stage = Path("\\\\?\\" + str(stage))  # the panel build's paths can pass 260 characters
    before = sum(1 for _ in stage.rglob("*") if _.is_file())
    trim(stage)
    after = sum(1 for _ in stage.rglob("*") if _.is_file())
    print(f"Trimmed {before - after} files, {after} left")


if __name__ == "__main__":
    main()
