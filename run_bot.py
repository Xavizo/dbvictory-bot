"""Standalone interactive launcher for the same MCP engine, without Codex.

No game attachment occurs until the user confirms the character-list state.
The panel can be reopened during play; stopping this console stops the proxy.
"""
import sys

# The desktop shortcut supplies no arguments. Load the actual console only
# after the updater exits so all imports come from the installed release.
if __name__ == "__main__" and len(sys.argv) == 1:
    from pathlib import Path
    from launcher_files import desktop_files

    try:
        with desktop_files(Path(__file__).resolve().parent) as ready:
            if not ready:
                raise SystemExit(0)
            from run_bot import main
            raise SystemExit(main(session_lock_held=True))
    except (OSError, RuntimeError) as error:
        print(f"Błąd launchera: {error}", file=sys.stderr)
        raise SystemExit(1)

import argparse
import asyncio
from contextlib import nullcontext
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import socket
from tray_exit import EXIT_FILE_ENV, tray_input
from launcher_lock import launcher_lock

ROOT = Path(__file__).resolve().parent
# These actions must always require an explicit choice in the panel after start.
COMBAT_ACTIONS = frozenset({
    'adaptive_cavebot', 'cavebot', 'cavebot2', 'aoe_spell', 'auto_combat',
    'auto_rune_and_spell', 'auto_targeting', 'auto_targeting2',
})


def occupied_ports(ports):
    busy = []
    for port in ports:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.25):
                busy.append(port)
        except OSError:
            pass
    return busy


def prepare_environment(game_port):
    env = {**os.environ, 'PYTHONUTF8':'1', 'PYTHONUNBUFFERED':'1',
           'DBV_GAME_PORT':str(game_port), 'DBV_ALLOW_PACKET_CAPTURE':'0'}
    env.pop('DBV_DASHBOARD_TOKEN', None)
    # Prefer the already installed local runtime; don't download or install tools.
    runtimes = sorted((ROOT/'.codex/runtime').glob('node-v*-win-x64'), reverse=True)
    for runtime in runtimes:
        if (runtime/'node.exe').is_file() and (runtime/'npm.cmd').is_file():
            env['PATH'] = str(runtime)+os.pathsep+env.get('PATH','')
            break
    if not shutil.which('npm.cmd', path=env.get('PATH')):
        raise RuntimeError('Brak Node.js/npm. Zainstaluj narzędzia przed uruchomieniem bota.')
    if not (ROOT/'dashboard/.next/BUILD_ID').is_file():
        raise RuntimeError('Brak skompilowanego panelu. Wymagana kompilacja dashboard.')
    return env


async def call(session, name, args=None):
    result = await session.call_tool(name, args or {}, read_timeout_seconds=timedelta(seconds=90))
    text = '\n'.join(getattr(c, 'text', '') for c in result.content)
    if name == 'list_actions':
        text = text.split('\n--- ')[0]
    elif name == 'session_recovery_preview' and not result.isError:
        sample = result.structuredContent or json.loads(text)
        last = sample['last_waypoint'] or '—'
        upcoming = sample['next_waypoint'] or '—'
        timers = ', '.join(f'{item} ({seconds:g} s)' for item, seconds in sample['potion_seconds'].items())
        text = (f"Próba odczytu OK · HP {sample['hp']} · {tuple(sample['position'])}\n"
                f"Ostatni waypoint {last} · następny {upcoming}\n"
                f"Mikstury · {timers or 'brak użyć w próbce'}")
    print(text, flush=True)
    if result.isError or text.startswith('ERROR:'):
        raise RuntimeError(f'Nie powiodło się: {name}. Sesja nie została uruchomiona ponownie.')
    return text


async def start_checked(session, settings):
    await call(session, 'get_status')
    await call(session, 'list_actions')
    for name, config in settings.get('actions', {}).items():
        if name in COMBAT_ACTIONS and config.get('enabled'):
            await call(session, 'toggle_action', {'name':name, 'enabled':False})
    await call(session, 'start_bot')


async def run(args, env, *, on_confirm=None):
    ports = (4747, 8089, 8090, int(env.get('DBV_LOGIN_PORT','7171')), args.game_port)
    if occupied_ports(ports):
        raise RuntimeError('Inna sesja bota lub program zajmuje porty. Nie uruchamiam drugiej. Jeśli bot działa, wpisz panel w jego konsoli.')
    with tray_input(sys.stdin.encoding) as (exit_file, lines):
        await run_console(args, {**env, EXIT_FILE_ENV: str(exit_file)}, lines, on_confirm=on_confirm)


async def run_console(args, env, lines, *, on_confirm=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    print('DBVictory Bot — uruchamianie bez Codexa\n', flush=True)
    if not args.start:
        print('Pozostaw klienta na LIŚCIE POSTACI lub ekranie logowania.\n'
              'Start z zalogowanej gry zerwałby połączenie.\n'
              'Naciśnij Enter, gdy postać jest wylogowana; wpisz q, aby zrezygnować.', flush=True)
        answer = await anext(lines, '')
        if not answer or answer.strip().lower() != '':
            return
    if on_confirm is not None:
        on_confirm()
    settings = json.loads((ROOT/'bot_settings.json').read_text(encoding='utf-8')) if (ROOT/'bot_settings.json').exists() else {}
    params = StdioServerParameters(command=sys.executable,args=[str(ROOT/'run_mcp.py')],cwd=str(ROOT),env=env)
    log_path = ROOT/'.codex/standalone-stderr.log'
    with log_path.open('a',encoding='utf-8') as log:
        async with stdio_client(params,errlog=log) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                await start_checked(session,settings)
                print('\nGOTOWE — możesz zalogować postać. Walka i caveboty są OFF.\n'
                      'Zapisane pozostałe akcje (np. mikstury) ruszą po logowaniu.\n'
                      'Pozostaw tę konsolę otwartą (możesz zminimalizować).\n'
                      'panel = otwórz panel | status = stan | actions = akcje | recovery = próba odczytu | quit = zakończ bota',flush=True)
                async for line in lines:
                    command = line.strip().lower()
                    if command in {'quit','exit'}:
                        break
                    if not command:
                        continue
                    tools = {'panel':'open_dashboard','status':'get_status','actions':'list_actions',
                             'recovery':'session_recovery_preview'}
                    if command in tools:
                        try:
                            await call(session,tools[command])
                        except RuntimeError as exc:
                            print(exc,flush=True)
                    else:
                        print('Polecenia: panel, status, actions, recovery, quit',flush=True)


class LocalConsoleInput:
    """Keep an outstanding console read across controller replacements."""

    def __init__(self, stream):
        self.stream = stream
        self.pending = None

    async def readline(self, controller_output=None):
        if self.pending is None:
            self.pending = asyncio.create_task(asyncio.to_thread(self.stream.readline))
        if controller_output is not None:
            # The SDK closes the send side of its AnyIO stream on child stdout
            # EOF. Checking the count does not consume MCP responses.
            while controller_output.statistics().open_send_streams:
                done, _ = await asyncio.wait([self.pending], timeout=.25)
                if done:
                    break
            else:
                raise RuntimeError("Kontroler MCP zakończył pracę.")
        line = await asyncio.shield(self.pending)
        self.pending = None
        return line


async def local_controller_console(params, upstream_port, console_input):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        for name in ("get_status", "list_actions"):
            result = await session.call_tool(name, {})
            if result.isError:
                raise RuntimeError(f"Nie udało się odczytać {name}.")
        result = await session.call_tool("start_local_transport", {"upstream_port": upstream_port})
        if result.isError:
            raise RuntimeError(result.content[0].text)
        port = result.structuredContent["port"]
        print(f"Transport testowy 127.0.0.1:{port} -> 127.0.0.1:{upstream_port}", flush=True)
        print("status = stan | restart = uruchom MCP ponownie | quit = zakończ test", flush=True)
        while True:
            line = await console_input.readline(read)
            command = line.strip().lower()
            if not line or command == "quit":
                return False
            if command == "restart":
                return True
            if command == "status":
                await call(session, "get_status")


async def run_local_transport(upstream_port):
    from mcp import StdioServerParameters
    from mcp.shared.exceptions import McpError
    from transport_control import controller_endpoint
    from transport_session import LocalTransportSession

    manager = LocalTransportSession()
    console_input = LocalConsoleInput(sys.stdin)
    try:
        while True:
            try:
                async with controller_endpoint(manager, upstream_port) as control_env:
                    params = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "run_mcp.py")],
                                                   cwd=ROOT, env={**control_env, "PYTHONUTF8": "1"})
                    if not await local_controller_console(params, upstream_port, console_input):
                        return
            except (McpError, OSError, RuntimeError, ExceptionGroup):
                # Keep ownership of the data plane if only the controller fails.
                print("Błąd kontrolera MCP. Wpisz restart, aby połączyć ponownie, lub quit, aby zakończyć test.",
                      flush=True)
                if (await console_input.readline()).strip().lower() != "restart":
                    return
    finally:
        await manager.stop()


def main(*, session_lock_held=False):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-port',type=int,default=7475,help='DBVictory world port; current local client uses 7475')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--start',action='store_true',help='Confirm that the client is already logged out; skip the prompt')
    mode.add_argument('--local-transport', type=int, metavar='PORT',
                      help='Run a separate transport session for a loopback test server')
    args = parser.parse_args()
    if args.local_transport is not None:
        if not 1 <= args.local_transport <= 65535:
            parser.error('local-transport port must be 1..65535')
        try:
            asyncio.run(run_local_transport(args.local_transport))
        except KeyboardInterrupt:
            pass
        return 0
    if not 1 <= args.game_port <= 65535:
        parser.error('game-port must be 1..65535')
    if os.name != 'nt':
        parser.error('This launcher requires Windows.')
    os.chdir(ROOT)
    try:
        with (nullcontext() if session_lock_held else launcher_lock(ROOT/'.codex/standalone.lock')):
            asyncio.run(run(args,prepare_environment(args.game_port)))
    except KeyboardInterrupt:
        print('\nBot zakończony. Ponowny start: z listy postaci.')
    except (OSError,RuntimeError,ValueError) as exc:
        print(f'BŁĄD: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
