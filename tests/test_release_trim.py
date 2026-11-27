import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_trim  # noqa: E402

LOCK = {
    "lockfileVersion": 3,
    "packages": {
        "": {"dependencies": {"next": "15", "react": "19"},
             "devDependencies": {"electron": "45", "wait-on": "9", "typescript": "5", "concurrently": "10"}},
        "node_modules/next": {"dependencies": {"@next/env": "1", "styled-jsx": "5", "postcss": "8"},
                              "optionalDependencies": {"@next/swc-win32-x64-msvc": "1", "sharp": "0"},
                              "peerDependencies": {"react": "19"}},
        "node_modules/@next/env": {},
        "node_modules/@next/swc-win32-x64-msvc": {"optional": True},
        "node_modules/sharp": {"optional": True, "dependencies": {"@img/sharp-win32-x64": "0"}},
        "node_modules/@img/sharp-win32-x64": {"optional": True},
        "node_modules/styled-jsx": {"dependencies": {"client-only": "0"}},
        "node_modules/client-only": {},
        "node_modules/postcss": {"dependencies": {"nanoid": "3"}},
        "node_modules/nanoid": {},
        "node_modules/react": {},
        "node_modules/electron": {"dev": True, "dependencies": {"@electron/get": "5"}},
        "node_modules/@electron/get": {"dev": True},
        "node_modules/wait-on": {"dev": True, "dependencies": {"rxjs": "7", "minimist": "1"}},
        "node_modules/wait-on/node_modules/minimist": {"dev": True},
        "node_modules/minimist": {"dev": True},
        "node_modules/rxjs": {"dev": True},
        "node_modules/typescript": {"dev": True},
        "node_modules/concurrently": {"dev": True, "dependencies": {"rxjs": "7"}},
    },
}


def stage(tmp_path):
    root = tmp_path / "DBVictory-Bot"
    files = ["venv/Scripts/python.exe", "venv/Scripts/Lib/site-packages/mcp/__init__.py",
             "venv/Scripts/Lib/site-packages/pip/__init__.py", "venv/Scripts/Lib/site-packages/pip-26.3.dist-info/RECORD",
             "venv/Scripts/Lib/site-packages/PyWin32.chm", "venv/Scripts/Lib/site-packages/win32/win32api.pyd",
             "venv/Scripts/Lib/tkinter/__init__.py", "venv/Scripts/Lib/ensurepip/__init__.py",
             "venv/Scripts/Lib/asyncio/__init__.py", "venv/Scripts/DLLs/tcl86t.dll", "venv/Scripts/DLLs/_ssl.pyd",
             "venv/Scripts/tcl/tcl8.6/init.tcl", "venv/Scripts/Scripts/pip.exe",
             "runtime/node/node.exe", "runtime/node/npm.cmd", "runtime/node/node_modules/npm/index.js",
             "runtime/node/node_modules/npm/docs/output/npm.html",
             "dashboard/.next/BUILD_ID", "dashboard/.next/static/x.js.map",
             "tests/test_x.py", "docs/screenshots/a.png", "docs/vbot/architecture.md", "requirements-dev.txt"]
    files += [f"dashboard/{key}/package.json" for key in LOCK["packages"] if key]
    files += ["dashboard/node_modules/next/dist/server/next.js", "dashboard/node_modules/next/dist/server/next.js.map",
              "dashboard/node_modules/next/dist/server/next.d.ts", "dashboard/node_modules/next/README.md",
              "dashboard/node_modules/next/license.md", "dashboard/node_modules/next/LICENSE",
              "dashboard/node_modules/electron/dist/electron.exe", "dashboard/node_modules/electron/dist/locales/pl.pak",
              "dashboard/node_modules/electron/index.js", "dashboard/node_modules/electron/cli.js",
              "dashboard/node_modules/electron/path.txt", "dashboard/node_modules/electron/install.js",
              "dashboard/node_modules/electron/node_modules/x/index.js", "dashboard/node_modules/.bin/next"]
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    (root / "dashboard" / "package-lock.json").write_text(json.dumps(LOCK))
    return root


def present(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_runtime_packages_follow_node_resolution():
    keep = release_trim.runtime_packages(LOCK)
    assert keep == {"node_modules/next", "node_modules/@next/env", "node_modules/styled-jsx",
                    "node_modules/client-only", "node_modules/postcss", "node_modules/nanoid",
                    "node_modules/react", "node_modules/electron", "node_modules/wait-on",
                    "node_modules/wait-on/node_modules/minimist", "node_modules/rxjs"}


def test_trim_keeps_what_players_run(tmp_path):
    root = stage(tmp_path)
    release_trim.trim(root)
    files = present(root)
    for kept in ("venv/Scripts/python.exe", "venv/Scripts/Lib/site-packages/mcp/__init__.py",
                 "venv/Scripts/Lib/site-packages/win32/win32api.pyd", "venv/Scripts/Lib/asyncio/__init__.py",
                 "venv/Scripts/DLLs/_ssl.pyd", "runtime/node/node.exe", "runtime/node/npm.cmd",
                 "runtime/node/node_modules/npm/index.js", "dashboard/.next/BUILD_ID", "dashboard/.next/static/x.js.map",
                 "dashboard/node_modules/next/dist/server/next.js", "dashboard/node_modules/next/LICENSE",
                 "dashboard/node_modules/next/license.md", "dashboard/node_modules/wait-on/package.json",
                 "dashboard/node_modules/wait-on/node_modules/minimist/package.json",
                 "dashboard/node_modules/rxjs/package.json", "dashboard/node_modules/electron/dist/electron.exe",
                 "dashboard/node_modules/electron/dist/locales/pl.pak", "dashboard/node_modules/electron/index.js",
                 "dashboard/node_modules/electron/cli.js", "dashboard/node_modules/electron/path.txt",
                 "dashboard/node_modules/.bin/next", "dashboard/package-lock.json", "docs/vbot/architecture.md"):
        assert kept in files, kept
    for dropped in ("tests/test_x.py", "docs/screenshots/a.png", "requirements-dev.txt",
                    "venv/Scripts/Lib/site-packages/pip/__init__.py",
                    "venv/Scripts/Lib/site-packages/pip-26.3.dist-info/RECORD",
                    "venv/Scripts/Lib/site-packages/PyWin32.chm", "venv/Scripts/Lib/tkinter/__init__.py",
                    "venv/Scripts/Lib/ensurepip/__init__.py", "venv/Scripts/DLLs/tcl86t.dll",
                    "venv/Scripts/tcl/tcl8.6/init.tcl", "venv/Scripts/Scripts/pip.exe",
                    "runtime/node/node_modules/npm/docs/output/npm.html",
                    "dashboard/node_modules/typescript/package.json", "dashboard/node_modules/concurrently/package.json",
                    "dashboard/node_modules/minimist/package.json", "dashboard/node_modules/sharp/package.json",
                    "dashboard/node_modules/@img/sharp-win32-x64/package.json",
                    "dashboard/node_modules/@next/swc-win32-x64-msvc/package.json",
                    "dashboard/node_modules/@electron/get/package.json", "dashboard/node_modules/electron/install.js",
                    "dashboard/node_modules/electron/node_modules/x/index.js",
                    "dashboard/node_modules/next/dist/server/next.js.map",
                    "dashboard/node_modules/next/dist/server/next.d.ts", "dashboard/node_modules/next/README.md"):
        assert dropped not in files, dropped
    assert not (root / "dashboard/node_modules/@img").exists()
    assert not (root / "venv/Scripts/tcl").exists()
