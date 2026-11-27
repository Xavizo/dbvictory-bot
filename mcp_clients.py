"""Optional: register this bot's MCP server with Codex, Claude Desktop or Claude Code.

The bot runs without any of them. Connecting only adds a "dbvictory-bot" entry
to that program's own MCP configuration so it can start ``run_mcp.py``;
disconnecting removes exactly that entry. Other entries are kept, and each
configuration file is backed up once before the first change.

Usage: python mcp_clients.py status | connect TARGET | disconnect TARGET
TARGET: codex, claude-desktop, claude-code
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
NAME = "dbvictory-bot"
TARGETS = ("codex", "claude-desktop", "claude-code")
ENV = {"PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
LABELS = {"codex": "Codex", "claude-desktop": "Claude Desktop", "claude-code": "Claude Code"}


def server_command(root=ROOT):
    root = Path(root)
    python = root / "venv" / "Scripts" / "python.exe"
    return str(python), [str(root / "run_mcp.py")]


def _home():
    return Path(os.environ.get("USERPROFILE") or Path.home())


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_name(path.name + ".dbv-backup")
    if path.exists() and not backup.exists():
        shutil.copyfile(path, backup)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# ── Codex: ~/.codex/config.toml ─────────────────────────────────────

def codex_config():
    return Path(os.environ.get("CODEX_HOME") or _home() / ".codex") / "config.toml"


_KEY = r"""(?:[A-Za-z0-9_-]+|"[^"\\]*"|'[^']*')"""
_TABLE = re.compile(rf"^\s*\[\[?\s*({_KEY}(?:\s*\.\s*{_KEY})*)\s*\]\]?\s*(?:#.*)?$")


def _is_ours(header):
    key = header.replace('"', "").replace("'", "").replace(" ", "")
    return key == f"mcp_servers.{NAME}" or key.startswith(f"mcp_servers.{NAME}.")


def _strip_codex(text):
    """Remove our [mcp_servers.dbvictory-bot] table and its sub-tables only."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        match = _TABLE.match(line)
        if match:
            skipping = _is_ours(match.group(1))
        if not skipping:
            out.append(line)
    return "".join(out).rstrip() + ("\n" if out else "")


def _toml(value):
    return json.dumps(value, ensure_ascii=True)  # A JSON string is a valid TOML basic string.


def codex_connected():
    try:
        text = codex_config().read_text(encoding="utf-8")
    except OSError:
        return False
    return any((m := _TABLE.match(line)) and _is_ours(m.group(1)) for line in text.splitlines())


def codex_set(enabled, root=ROOT):
    path = codex_config()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    text = _strip_codex(text)
    if enabled:
        command, args = server_command(root)
        block = (f"\n[mcp_servers.{NAME}]\ncommand = {_toml(command)}\n"
                 f"args = [{', '.join(_toml(a) for a in args)}]\n\n[mcp_servers.{NAME}.env]\n"
                 + "".join(f"{k} = {_toml(v)}\n" for k, v in ENV.items()))
        text = (text + block).lstrip("\n")
    if text or path.exists():
        _write_text(path, text)


# ── Claude Desktop: claude_desktop_config.json ──────────────────────

def claude_desktop_configs():
    """The normal path and, for the Microsoft Store app, its private copy if it has one."""
    appdata = Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")
    paths = [appdata / "Claude" / "claude_desktop_config.json"]
    local = Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local")
    for package in sorted((local / "Packages").glob("Claude_*")):
        store = package / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
        if store.is_file():
            paths.append(store)
    return paths


def _read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Nieczytelna konfiguracja: {path}")
    return data


def claude_desktop_connected():
    for path in claude_desktop_configs():
        try:
            if NAME in (_read_json(path).get("mcpServers") or {}):
                return True
        except (OSError, ValueError):
            pass
    return False


def claude_desktop_set(enabled, root=ROOT):
    command, args = server_command(root)
    for path in claude_desktop_configs():
        data = _read_json(path)
        servers = data.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        if enabled:
            servers[NAME] = {"command": command, "args": args, "env": dict(ENV)}
        elif NAME not in servers:
            continue
        else:
            del servers[NAME]
        data["mcpServers"] = servers
        _write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ── Claude Code: its own CLI, user scope ────────────────────────────

def _claude_cli():
    return shutil.which("claude")


def _run(*args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=60, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def claude_code_connected():
    # ~/.claude.json is Claude Code's own state; only read it.
    try:
        data = json.loads((_home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and NAME in (data.get("mcpServers") or {})


def claude_code_set(enabled, root=ROOT):
    cli = _claude_cli()
    if not cli:
        raise RuntimeError("Nie znaleziono Claude Code (polecenie claude). Zainstaluj je i spróbuj ponownie.")
    if claude_code_connected():
        _run(cli, "mcp", "remove", "--scope", "user", NAME)
    if enabled:
        command, args = server_command(root)
        env = [part for key, value in ENV.items() for part in ("--env", f"{key}={value}")]
        result = _run(cli, "mcp", "add", "--scope", "user", *env, NAME, "--", command, *args)
        if result.returncode:
            raise RuntimeError("Claude Code odrzuciło dodanie serwera: "
                               + (result.stderr or result.stdout).strip()[:300])


def status(root=ROOT):
    return {
        "codex": {"label": LABELS["codex"], "connected": codex_connected(), "available": True},
        "claude-desktop": {"label": LABELS["claude-desktop"], "connected": claude_desktop_connected(),
                           "available": True},
        "claude-code": {"label": LABELS["claude-code"], "connected": claude_code_connected(),
                        "available": bool(_claude_cli()) or claude_code_connected()},
    }


def set_target(target, enabled, root=ROOT):
    setter = {"codex": codex_set, "claude-desktop": claude_desktop_set, "claude-code": claude_code_set}[target]
    setter(enabled, root)
    hint = {"codex": "Uruchom Codex ponownie, aby zobaczył zmianę.",
            "claude-desktop": "Zamknij całkowicie Claude Desktop (także z zasobnika) i otwórz ponownie.",
            "claude-code": "Nowe sesje Claude Code zobaczą zmianę."}[target]
    action = "Połączono" if enabled else "Odłączono"
    return {"status": status(root), "message": f"{action}: {LABELS[target]}. {hint}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("operation", choices=("status", "connect", "disconnect"))
    parser.add_argument("target", nargs="?", choices=TARGETS)
    args = parser.parse_args(argv)
    try:
        if args.operation == "status":
            result = {"status": status(), "message": ""}
        else:
            if not args.target:
                parser.error("Podaj program: " + ", ".join(TARGETS))
            result = set_target(args.target, args.operation == "connect")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
