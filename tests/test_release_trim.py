import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_trim  # noqa: E402

SITE = "venv/Scripts/Lib/site-packages"


def stage(tmp_path):
    root = tmp_path / "DBVictory-Bot"
    files = ["venv/Scripts/python.exe", "venv/Scripts/pythonw.exe", f"{SITE}/mcp/__init__.py",
             f"{SITE}/pip/__init__.py", f"{SITE}/pip-26.3.dist-info/RECORD",
             f"{SITE}/PyWin32.chm", f"{SITE}/win32/win32api.pyd", f"{SITE}/win32/win32clipboard.pyd",
             f"{SITE}/webview/__init__.py", f"{SITE}/webview/lib/Microsoft.Web.WebView2.Core.dll",
             f"{SITE}/webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
             f"{SITE}/webview/lib/runtimes/win-x86/native/WebView2Loader.dll",
             f"{SITE}/webview/lib/runtimes/win-arm64/native/WebView2Loader.dll",
             f"{SITE}/webview/lib/pywebview-android.jar", f"{SITE}/webview/lib/WebBrowserInterop.x64.dll",
             f"{SITE}/webview/lib/WebBrowserInterop.x86.dll", f"{SITE}/pythonnet/runtime/Python.Runtime.dll",
             "venv/Scripts/Lib/tkinter/__init__.py", "venv/Scripts/Lib/ensurepip/__init__.py",
             "venv/Scripts/Lib/asyncio/__init__.py", "venv/Scripts/DLLs/tcl86t.dll", "venv/Scripts/DLLs/_ssl.pyd",
             "venv/Scripts/tcl/tcl8.6/init.tcl", "venv/Scripts/Scripts/pip.exe",
             "runtime/node/node.exe", "runtime/node/node_modules/npm/index.js",
             "dashboard/.next/BUILD_ID", "dashboard/.next/static/chunks/x.js",
             "dashboard/.next/server/app/index.html", "dashboard/.next/cache/webpack/x.pack",
             "dashboard/.next/trace", "dashboard/.next/types/app/page.ts",
             "dashboard/public/desktop-bridge.js", "dashboard/package.json", "dashboard/package-lock.json",
             "dashboard/node_modules/next/dist/server/next.js", "dashboard/node_modules/react/index.js",
             "dashboard/node_modules/.bin/next",
             "dashboard/electron/windows_alert.py", "dashboard/electron/tray.png", "dashboard/electron/sounds/player.wav",
             "dashboard/electron/atlas.test.js", "dashboard/electron/layout-regression.cjs",
             "tests/test_x.py", "launcher/desktop_app.py", "dbv/mcp_server/panel_server.py",
             "docs/screenshots/a.png", "docs/vbot/architecture.md", "requirements-dev.txt"]
    for rel in files:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    return root


def present(root):
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_trim_keeps_what_players_run(tmp_path):
    root = stage(tmp_path)
    release_trim.trim(root)
    files = present(root)
    for kept in ("venv/Scripts/python.exe", "venv/Scripts/pythonw.exe", f"{SITE}/mcp/__init__.py",
                 f"{SITE}/win32/win32api.pyd", f"{SITE}/win32/win32clipboard.pyd", f"{SITE}/webview/__init__.py",
                 f"{SITE}/webview/lib/Microsoft.Web.WebView2.Core.dll",
                 f"{SITE}/webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
                 f"{SITE}/webview/lib/WebBrowserInterop.x64.dll", f"{SITE}/pythonnet/runtime/Python.Runtime.dll",
                 f"{SITE}/webview/lib/runtimes/win-x86/native/WebView2Loader.dll",
                 f"{SITE}/webview/lib/runtimes/win-arm64/native/WebView2Loader.dll",
                 "venv/Scripts/Lib/asyncio/__init__.py", "venv/Scripts/DLLs/_ssl.pyd",
                 "dashboard/.next/BUILD_ID", "dashboard/.next/static/chunks/x.js",
                 "dashboard/.next/server/app/index.html", "dashboard/public/desktop-bridge.js",
                 "dashboard/package.json", "dashboard/package-lock.json",
                 "dashboard/electron/windows_alert.py", "dashboard/electron/tray.png",
                 "dashboard/electron/sounds/player.wav", "launcher/desktop_app.py",
                 "dbv/mcp_server/panel_server.py", "docs/vbot/architecture.md"):
        assert kept in files, kept
    for dropped in ("tests/test_x.py", "docs/screenshots/a.png", "requirements-dev.txt",
                    f"{SITE}/pip/__init__.py", f"{SITE}/pip-26.3.dist-info/RECORD", f"{SITE}/PyWin32.chm",
                    f"{SITE}/webview/lib/pywebview-android.jar",
                    "venv/Scripts/Lib/tkinter/__init__.py", "venv/Scripts/Lib/ensurepip/__init__.py",
                    "venv/Scripts/DLLs/tcl86t.dll", "venv/Scripts/tcl/tcl8.6/init.tcl", "venv/Scripts/Scripts/pip.exe",
                    "runtime/node/node.exe", "dashboard/.next/cache/webpack/x.pack", "dashboard/.next/trace",
                    "dashboard/.next/types/app/page.ts", "dashboard/node_modules/next/dist/server/next.js",
                    "dashboard/node_modules/react/index.js", "dashboard/node_modules/.bin/next",
                    "dashboard/electron/atlas.test.js", "dashboard/electron/layout-regression.cjs"):
        assert dropped not in files, dropped
    for folder in ("dashboard/node_modules", "venv/Scripts/tcl", "runtime"):
        assert not (root / folder).exists(), folder


def test_trim_refuses_a_stage_without_the_panel_build(tmp_path):
    root = stage(tmp_path)
    (root / "dashboard/.next/BUILD_ID").unlink()
    try:
        release_trim.trim(root)
    except SystemExit as error:
        assert "BUILD_ID" in str(error)
    else:
        raise AssertionError("trim accepted a stage without the panel build")


def test_trim_drops_unused_python_extras_but_keeps_what_the_bot_imports(tmp_path):
    root = stage(tmp_path)
    py = "venv/Scripts"
    dropped = [f"{SITE}/cryptography/__init__.py", f"{SITE}/cryptography-50.0.2.dist-info/METADATA",
               f"{SITE}/Crypto/SelfTest/__init__.py", f"{SITE}/win32com/__init__.py",
               f"{SITE}/win32comext/shell/shell.pyd", f"{SITE}/win32/Demos/x.py", f"{SITE}/win32/test/x.py",
               f"{SITE}/win32/libs/pywintypes.lib", f"{SITE}/win32/include/PyWinTypes.h", f"{SITE}/win32/scripts/x.py",
               f"{py}/DLLs/_testcapi.pyd", f"{py}/DLLs/_ctypes_test.pyd", f"{py}/DLLs/_msi.pyd",
               f"{py}/DLLs/xxlimited.pyd", f"{py}/Lib/msilib/__init__.py", f"{py}/Lib/venv/__init__.py",
               f"{py}/Lib/pydoc_data/topics.py", f"{py}/Lib/lib2to3/__init__.py", f"{py}/Lib/turtle.py"]
    kept = [f"{SITE}/Crypto/PublicKey/RSA.py", f"{SITE}/jwt/__init__.py", f"{SITE}/cffi/__init__.py",
            f"{SITE}/pycparser/__init__.py", f"{SITE}/win32/lib/win32con.py", f"{SITE}/win32/win32job.pyd",
            f"{SITE}/pywin32_system32/pywintypes312.dll", f"{SITE}/mcp-1.30.0.dist-info/METADATA",
            f"{py}/DLLs/_ctypes.pyd", f"{py}/DLLs/_sqlite3.pyd", f"{py}/Lib/unittest/__init__.py",
            f"{py}/Lib/pydoc.py"]
    for rel in dropped + kept:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x")
    release_trim.trim(root)
    files = present(root)
    assert not files & set(dropped)
    assert set(kept) <= files
