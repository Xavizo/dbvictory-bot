"""The panel's local API: HTTP for commands, a WebSocket for live state.

Both listen on loopback only and require the session token. The HTTP server
runs in a daemon thread and hands commands to the MCP server's event loop;
the WebSocket server runs on that loop and pushes the whole state to every
authenticated panel ten times a second.
"""
import asyncio
import itertools
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from dbv import paths
from dbv.brain.cavebot.cavebot_combat import validate_combat
from dbv.brain.navigation.map_atlas import database_busy
from dbv.brain.state.session_observations import observation_family_status
from dbv.mcp_server.dashboard_security import DASHBOARD_ORIGIN, allowed_origin, valid_token

log = logging.getLogger(name="dashboard_api")

API_PORT, WS_PORT = 8089, 8090
WS_PUSH_INTERVAL = 0.1     # seconds between state pushes
WS_AUTH_TIMEOUT = 5        # seconds a socket may stay open before sending its token
CALL_TIMEOUT = 5           # seconds to wait for a command on the MCP loop
ACTIONS_DIR = paths.ACTIONS_DIR

# Set by start_api(): the MCP server's loop and its BotState.
_main_loop = None
_state = None
_started = False
_background_tasks = set()   # tasks started here, referenced until the process ends


def _submit(method, *args):
    """Run ``method`` of the MCP server module (it runs as __main__) on its loop."""
    return asyncio.run_coroutine_threadsafe(getattr(sys.modules["__main__"], method)(*args), _main_loop)


def _action_name_ok(name):
    return all(c.isalnum() or c == "_" for c in name)


_COMMON_HEADERS = (("Vary", "Origin"), ("Cache-Control", "no-store"),
                   ("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"),
                   ("Access-Control-Allow-Headers", "Content-Type, Authorization"))
NOT_STARTED = {"error": "bot not started"}


class _Handler(BaseHTTPRequestHandler):  # one instance per request, on the server's thread
    """Routes /api/... requests. Every request is checked by _authorize first."""

    def log_message(self, format, *args):
        log.debug(format, *args)  # never to stderr's access log format

    def _cors_headers(self) -> None:
        if allowed_origin(self.headers.get("Origin")):
            self.send_header("Access-Control-Allow-Origin", DASHBOARD_ORIGIN)
        for header, value in _COMMON_HEADERS:
            self.send_header(header, value)

    def _not_started(self, need_state=False):
        """Answer 503 and return True when the bot (or its state) is not there yet."""
        if _main_loop is None or (need_state and _state is None):
            self._json_response(NOT_STARTED, 503)
            return True
        return False

    def _authorize(self, preflight=False):
        # Validate Host as well as Origin to reject DNS-rebinding requests.
        port = self.server.server_port
        if self.headers.get("Host") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._json_response({"error": "invalid host"}, 403)
            return False
        origin = self.headers.get("Origin")
        if (origin is not None or preflight) and not allowed_origin(origin):
            self._json_response({"error": "origin not allowed"}, 403)
            return False
        if not preflight:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or not valid_token(auth[7:]):
                self._json_response({"error": "authentication required"}, 401)
                return False
        return True


    def do_OPTIONS(self) -> None:
        if self._authorize(preflight=True):
            self._send(204, b"", None)

    def _send(self, status, body: bytes, content_type="application/json"):
        self.send_response(code=status)
        if content_type:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self._cors_headers()   # also on errors, so the panel can read them
        self.end_headers()     # headers are flushed here, the body follows
        if body:
            self.wfile.write(body)

    def _drain_rejected_body(self):
        # HTTP/1.0 closes after the response. On Windows, closing with unread
        # request data can reset TCP before the peer receives our 4xx. Drain
        # only small rejected bodies, with a strict time bound, never parse them.
        previous_timeout = self.connection.gettimeout()
        try:
            length = int(self.headers.get("Content-Length", 0))
            if 0 < length <= 16384:
                self.connection.settimeout(.2)
                self.rfile.read(length)
                self._body_read = True
        except (OSError, ValueError):
            pass
        finally:
            self.connection.settimeout(previous_timeout)

    def _json_response(self, data: dict, status: int = 200):
        if status >= 400 and not getattr(self, "_body_read", False):
            self._drain_rejected_body()
        self._send(status, json.dumps(data).encode())

    def _read_body(self):
        """The request's JSON body; {} when it has none."""
        size = int(self.headers.get("Content-Length", 0))
        if size == 0:
            return dict()
        raw = self.rfile.read(size)
        self._body_read = True
        return json.loads(raw)

    # ── routes ──────────────────────────────────────────────────────
    def do_GET(self) -> None:
        if not self._authorize():
            return
        if self.path == "/api/profiles":
            store=getattr(_state,"profile_store",None)
            if store is None:return self._json_response({"error":"Profile niedostępne."},503)
            try:return self._json_response({"profiles":store.list()})
            except (OSError,ValueError):return self._json_response({"error":"Nie można odczytać profili."},500)
        if self.path == "/api/alerts":
            center = getattr(_state, "alert_center", None)
            if center is None:
                return self._json_response({"error": "Alerty niedostępne."}, 503)
            return self._json_response(center.snapshot())
        plain = {"/api/state": self._handle_get_state, "/api/recordings": self._handle_list_recordings}.get(self.path)
        if plain:
            return plain()
        if urlsplit(self.path).path == "/api/message-history":
            query = parse_qs(urlsplit(self.path).query)
            try:
                limit = int(query.get("limit", ["50"])[0])
                before = int(query["before"][0]) if "before" in query else None
            except ValueError:
                return self._json_response({"error": "Nieprawidłowy limit lub kursor."}, 400)
            return self._run_control("read_message_history", query.get("channel", ["general"])[0], limit, before)
        if self.path == "/api/movement-history":
            return self._movement_history()
        if urlsplit(self.path).path == "/api/cavebot/reports":
            from dbv.brain.cavebot.cavebot_history import read_reports
            import math
            import sqlite3
            values = parse_qs(urlsplit(self.path).query)
            ident = values.get('id',[None])[0]
            try:
                before = float(values['before'][0]) if 'before' in values else None
                if before is not None and not math.isfinite(before):
                    raise ValueError()
                result = read_reports(_state,ident,before)
            except ValueError:
                return self._json_response({'error':'Nieprawidłowy zakres raportów.'},400)
            except (OSError,sqlite3.Error):
                return self._json_response({'error':'Odczyt raportów chwilowo niedostępny.'},503)
            return self._json_response(result if result is not None else {'error':'Nie znaleziono raportu.'},
                                       200 if result is not None else 404)

        if urlsplit(self.path).path == "/api/map/atlas":
            return self._handle_atlas()
        if urlsplit(self.path).path == "/api/map/zones":
            return self._hunt_areas(zones=True)
        if self.path == "/api/map/grounds":
            return self._hunt_areas()
        if urlsplit(self.path).path == "/api/map/discoveries":
            return self._hunt_areas(discovered=True)
        if urlsplit(self.path).path == "/api/map/world":
            import sqlite3
            import zlib
            from dbv.brain.navigation.world_minimap import client_minimap, world_floor
            query = parse_qs(urlsplit(self.path).query)
            try:
                floor = int(query.get('z', ['7'])[0])
                if not 0 <= floor <= 15:
                    raise ValueError()
            except ValueError:
                return self._json_response({'error': 'Nieprawidłowe piętro.'}, 400)
            atlas = getattr(getattr(_state, 'passive_map_monitor', None), 'atlas', None)
            try:
                result = world_floor(client_minimap(), getattr(atlas, 'path', None), floor,
                                     since=query.get('since', [None])[0], tile_count=getattr(atlas, 'total', 0))
            except (KeyError, FileNotFoundError):
                return self._json_response({'error': 'Brak zapisanej minimapy DBVictory.'}, 404)
            except (OSError, ValueError, sqlite3.Error, zlib.error):
                return self._json_response({'error': 'Nie można odczytać zapisanej mapy. Spróbuj ponownie.'}, 503)
            return self._json_response(result)
        if self.path == "/api/map/drafts":
            return self._handle_map_drafts()
        if urlsplit(self.path).path == "/api/map/drafts/export":
            return self._handle_route_export()
        return self._named_route("GET")

    def do_POST(self) -> None:
        if not self._authorize():
            return
        if self.path == "/api/movement-history":
            return self._movement_history(save=True)
        if self.path == "/api/map/grounds":
            return self._hunt_areas(save=True)
        if self.path == "/api/map/grounds/route":
            return self._hunt_areas(route=True)
        if self.path == "/api/profiles":
            try:
                if not 0<int(self.headers.get("Content-Length",0))<=65536:raise ValueError("Profil jest zbyt duży.")
                body=self._read_body()
                if not isinstance(body,dict):raise ValueError("Nieprawidłowy profil.")
                return self._run_control("_async_profiles",body)
            except (ValueError,TypeError) as exc:return self._json_response({"error":str(exc)},400)
        if self.path == "/api/alerts":
            try:
                if not 0 < int(self.headers.get("Content-Length", 0)) <= 4096:
                    raise ValueError("Nieprawidłowy rozmiar ustawień alertów.")
                body = self._read_body()
                if not isinstance(body, dict) or body.get("command") not in {"configure", "acknowledge", "set_paused"}:
                    raise ValueError("Nieprawidłowe polecenie alertów.")
                return self._run_control("_async_alerts", body["command"], body)
            except (ValueError, TypeError) as exc:
                return self._json_response({"error": str(exc)}, 400)
        if self.path == "/api/emergency-stop":
            return self._run_control("_async_emergency_stop")
        if self.path == "/api/automation/unlock":
            return self._run_control("_async_unlock_automation")
        if self.path == "/api/automation/pause":
            return self._run_control("_async_pause_automation")
        if self.path == "/api/automation/resume":
            return self._run_control("_async_resume_automation")
        if self.path == "/api/adaptive/preview":
            try:
                if not 0<int(self.headers.get("Content-Length",0))<=16384:raise ValueError('Nieprawidłowy rozmiar konfiguracji.')
                body=self._read_body()
                if not isinstance(body,dict):raise ValueError('Nieprawidłowa konfiguracja.')
                return self._run_control('_async_adaptive_preview',body.get('combat'))
            except (ValueError,TypeError) as exc:return self._json_response({'error':str(exc)},400)
        if self.path == "/api/adaptive/control":
            try:
                if not 0<int(self.headers.get("Content-Length",0))<=16384:raise ValueError("Nieprawidłowy rozmiar żądania.")
                body=self._read_body()
                if not isinstance(body,dict) or body.get("command") not in {"start","start_combat","configure_combat","pause","resume","stop","discard_progress"}:raise ValueError("Nieznane polecenie V2.")
                return self._run_control("_async_adaptive_control",body["command"],body)
            except (ValueError,TypeError) as exc:return self._json_response({"error":str(exc)},400)
        if self.path == "/api/potions/diagnostics":
            body = self._read_body()
            if type(body.get("enabled")) is not bool:
                return self._json_response({"error": "enabled must be boolean"}, 400)
            return self._run_control("_async_potion_diagnostics", body["enabled"])
        if self.path == "/api/diagnostics/item-identification":
            body = self._read_body()
            if not isinstance(body, dict) or type(body.get("enabled")) is not bool:
                return self._json_response({"error": "enabled must be boolean"}, 400)
            return self._run_control("_async_item_identification", body["enabled"])
        if self.path == "/api/map/pet":
            try:
                from dbv.brain.navigation.transition_learning import position
                if not 0 < int(self.headers.get("Content-Length", 0)) <= 1024:
                    raise ValueError("Nieprawidłowy rozmiar oznaczenia.")
                body = self._read_body()
                required = {"position", "ref", "name"}
                if (not isinstance(body, dict) or not required <= body.keys()
                        or body.keys() - (required | {"remember"})
                        or not isinstance(body.get("remember", False), bool)
                        or not position(body["position"]) or not isinstance(body["ref"], str)):
                    raise ValueError("Wybierz jedno stworzenie na mapie na żywo.")
                if body.get("remember"):
                    raise ValueError("Odśwież panel. Możesz oznaczyć tylko wybrane stworzenie.")
                if not _state.connected:
                    raise ValueError("Mapa czeka na połączenie.")
                _state.game_state.pets.label_visible(tuple(body["position"]), body["ref"], body["name"])
                return self._json_response({"ok": True})
            except (ValueError, TypeError) as exc:
                return self._json_response({"error": str(exc)}, 400)
        if self.path == "/api/map/annotation":
            monitor=getattr(_state,"passive_map_monitor",None)
            if monitor is None:
                return self._json_response({"error":"Podgląd mapy nie jest uruchomiony."},503)
            try:
                if not 0<int(self.headers.get("Content-Length",0))<=4096:
                    return self._json_response({"error":"Opis pola ma nieprawidłowy rozmiar."},400)
                message=monitor.save_annotation(self._read_body())
                return self._json_response({"ok":True,"message":message})
            except ValueError as exc:
                return self._json_response({"error":str(exc)},400)
            except Exception:
                return self._json_response({"error":"Nie udało się zapisać opisu pola."},500)
        if self.path == "/api/map/plan":
            return self._handle_map_plan()
        if self.path == "/api/map/drafts":
            return self._handle_map_drafts(save=True)
        if self.path == "/api/map/drafts/import":
            return self._handle_route_import()
        return self._named_route("POST")

    def do_DELETE(self) -> None:
        if self._authorize():
            self._named_route("DELETE")

    def _named_route(self, method):
        """Routes that carry a name: /api/actions/<name>/..., /api/recordings/<name>/..."""
        parts = self.path.strip("/").split(sep="/")
        kind, rest = (parts[1], parts[2:]) if len(parts) >= 3 and parts[0] == "api" else (None, [])
        if kind == "actions" and len(rest) == (2 if method == "POST" else 1) and method != "GET":
            name = rest[0]
            if not _action_name_ok(name):
                return self._json_response({"error": "invalid action name"}, 400)
            handler = ({"toggle": self._handle_toggle, "restart": self._handle_restart,
                        "config": self._handle_action_config}.get(rest[1]) if method == "POST"
                       else self._handle_delete)
            if handler:
                return handler(name)
        if kind == "recordings":
            if method == "GET" and len(rest) == 1:
                return self._handle_get_recording(unquote(rest[0]))
            if method == "DELETE" and len(rest) == 1:
                return self._handle_delete_recording(unquote(rest[0]))
            if method == "POST" and len(rest) == 2 and rest[1] == "remove_waypoints":
                return self._handle_remove_waypoints(unquote(rest[0]))
        if kind == "cavebot" and method == "GET" and len(rest) == 2 and rest[0] == "actions_map":
            return self._handle_get_actions_map(rest[1])
        recording = {"/api/cavebot/record/start": self._handle_cavebot_record_start,
                     "/api/cavebot/record/stop": self._handle_cavebot_record_stop}.get(self.path)
        if method == "POST" and recording:
            return recording()
        return self._json_response({"error": "not found"}, 404)

    def _run_control(self, method, *args):
        """Answer with the result of an MCP server command: ValueError is the caller's fault (400)."""
        if self._not_started(need_state=True):
            return None
        future = _submit(method, *args)
        try:
            self._json_response({"ok": True, "message": future.result(timeout=CALL_TIMEOUT)})
        except ValueError as exc:
            self._json_response({"error": str(exc)}, 400)
        except Exception as exc:
            future.cancel()
            self._json_response({"error": str(exc)}, 500)

    def _handle_get_state(self) -> None:
        self._send(200, _build_state_json().encode("utf-8"))

    def _hunt_areas(self, save=False, route=False, discovered=False, zones=False):
        import sqlite3
        from dbv.brain.hunts.hunt_areas import catalogue, save_area
        atlas = getattr(getattr(_state, 'passive_map_monitor', None), 'atlas', None)
        if atlas is None:
            return self._json_response({'error':'Atlas czeka na pierwszy odczyt.'},503)
        try:
            if zones:
                from dbv.brain.hunts.hunt_world import world_zone_update
                since=parse_qs(urlsplit(self.path).query).get("since",[None])[0]
                return self._json_response(world_zone_update(atlas,since))
            if discovered:
                from dbv.brain.hunts.manual_hunts import discoveries, storage
                try:
                    stat = storage(atlas).stat()
                    revision = f'{stat.st_mtime_ns:x}-{stat.st_size:x}'
                except FileNotFoundError:
                    revision = 'empty'
                body = {'scope':atlas.scope,'revision':revision}
                if parse_qs(urlsplit(self.path).query).get('since')!=[revision]:
                    body['discoveries']=discoveries(atlas)
                return self._json_response(body)
            if route:
                from dbv.brain.hunts.hunt_routes import propose
                if not 0 < int(self.headers.get('Content-Length',0)) <= 256*1024:
                    raise ValueError('Nieprawidłowe dane propozycji.')
                body = self._read_body()
                if not isinstance(body, dict) or body.get('scope') != atlas.scope:
                    raise ValueError('Zmieniono atlas. Wczytaj obszary ponownie.')
                # The panel has one HTTP thread. A city-wide path search must
                # leave it free to process stop, healing and other controls.
                if not hasattr(self.server, 'route_jobs'):
                    self.server.route_jobs = {}
                    self.server.route_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='atlas-route')
                jobs = self.server.route_jobs
                if 'ticket' in body:
                    ticket = body['ticket']
                    job = jobs.get(ticket) if isinstance(ticket,str) else None
                    if job is None or job[0] is not atlas:
                        raise ValueError('Propozycja wygasła. Przelicz trasę ponownie.')
                    if job[1].done():
                        try:
                            return self._json_response(job[1].result())
                        finally:
                            jobs.pop(ticket)
                    return self._json_response({'ticket':ticket},202)
                if any(not job.done() for _,job in jobs.values()):
                    return self._json_response({'error':'Atlas wyznacza inną trasę. Spróbuj ponownie za chwilę.'},409)
                while len(jobs) >= 4:
                    jobs.pop(next(iter(jobs)))
                learner = getattr(_state.passive_map_monitor, 'learner', None)
                rows = list(learner.records.values()) if learner else []
                ticket = uuid4().hex
                jobs[ticket] = atlas,self.server.route_worker.submit(propose,atlas,rows,body)
                return self._json_response({'ticket':ticket},202)
            if save:
                if not 0 < int(self.headers.get('Content-Length',0)) <= 2*1024*1024:
                    raise ValueError('Nieprawidłowy rozmiar danych obszaru.')
                return self._json_response({'id':save_area(atlas,self._read_body())})
            return self._json_response(catalogue(atlas))
        except (ValueError, TypeError) as exc:
            return self._json_response({'error':str(exc)},400)
        except (OSError, sqlite3.Error):
            return self._json_response({'error':'Atlas jest chwilowo niedostępny.'},503)

    def _movement_history(self, save=False):
        history = getattr(getattr(_state, "game_state", None), "movement_history", None)
        if history is None:
            if save:return self._json_response({"error":"Historia ruchu czeka na zalogowanie postaci."},503)
            return self._json_response(dict(active=False,capturing=False,available=False,latest=None,error=None,unwritten_reports=0))
        if save:
            if self.headers.get("Content-Length","0") != "0":
                return self._json_response({"error":"Zapis nie przyjmuje parametrów."},400)
            try:
                history.manual_save()
            except ValueError as exc:return self._json_response({"error":str(exc)},400)
            except OSError:return self._json_response({"error":"Nie udało się zapisać raportu na dysku."},500)
        return self._json_response(history.status())

    def _handle_atlas(self):
        monitor=getattr(_state,"passive_map_monitor",None)
        atlas=getattr(monitor,"atlas",None)
        if atlas is None:return self._json_response({"error":"Atlas czeka na pierwszy odczyt mapy."},503)
        try:
            query=parse_qs(urlsplit(self.path).query)
            if set(query)-{"x","y","z","radius"}:raise ValueError()
            values={k:int(query[k][0]) for k in ("x","y","z")}
            view=atlas.view(**values,radius=int(query.get("radius",[20])[0]))
            # Enrich old cached cells using current profile evidence, still
            # matching the last seen item IDs. This never claims a fresh state.
            if getattr(monitor,"annotations",None):view["cells"]=monitor.annotations.annotate(view["cells"])
            if getattr(monitor,"learner",None):view["cells"]=monitor.learner.annotate(view["cells"])
            rows=list(monitor.learner.records.values()) if getattr(monitor,'learner',None) else []
            try:
                view['route_transitions']=atlas.route_transitions(rows,view['cells'])
            except sqlite3.OperationalError as exc:
                if not database_busy(exc):raise
                view['route_transitions']=[]
                view['error']=view['error'] or 'Baza atlasu jest zajęta. Odświeżam przejścia.'
            return self._json_response(view)
        except (ValueError,KeyError,TypeError):return self._json_response({"error":"Nieprawidłowy obszar atlasu."},400)

    def _handle_map_drafts(self,save=False):
        atlas=getattr(getattr(_state,"passive_map_monitor",None),"atlas",None)
        if atlas is None:return self._json_response({"error":"Atlas nie jest jeszcze dostępny."},503)
        try:
            draft_id=None
            if save:
                if not 0<int(self.headers.get("Content-Length",0))<=131072:raise ValueError("Szkic jest zbyt duży.")
                body=self._read_body()
                if body.get("scope")!=atlas.scope:raise ValueError("Profil atlasu się zmienił. Odśwież widok.")
                controller=getattr(_state,'adaptive_cavebot',None)
                active_draft = (body.get('id') and controller and controller.status in {'running','paused','ready'}
                                and getattr(controller, 'draft_id', None) == body['id'])
                if active_draft:
                    if body.get('operation') not in {None, 'set_tags'}:
                        raise ValueError('Zatrzymaj Cavebota przed tą operacją na aktywnej trasie.')
                    if body.get('operation') is None and body.get('expected_updated') is None:
                        raise ValueError('Odśwież szkic przed zapisem, aby sprawdzić jego wersję.')
                if body.get('operation') == 'set_tags':
                    atlas.set_tags(body.get('id'), body.get('tags'), body.get('previous'))
                elif body.get('operation') in {'delete','restore','configure','set_loop'}:
                    atlas.change_draft(body.get('id'),body['operation'],body.get('combat'),body.get('loop'))
                elif body.get('operation') is not None:raise ValueError('Nieznana operacja szkicu.')
                else:draft_id=atlas.save_draft(body.get("name"),body.get("points"),body.get("id"),body.get("loop",True),body.get('expected_updated'),body.get('labels'),body.get('links'),body.get('tags'),body.get('terrain'))
            return self._json_response({"scope":atlas.scope,"drafts":atlas.drafts(),"trash":atlas.drafts(deleted=True),"saved_id":draft_id})
        except (BlockingIOError, sqlite3.OperationalError) as exc:
            if isinstance(exc, BlockingIOError) or database_busy(exc):
                return self._json_response({"error":"Baza atlasu jest chwilowo zajęta. Spróbuj ponownie."},503)
            return self._json_response({"error":"Nie udało się odczytać lub zapisać szkiców."},500)
        except (ValueError,TypeError,KeyError) as exc:return self._json_response({"error":str(exc)},400)
        except Exception:return self._json_response({"error":"Nie udało się odczytać lub zapisać szkiców."},500)

    def _handle_route_export(self):
        """GET /api/map/drafts/export?id=<route>: the route as a shareable route pack."""
        from dbv.brain.navigation import route_pack
        monitor=getattr(_state,"passive_map_monitor",None)
        atlas=getattr(monitor,"atlas",None)
        if atlas is None or not getattr(monitor,"server",None):
            return self._json_response({"error":"Atlas nie jest jeszcze dostępny."},503)
        try:
            draft_id=parse_qs(urlsplit(self.path).query).get("id",[""])[0]
            observations=list(monitor.learner.records.values()) if getattr(monitor,"learner",None) else []
            pack=route_pack.export(atlas,observations,draft_id,monitor.server)
            return self._json_response({"file_name":route_pack.file_name(pack["drafts"][0]["name"]),"pack":pack})
        except (BlockingIOError, sqlite3.OperationalError):
            return self._json_response({"error":"Baza atlasu jest chwilowo zajęta. Spróbuj ponownie."},503)
        except ValueError as exc:return self._json_response({"error":str(exc)},400)
        except Exception:
            log.warning("Route export failed", exc_info=True)
            return self._json_response({"error":"Nie udało się wyeksportować trasy."},500)

    def _handle_route_import(self):
        """POST /api/map/drafts/import {scope, pack}: add a shared route as a new route."""
        from dbv.brain.navigation import route_pack
        monitor=getattr(_state,"passive_map_monitor",None)
        atlas=getattr(monitor,"atlas",None)
        if atlas is None or not getattr(monitor,"server",None):
            return self._json_response({"error":"Zaloguj się do gry, aby zaimportować trasę."},503)
        try:
            if not 0<int(self.headers.get("Content-Length",0))<=route_pack.MAX_FILE:
                raise ValueError("Plik trasy jest zbyt duży.")
            body=self._read_body()
            if not isinstance(body,dict) or body.get("scope")!=atlas.scope:
                raise ValueError("Profil atlasu się zmienił. Odśwież widok.")
            created=route_pack.import_pack(atlas,getattr(monitor,"learner",None),body.get("pack"),monitor.server)
            return self._json_response({"scope":atlas.scope,"drafts":atlas.drafts(),"trash":atlas.drafts(deleted=True),
                                        "imported":created})
        except (BlockingIOError, sqlite3.OperationalError):
            return self._json_response({"error":"Baza atlasu jest chwilowo zajęta. Spróbuj ponownie."},503)
        except (ValueError,TypeError) as exc:return self._json_response({"error":str(exc)},400)
        except Exception:
            log.warning("Route import failed", exc_info=True)
            return self._json_response({"error":"Nie udało się zaimportować trasy."},500)

    def _handle_map_plan(self):
        monitor=getattr(_state,"passive_map_monitor",None)
        atlas=getattr(monitor,"atlas",None)
        if atlas is None:return self._json_response({"error":"Atlas nie jest jeszcze dostępny."},503)
        try:
            if not 0<int(self.headers.get("Content-Length",0))<=131072:raise ValueError("Plan jest zbyt duży.")
            body=self._read_body()
            if body.get("scope")!=atlas.scope:raise ValueError("Profil atlasu się zmienił. Odśwież widok.")
            original=None;interactions=[];recording=None;links=body.get("links");adjusted=[]
            hunt = None
            observations=list(monitor.learner.records.values()) if monitor.learner else []
            if "recording" in body:
                from dbv.brain.cavebot import cavebot
                name=body["recording"]
                if name not in {r["name"] for r in cavebot.list_recordings()}:raise ValueError("Nie ma takiego nagrania.")
                recording=cavebot.load_recording(name)
                optimize=body.get('optimize',False)
                if type(optimize) is not bool:raise ValueError('Nieprawidłowa opcja optymalizacji.')
                if recording and recording.get("recording_mode") == "adaptive" and body.get("full_trace") is not True:
                    from dbv.brain.cavebot.adaptive_recording import hunting_excerpt
                    if recording.get("atlas_scope") != atlas.scope:
                        raise ValueError("To nagranie pochodzi z innego atlasu.")
                    observations = atlas.remember_recorded_ropes(recording, observations)
                    observations = atlas.recording_step_observations(observations)
                    source_recording = recording
                    ground = atlas.ordinary_recorded_tiles(recording, observations)
                    unavailable = atlas.unavailable_recorded_crossings(recording, observations)
                    recording, hunt = hunting_excerpt(recording, observations, ordinary_tiles=ground, unavailable=unavailable)
                    optimize = True
                points,original,links,adjusted=atlas.recording_route(recording,observations,optimize)
            else:points=body.get("points")
            loop=body.get("loop") if "loop" in body else False
            if type(loop)is not bool:raise ValueError("Nieprawidłowa opcja pętli.")
            result=atlas.plan(points,observations,original,loop=loop,links=links)
            if hunt is not None:
                from dbv.brain.cavebot.adaptive_recording import sector, stays_near_trace
                required = {sector(p) for p in recording["coverage_anchors"]}
                if (not result["complete"] or not required.issubset(map(sector, result["path"]))
                        or not stays_near_trace(result["path"], recording["observed_path"])):
                    if hunt.get("reordered"):
                        recording, hunt = hunting_excerpt(source_recording, observations, reorder=False)
                    points, original, links, adjusted = atlas.recording_route(recording, observations, False)
                    result = atlas.plan(points, observations, original, loop=loop, links=links)
                    if not stays_near_trace(result["path"], recording["observed_path"]):
                        raise ValueError("Droga przez atlas wychodzi poza zaobserwowany obszar expienia. Możesz wczytać pełny ślad.")
                covered = len(required.intersection(map(sector, result["path"])))
                if result["complete"] and covered < len(required):
                    raise ValueError(f'Plan atlasu omija {len(required)-covered} odwiedzonych sektorów. Pełny ślad pozostaje dostępny.')
                result["adaptive_evidence"] = {**hunt, "covered":covered}
            if recording is None and body.get('optimize') is True:
                from dbv.brain.navigation.map_atlas import recording_points
                from dbv.brain.navigation.map_atlas import validate_labels,remap_links
                from dbv.brain.hunts.hunt_routes import shorter_draft
                labels=validate_labels(body.get('labels'),len(points))
                if not result['complete']:result['optimization_blocked']=True
                elif (shortened:=shorter_draft(atlas,observations,result,labels)) is not result:
                    result=shortened
                else:
                    optimized,original=recording_points({'observed_path':[{'pos':p} for p in result['path'][:result['return_start']+1]]},observations,optimize=True,keep_positions=[p for p,label in zip(points,labels) if label]+[p for link in result['links'] for p in points[link['from_index']:link['from_index']+2]])
                    candidate=atlas.plan(optimized,observations,original,loop=loop,links=remap_links(result['links'],points,optimized))
                    if (candidate['complete'] and candidate['return_mode']==result['return_mode']
                            and candidate['transitions']==result['transitions']
                            and (len(candidate['points'])<=len(points) or len(candidate['path'])<len(result['path']))):
                        result=candidate
            if recording is not None:
                from dbv.brain.navigation.map_atlas import review_recorded_actions
                interactions=review_recorded_actions(recording,result['transitions'])
                result['recording_adjustments']=adjusted
                if adjusted:result['warning']+=' Na pobliskie przechodnie pola przesunięto waypointy: '+', '.join(str(r['waypoint']) for r in adjusted)+'.'
                for gap in result['gaps']:
                    if gap['from'][2]!=gap['to'][2] and gap['reason']=='Dodaj jawne przejście między piętrami z menu mapy.':
                        gap['reason']='Nagranie nie potwierdza jednoznacznie czynności i lądowania dla tego odcinka.'
            result["recorded_actions"]=interactions[:128]
            result["unmatched_recorded_actions"]=sum(r['matched_transition'] is None for r in interactions)
            if result["avoid_tiles"]:
                result["warning"]+=" Trasa obejmuje pola oznaczone jako omijane; mogą mieć efekty terenowe."
            if recording is not None:
                observed=isinstance(recording.get("observed_path"),list)
                result["recording_source"]=recording.get("position_source","commands") if observed else "commands"
                if not observed:
                    result["warning"]+=" Starsze nagranie zapisuje żądania ruchu, nie potwierdzony ślad postaci."
                    result["requires_action_review"]=True
                if recording.get("trace_limited") or any(p.get("break_before") for p in recording.get("observed_path",[])):
                    result["warning"]+=" Odczyt pozycji ma przerwy lub osiągnął limit zapisu; sprawdź przebieg."
                    result["requires_action_review"]=True
            result['requires_path_review']=bool(result.get('requires_action_review'))
            if result['unmatched_recorded_actions']:
                result["warning"]+=f" Nie przypisano {result['unmatched_recorded_actions']} czynności do potwierdzonych przejść; sprawdź je w szczegółach."
                result["requires_action_review"]=True
            return self._json_response(result)
        except (ValueError,TypeError,KeyError) as exc:return self._json_response({"error":str(exc)},400)
        except Exception:
            log.exception("Preview planning failed")
            return self._json_response({"error":"Nie udało się przygotować planu."},500)

    # ── actions ─────────────────────────────────────────────────────
    def _handle_toggle(self, name):
        enabled = self._read_body().get("enabled", True)
        log.info("Toggle request: %s -> enabled=%s", name, enabled)
        if self._not_started():
            log.warning("Toggle failed: the bot is not started")
            return None
        try:
            result = _submit("_async_toggle_action", name, enabled).result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            log.error("Toggle error: %s", exc)
            return self._json_response({"error": str(exc)}, 500)
        log.info("Toggle result: %s", result)
        if result.startswith("Error:"):
            return self._json_response({"error": result}, 409)
        self._ok(result)

    def _ok(self, message):
        """Answer 200 with a human-readable message."""
        self._json_response(dict(ok=True, message=message))

    def _handle_restart(self, name):
        if self._not_started():
            return None
        try:
            message = _submit("_async_restart_action", name).result(timeout=CALL_TIMEOUT)
        except Exception as exc:
            return self._json_response({"error": str(exc)}, 500)
        self._json_response({"ok": True, "message": message})

    def _handle_action_config(self, name):
        changes = self._read_body()
        if not changes:
            return self._json_response({"error": "empty body"}, 400)
        return self._run_control("_async_update_action_config", name, changes)

    def _handle_delete(self, name):
        script = ACTIONS_DIR / f"{name}.py"
        if not script.exists():
            return self._json_response({"error": f"action '{name}' not found"}, 404)
        if _main_loop:
            try:  # stop it first; the file goes either way
                _submit("_async_toggle_action", name, False).result(timeout=CALL_TIMEOUT)
            except Exception:
                pass
        try:
            script.unlink()
        except Exception as exc:
            return self._json_response({"error": str(exc)}, 500)
        log.info("Deleted action: %s", name)
        self._ok(f"Deleted {name}")

    # ── route recording ─────────────────────────────────────────────
    def _handle_cavebot_record_start(self) -> None:
        request = self._read_body()
        name = request.get("name", "").strip()
        if name == "":
            return self._json_response({"error": "name is required"}, 400)
        if self._not_started(need_state=True):
            return None
        from dbv.brain.cavebot import cavebot
        refusal = cavebot.start_recording(_state, name, mode=request.get("mode", "manual"))
        if refusal:
            return self._json_response({"error": refusal}, 400)
        self._ok(f"Recording '{name}' started")

    def _handle_cavebot_record_stop(self) -> None:
        if not _state:
            return self._json_response(NOT_STARTED, 503)
        discard = self._read_body().get("discard", False)
        from dbv.brain.cavebot import cavebot
        saved = cavebot.stop_recording(_state, discard=discard)
        if saved is None:
            message = "Recording discarded" if discard else "No recording in progress"
        else:
            message = f"Saved '{saved['name']}' ({len(saved.get('waypoints', []))} waypoints)"
        self._json_response({"ok": True, "message": message})

    def _recording_or_404(self, name):
        from dbv.brain.cavebot import cavebot
        found = cavebot.load_recording(name)
        if found is None:
            self._recording_missing(name)
        return found

    def _recording_missing(self, name):
        message = f"Recording '{name}' not found"
        self._json_response({"error": message}, 404)

    def _handle_list_recordings(self) -> None:
        from dbv.brain.cavebot import cavebot
        self._json_response({"recordings": cavebot.list_recordings()})

    def _handle_get_recording(self, name):
        found = self._recording_or_404(name)
        if found is not None:
            self._json_response(found)

    def _handle_get_actions_map(self, name):
        from dbv.brain.cavebot import cavebot
        found = self._recording_or_404(name)
        if found is None:
            return
        nodes = cavebot.build_actions_map(found)
        self._json_response({"name": name, "actions_map": nodes,
                             "text_preview": cavebot.actions_map_to_text(nodes), "node_count": len(nodes)})

    def _handle_remove_waypoints(self, name):
        indices = self._read_body().get("indices", [])
        if not isinstance(indices, list):  # a JSON array of waypoint numbers
            return self._json_response({"error": "indices must be a list"}, 400)
        from dbv.brain.cavebot import cavebot
        updated = cavebot.remove_waypoints(name, indices)
        if updated is None:
            return self._recording_missing(name)
        self._json_response({"waypoints": updated.get("waypoints", [])})

    def _handle_delete_recording(self, name):
        from dbv.brain.cavebot import cavebot
        if not cavebot.delete_recording(name):
            return self._recording_missing(name)
        self._ok(f"Deleted '{name}'")


# ── the state pushed to the panel ───────────────────────────────────

@lru_cache(maxsize=128)
def _action_description(path, mtime_ns, size):
    """Modification time and size invalidate earlier descriptions after an edit."""
    with open(path, encoding="utf-8") as action:
        for line in action:
            description = line.strip().strip('"').strip("'")
            if description:
                return description
    return ""


def _action_catalog(directory, internal):
    rows = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                name, extension = os.path.splitext(entry.name)
                if extension != ".py" or name == "__init__" or name in internal:
                    continue
                try:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                    description = _action_description(entry.path, stat.st_mtime_ns, stat.st_size)
                except (OSError, UnicodeError):
                    description = ""
                rows.append((name, description))
    except OSError:
        return []
    return sorted(rows)


def _knife_corpse_config(st, cfg):
    from dbv.brain.combat.hunter_knife import DEFAULT_MONSTERS, effective_config
    current = effective_config(cfg)
    suggestions = getattr(st, "knife_suggestions", None)
    return {"monsters": current["monsters"],
            "default_monsters": [{"name": m["name"], "corpses": [dict(c) for c in m["corpses"]]} for m in DEFAULT_MONSTERS],
            "ignored_suggestions": current["ignored_suggestions"],
            "suggestions": suggestions.pending(cfg) if suggestions else []}


def _live(task):
    return task is not None and not task.done()


def _actions_json(st, internal, tasks):
    configured = st.settings.get("actions", {})
    rows = []
    for name, description in _action_catalog(ACTIONS_DIR, internal):
        cfg = configured.get(name, {})
        running = _live(tasks.get(name))
        row = {"name": name, "enabled": cfg.get("enabled", False), "running": running,
               "description": description, "completed": name in st._action_completed}
        if name == "auto_potions" and running and cfg.get("defer_senzu_ki"):
            guard = st.potion_scheduler.deferral
            if guard and guard.phase != "normal":
                row["senzu_deferral"] = guard.phase
        extra = {key: value for key, value in cfg.items() if key != "enabled"}
        if extra:
            row["config"] = extra
        lines = list(st.action_logs.get(name, []))
        if lines:
            row["logs"] = lines
        rows.append(row)
    return rows


_PROXY_STEPS = (("listening", "_server", "_ts_listening"), ("client_connected", "client_writer", "_ts_client_connected"),
                ("server_connected", "server_writer", "_ts_server_connected"),
                ("xtea_captured", "xtea_keys", "_ts_xtea_captured"))


def _proxy_sequence(proxy, flowing):
    """How far the game proxy got, with seconds since it was created, for connection debugging."""
    created = getattr(proxy, "_ts_created", None) if proxy else None

    def since_created(attribute):
        at = getattr(proxy, attribute, None) if proxy else None
        return None if at is None or created is None else round(at - created, 2)

    sequence = {"proxy_created": proxy is not None}
    sequence.update((step, proxy is not None and getattr(proxy, field, None) is not None)
                    for step, field, _ in _PROXY_STEPS)
    sequence["logged_in"] = proxy is not None and getattr(proxy, "logged_in", False)
    sequence["packets_flowing"] = flowing
    times = {"proxy_created": 0 if created else None}
    times.update((step, since_created(stamp)) for step, _, stamp in _PROXY_STEPS)
    times["logged_in"] = since_created("_ts_logged_in")
    times["packets_flowing"] = 0 if flowing else None
    sequence["timestamps"] = times
    return sequence


def _player_json(gs):
    age = time.time() - gs.stats_updated_at if gs.stats_updated_at else -1
    return {"hp": gs.hp, "max_hp": gs.max_hp, "mana": gs.mana, "max_mana": gs.max_mana,
            "level": gs.level, "experience": gs.experience, "position": list(gs.position),
            "magic_level": gs.magic_level, "soul": gs.soul, "stats_age": round(age, 1)}


def _visible_floor(gs, creatures):
    """The player's floor; without a position, the floor most creatures are on (0 if unknown)."""
    floor = gs.position[2]
    if floor == 0 and gs.player_id in creatures:
        floor = creatures[gs.player_id].get("z", 0)
    if floor == 0:
        counts = Counter(c.get("z", 0) for c in creatures.values() if 1 <= c.get("z", 0) <= 15)
        if counts:
            floor = max(counts, key=counts.get)
    return floor


def _creatures_json(gs):
    creatures = dict(gs.creatures)
    floor = _visible_floor(gs, creatures)
    return [{"id": cid, "health": c.get("health", 0), "name": c.get("name", ""),
             "x": c.get("x", 0), "y": c.get("y", 0), "z": c.get("z", 0)}
            for cid, c in creatures.items() if floor == 0 or c.get("z") == floor]


def _observer_connected(gs):
    try:
        bridge = getattr(gs, "dll_bridge", None)
        return bridge.connected if bridge is not None else False
    except Exception:
        return False


def _cavebot_json(st):
    from dbv.brain.cavebot import cavebot as _cb
    active = st.recording_active
    steps = list(st.recording_waypoints) if active else []
    trace = getattr(st, "recording_observed_path", [])
    return {"recording": {
                "active": active,
                "name": st.recording_name,  # "" when nothing is recorded
                "mode": getattr(st, "recording_mode", "manual"),
                "hunt_progress": getattr(st, "recording_hunt_progress", None) if active else None,
                "waypoint_count": len(steps),
                "waypoints": steps[-20:],
                "position_count": len(trace) if active else 0,
                "observed_path": list(trace[-512:]) if active else [],
                "trace_limited": getattr(st, "recording_trace_limited", False) if active else False,
            },
            "recordings": _cb.list_recordings()}


def _hunter_knife_json(st, gs):
    knife = getattr(gs, "hunter_knife", None)
    return {
        "status": knife.panel_status() if knife else "Wyłączony",
        "processed": knife.done if knife else 0,
        "unattributed": getattr(knife, "unattributed", 0),
        "last_issue": getattr(knife, "last_issue", None),
        "unassigned_bodies": getattr(knife.queue, "unassigned_bodies", 0) if knife else 0,
        "last_timing_ms": knife.last_timing_ms if knife else None,
        "from_combat": knife.queue.from_combat if knife else 0,
        "personal_loot": getattr(gs, "knife_loot_count", 0),
        "unclassified_loot": getattr(gs, "knife_candidate_count", 0),
        "denied": getattr(gs, "knife_denials", 0),
        **_knife_corpse_config(st, st.settings.get("actions", {}).get("auto_hunter_knife")),
    }


def _snapshot_of(owner, name):
    part = getattr(owner, name, None)
    return part.snapshot() if part else None


def _build_state_json() -> str:
    """The whole state for the panel, as JSON. Reads only; safe from any thread."""
    st = _state  # read once: start_api may replace it from another thread
    if not st:
        return json.dumps(dict(connected=False, actions=[]))
    internal = getattr(sys.modules.get("__main__"), "INTERNAL_ACTIONS", {"dll_bridge"})
    tasks = dict(st._action_tasks)
    proxy = st.game_proxy
    from_server = proxy.packets_from_server if proxy else 0
    from_client = proxy.packets_from_client if proxy else 0
    gs = st.game_state
    observer = _observer_connected(gs)
    return json.dumps(obj={
        "connected": st.connected,  # a character is in game
        "session_kills": getattr(gs, "session_kills", 0),
        "login_startup": {"phase": st.login_startup.phase} if getattr(st, "login_startup", None) else None,
        "pause_hotkey": _snapshot_of(st, "pause_hotkey"),
        "profile_selection": {"combat":st.settings.get("selected_combat_profile"),"bot":st.settings.get("selected_bot_profile"),"routes":st.settings.get("route_combat_profiles",{})},
        "alerts": _snapshot_of(st, "alert_center"),
        "passive_map": st.passive_map_monitor.snapshot() if getattr(st, "passive_map_monitor", None) else {
            "status": "waiting", "message": "Czekam na uruchomienie pasywnego podglądu.", "cells": [], "passive": True, "stale": True},
        "automation_paused": st.automation_paused,
        "automation_pause_kind": st.automation_pause_kind,
        "potion_diagnostics": st.potion_diagnostics.snapshot(),
        "item_identification": _snapshot_of(st, "item_identification"),
        "senzu_usage": _snapshot_of(gs, "senzu_usage"),
        "hunter_knife": _hunter_knife_json(st, gs),
        "barrier_band": st.band_scheduler.snapshot(),
        "barrier_spell": {"status": st.barrier_scheduler.status} if getattr(st, "barrier_scheduler", None) else None,
        "speed_up": _snapshot_of(st, "speed_scheduler"),
        "commands": proxy.command_snapshot() if proxy and hasattr(proxy, "command_snapshot") else None,
        "actions": _actions_json(st, internal, tasks),
        "packets_from_server": from_server,
        "packets_from_client": from_client,
        "player": _player_json(gs),
        "packet_observer": _snapshot_of(st, "packet_observer"),
        "observation_families": observation_family_status(st),
        "creatures": _creatures_json(gs),
        "dll_injected": observer,
        "dll_bridge_connected": _live(tasks.get("dll_bridge")) and observer,
        "cavebot": _cavebot_json(st),
        "combat_profile": validate_combat(st.settings.get("combat_profile")),
        "adaptive_cavebot": {**(st.adaptive_cavebot.snapshot() if getattr(st,"adaptive_cavebot",None) else {"status":"idle","reason":"Wybierz zapisany szkic."}),"preparing":getattr(st,"_adaptive_prepare_token",None) is not None},
        "proxy_sequence": _proxy_sequence(proxy, from_server > 0 or from_client > 0),
    })


# ── WebSocket push ──────────────────────────────────────────────────

_ws_clients: set[object] = set()   # authenticated panel connections


async def _ws_handler(websocket) -> None:
    """One panel connection: authenticate, then only receive pushes."""
    # No state is sent or broadcast until the first message authenticates.
    try:
        credentials = json.loads(await asyncio.wait_for(
            websocket.recv(), timeout=WS_AUTH_TIMEOUT
        ))
        if not isinstance(credentials, dict) or not valid_token(credentials.get("token")):
            await websocket.close(code=1008, reason="authentication required")
            return
    except Exception:
        await websocket.close(code=1008, reason="authentication required")
        return
    clients = _ws_clients
    clients.add(websocket)
    log.info("WS client connected (%d total)", len(clients))
    try:
        await websocket.send(_build_state_json())  # the first state without waiting for a push
        async for _message in websocket:
            pass  # the panel sends nothing after its token; this waits for it to leave
    except Exception:
        pass
    finally:
        clients.discard(websocket)
        log.info("WS client disconnected (%d total)", len(_ws_clients))


async def _push_to(clients, message):
    """Send to each client; returns the ones whose connection failed."""
    failed = []
    for client in clients:
        try:
            await client.send(message)
        except Exception:
            failed.append(client)
    return failed


async def _ws_push_loop() -> None:
    """Push the state to every panel connection, forever; dead connections are dropped."""
    clients = _ws_clients
    for _ in itertools.count():
        if clients:
            clients.difference_update(await _push_to(list(clients), _build_state_json()))
        await asyncio.sleep(delay=WS_PUSH_INTERVAL)


async def _create_ws_server():
    import websockets
    return await websockets.serve(
        _ws_handler, "127.0.0.1", WS_PORT,
        origins=[DASHBOARD_ORIGIN], max_size=4096, max_queue=4,
    )


async def _start_ws_server() -> None:
    try:
        server = await _create_ws_server()
    except ImportError:
        return log.warning("websockets not installed — WS push disabled (pip install websockets)")
    except OSError as exc:
        return log.error("WebSocket server failed to bind port %s: %s", WS_PORT, exc)
    except Exception as exc:
        return log.error("WebSocket server failed: %s", exc)
    log.info("WebSocket server listening on ws://127.0.0.1:%s", WS_PORT)
    pusher = asyncio.create_task(_ws_push_loop())
    _background_tasks.add(pusher)   # keep a reference so the task is not collected
    await server.wait_closed()  # serves until the process ends


def _start_game_monitors(loop, bot_state):
    if getattr(bot_state, "passive_map_monitor", None) is None:
        from dbv.brain.navigation.passive_map import PassiveMapMonitor
        bot_state.passive_map_monitor = PassiveMapMonitor(bot_state)
        bot_state.passive_map_monitor.start()

    if not getattr(bot_state,"pause_hotkey",None):
        from dbv.mcp_server.pause_hotkey import PauseHotkey
        bot_state.pause_hotkey = PauseHotkey(
            loop, lambda: getattr(sys.modules["__main__"], "_async_toggle_all")(),
            indicator_active=lambda: (bot_state.connected and bot_state.automation_paused
                                      and bot_state.automation_pause_kind == "manual"),
        )
        bot_state.pause_hotkey.start()

    if not getattr(bot_state, "_alert_monitor_future", None):
        from dbv.brain.state.game_alerts import monitor_alerts
        bot_state._alert_monitor_future = asyncio.run_coroutine_threadsafe(monitor_alerts(bot_state), loop)


def start_api(loop: asyncio.AbstractEventLoop, bot_state, *, panel_only=False) -> bool:
    """Serve the local panel. Game monitors belong to the connected session."""
    global _main_loop, _state, _started  # the handlers read these from their own thread
    _main_loop, _state = loop, bot_state
    if not panel_only:
        _start_game_monitors(loop, bot_state)
    if not _started:
        _started = True  # before binding, so a second call cannot bind twice
        try:
            server = HTTPServer(("127.0.0.1", API_PORT), _Handler)  # loopback only
        except OSError as exc:
            log.error("Failed to start HTTP server on port %s: %s", API_PORT, exc)
            _started = False  # a later call may try the port again
            return False
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("Dashboard API listening on http://127.0.0.1:%s", API_PORT)
        asyncio.run_coroutine_threadsafe(coro=_start_ws_server(), loop=loop)
    return True
