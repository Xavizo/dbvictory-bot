"""Standalone interactive launcher for the same MCP engine, without Codex.

No game attachment occurs until the user confirms the character-list state.
The panel can be reopened during play; stopping this console stops the proxy.
"""
import argparse
import asyncio
from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import socket
import sys
from tray_exit import EXIT_FILE_ENV, tray_input

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


@contextmanager
def launcher_lock(path):
    """OS releases the lock on exit/crash; don't delete another process's lock."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Bot jest już uruchomiony. W jego konsoli wpisz: panel') from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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


async def run(args, env):
    ports = (4747, 8089, 8090, int(env.get('DBV_LOGIN_PORT','7171')), args.game_port)
    if occupied_ports(ports):
        raise RuntimeError('Inna sesja bota lub program zajmuje porty. Nie uruchamiam drugiej. Jeśli bot działa, wpisz panel w jego konsoli.')
    with tray_input(sys.stdin.encoding) as (exit_file, lines):
        await run_console(args, {**env, EXIT_FILE_ENV: str(exit_file)}, lines)


async def run_console(args, env, lines):
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
                      'panel = otwórz panel | status = stan | actions = akcje | quit = zakończ bota',flush=True)
                async for line in lines:
                    command = line.strip().lower()
                    if command in {'quit','exit'}:
                        break
                    if not command:
                        continue
                    tools = {'panel':'open_dashboard','status':'get_status','actions':'list_actions'}
                    if command in tools:
                        try:
                            await call(session,tools[command])
                        except RuntimeError as exc:
                            print(exc,flush=True)
                    else:
                        print('Polecenia: panel, status, actions, quit',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-port',type=int,default=7475,help='DBVictory world port; current local client uses 7475')
    parser.add_argument('--start',action='store_true',help='Confirm that the client is already logged out; skip the prompt')
    args = parser.parse_args()
    if not 1 <= args.game_port <= 65535:
        parser.error('game-port must be 1..65535')
    if os.name != 'nt':
        parser.error('This launcher requires Windows.')
    os.chdir(ROOT)
    try:
        with launcher_lock(ROOT/'.codex/standalone.lock'):
            asyncio.run(run(args,prepare_environment(args.game_port)))
    except KeyboardInterrupt:
        print('\nBot zakończony. Ponowny start: z listy postaci.')
    except (OSError,RuntimeError,ValueError) as exc:
        print(f'BŁĄD: {exc}',file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
