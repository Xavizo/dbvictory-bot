"""
Dashboard API — HTTP for actions + WebSocket for real-time state push.

HTTP runs in a daemon thread.  WebSocket runs in the main asyncio loop
and pushes game state to all connected dashboards every 100ms.
"""

import asyncio
import json
import logging
import sqlite3
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs
from dashboard_security import DASHBOARD_ORIGIN, allowed_origin, valid_token
from cavebot_combat import validate_combat
from map_atlas import database_busy

log = logging.getLogger("dashboard_api")

# References set by start_api()
_main_loop: asyncio.AbstractEventLoop | None = None
_state = None  # will be set to the BotState instance
ACTIONS_DIR = Path(__file__).parent / "actions"

API_PORT = 8089
WS_PORT = 8090
WS_PUSH_INTERVAL = 0.1  # seconds between WebSocket pushes
WS_AUTH_TIMEOUT = 5


class _Handler(BaseHTTPRequestHandler):
    """Minimal REST handler — no frameworks needed."""

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    # ── CORS ───────────────────────────────────────────────────────
    def _cors_headers(self):
        if allowed_origin(self.headers.get("Origin")):
            self.send_header("Access-Control-Allow-Origin", DASHBOARD_ORIGIN)
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

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

    def do_OPTIONS(self):
        if not self._authorize(preflight=True):
            return
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Helpers ────────────────────────────────────────────────────
    def _json_response(self, data: dict, status: int = 200):
        # HTTP/1.0 closes after the response. On Windows, closing with unread
        # request data can reset TCP before the peer receives our 4xx. Drain
        # only small rejected bodies, with a strict time bound, never parse them.
        if status>=400 and not getattr(self,"_body_read",False):
            previous_timeout=self.connection.gettimeout()
            try:
                length=int(self.headers.get("Content-Length",0))
                if 0<length<=16384:
                    self.connection.settimeout(.2)
                    self.rfile.read(length)
                    self._body_read=True
            except (OSError,ValueError):pass
            finally:self.connection.settimeout(previous_timeout)
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length",str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body=self.rfile.read(length)
        self._body_read=True
        return json.loads(body)

    # ── Routes ─────────────────────────────────────────────────────
    def do_GET(self):
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
        if self.path == "/api/state":
            return self._handle_get_state()
        if urlsplit(self.path).path == "/api/cavebot/reports":
            from cavebot_history import read_reports
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
        if self.path == "/api/recordings":
            return self._handle_list_recordings()
        if urlsplit(self.path).path == "/api/map/atlas":
            return self._handle_atlas()
        if self.path == "/api/map/grounds":
            return self._hunt_areas()
        if urlsplit(self.path).path == "/api/map/world":
            import sqlite3
            import zlib
            from world_minimap import client_minimap, world_floor
            query = parse_qs(urlsplit(self.path).query)
            try:
                floor = int(query.get('z', ['7'])[0])
                if not 0 <= floor <= 15:
                    raise ValueError()
            except ValueError:
                return self._json_response({'error': 'Nieprawidłowe piętro.'}, 400)
            atlas = getattr(getattr(_state, 'passive_map_monitor', None), 'atlas', None)
            try:
                result = world_floor(client_minimap(), getattr(atlas, 'path', None), floor)
            except (KeyError, FileNotFoundError):
                return self._json_response({'error': 'Brak zapisanej minimapy DBVictory.'}, 404)
            except (OSError, ValueError, sqlite3.Error, zlib.error):
                return self._json_response({'error': 'Nie można odczytać zapisanej mapy. Spróbuj ponownie.'}, 503)
            return self._json_response(result)
        if self.path == "/api/map/drafts":
            return self._handle_map_drafts()
        # GET /api/cavebot/actions_map/<name>
        parts = self.path.strip("/").split("/")
        if (len(parts) == 4 and parts[0] == "api" and parts[1] == "cavebot"
                and parts[2] == "actions_map"):
            return self._handle_get_actions_map(parts[3])
        # GET /api/recordings/<name>
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "recordings":
            return self._handle_get_recording(unquote(parts[2]))
        self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        if not self._authorize():
            return
        if self.path == "/api/map/grounds":
            return self._hunt_areas(save=True)
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
                if not isinstance(body, dict) or body.get("command") not in {"configure", "acknowledge"}:
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
        if self.path == "/api/map/pet":
            try:
                from transition_learning import position
                if not 0 < int(self.headers.get("Content-Length", 0)) <= 1024:
                    raise ValueError("Nieprawidłowy rozmiar oznaczenia.")
                body = self._read_body()
                if (not isinstance(body, dict) or set(body) != {"position", "ref", "name"}
                        or not position(body["position"]) or not isinstance(body["ref"], str)):
                    raise ValueError("Wybierz jedno stworzenie na mapie na żywo.")
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
        # POST /api/actions/{name}/toggle
        # POST /api/actions/{name}/restart
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "actions":
            name = parts[2]
            if not all(c.isalnum() or c == '_' for c in name):
                self._json_response({"error": "invalid action name"}, 400)
                return
            verb = parts[3]
            if verb == "toggle":
                return self._handle_toggle(name)
            if verb == "restart":
                return self._handle_restart(name)
            if verb == "config":
                return self._handle_action_config(name)

        # POST /api/recordings/{name}/remove_waypoints
        if (len(parts) == 4 and parts[0] == "api" and parts[1] == "recordings"
                and parts[3] == "remove_waypoints"):
            return self._handle_remove_waypoints(unquote(parts[2]))

        # Cavebot endpoints
        if self.path == "/api/cavebot/record/start":
            return self._handle_cavebot_record_start()
        if self.path == "/api/cavebot/record/stop":
            return self._handle_cavebot_record_stop()
        if self.path == "/api/cavebot/play":
            return self._handle_cavebot_play()
        if self.path == "/api/cavebot/play/stop":
            return self._handle_cavebot_play_stop()
        if self.path == "/api/cavebot/logs/spacer":
            return self._handle_cavebot_logs_spacer()

        self._json_response({"error": "not found"}, 404)

    def do_DELETE(self):
        if not self._authorize():
            return
        parts = self.path.strip("/").split("/")
        # DELETE /api/actions/{name}
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "actions":
            name = parts[2]
            if not all(c.isalnum() or c == '_' for c in name):
                self._json_response({"error": "invalid action name"}, 400)
                return
            return self._handle_delete(name)
        # DELETE /api/recordings/{name}
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "recordings":
            name = unquote(parts[2])
            return self._handle_delete_recording(name)
        self._json_response({"error": "not found"}, 404)

    def _run_control(self, method, *args):
        if _main_loop is None or _state is None:
            return self._json_response({"error": "bot not started"}, 503)
        future = asyncio.run_coroutine_threadsafe(getattr(sys.modules["__main__"], method)(*args), _main_loop)
        try:
            result = future.result(timeout=5)
            self._json_response({"ok": True, "message": result})
        except ValueError as exc:
            self._json_response({"error": str(exc)}, 400)
        except Exception as exc:
            future.cancel()
            self._json_response({"error": str(exc)}, 500)

    # ── GET /api/state ─────────────────────────────────────────────
    def _handle_get_state(self):
        body = _build_state_json().encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _hunt_areas(self, save=False):
        import sqlite3
        from hunt_areas import catalogue, save_area
        atlas = getattr(getattr(_state, 'passive_map_monitor', None), 'atlas', None)
        if atlas is None:
            return self._json_response({'error':'Atlas czeka na pierwszy odczyt.'},503)
        try:
            if save:
                if not 0 < int(self.headers.get('Content-Length',0)) <= 2*1024*1024:
                    raise ValueError('Nieprawidłowy rozmiar danych obszaru.')
                return self._json_response({'id':save_area(atlas,self._read_body())})
            return self._json_response(catalogue(atlas))
        except (ValueError, TypeError) as exc:
            return self._json_response({'error':str(exc)},400)
        except (OSError, sqlite3.Error):
            return self._json_response({'error':'Atlas jest chwilowo niedostępny.'},503)

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
                if body.get('operation') != 'set_tags' and controller and controller.status in {'running','paused','ready'} and getattr(controller,'draft_id',None)==body.get('id'):
                    raise ValueError('Zatrzymaj V2 przed zmianą lub usunięciem wykonywanej trasy.')
                if body.get('operation') == 'set_tags':
                    atlas.set_tags(body.get('id'), body.get('tags'), body.get('previous'))
                elif body.get('operation') in {'delete','restore','configure','set_loop'}:
                    atlas.change_draft(body.get('id'),body['operation'],body.get('combat'),body.get('loop'))
                elif body.get('operation') is not None:raise ValueError('Nieznana operacja szkicu.')
                else:draft_id=atlas.save_draft(body.get("name"),body.get("points"),body.get("id"),body.get("loop",True),body.get('expected_updated'),body.get('labels'),body.get('links'),body.get('tags'))
            return self._json_response({"scope":atlas.scope,"drafts":atlas.drafts(),"trash":atlas.drafts(deleted=True),"saved_id":draft_id})
        except (BlockingIOError, sqlite3.OperationalError) as exc:
            if isinstance(exc, BlockingIOError) or database_busy(exc):
                return self._json_response({"error":"Baza atlasu jest chwilowo zajęta. Spróbuj ponownie."},503)
            return self._json_response({"error":"Nie udało się odczytać lub zapisać szkiców."},500)
        except (ValueError,TypeError,KeyError) as exc:return self._json_response({"error":str(exc)},400)
        except Exception:return self._json_response({"error":"Nie udało się odczytać lub zapisać szkiców."},500)

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
                import cavebot
                name=body["recording"]
                if name not in {r["name"] for r in cavebot.list_recordings()}:raise ValueError("Nie ma takiego nagrania.")
                recording=cavebot.load_recording(name)
                optimize=body.get('optimize',False)
                if type(optimize) is not bool:raise ValueError('Nieprawidłowa opcja optymalizacji.')
                if recording and recording.get("recording_mode") == "adaptive" and body.get("full_trace") is not True:
                    from adaptive_recording import hunting_excerpt
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
            loop=body.get("loop",False)
            if type(loop)is not bool:raise ValueError("Nieprawidłowa opcja pętli.")
            result=atlas.plan(points,observations,original,loop=loop,links=links)
            if hunt is not None:
                from adaptive_recording import sector, stays_near_trace
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
                from map_atlas import recording_points
                from map_atlas import validate_labels,remap_links
                labels=validate_labels(body.get('labels'),len(points))
                if not result['complete']:result['optimization_blocked']=True
                else:
                    optimized,original=recording_points({'observed_path':[{'pos':p} for p in result['path'][:result['return_start']+1]]},observations,optimize=True,keep_positions=[p for p,label in zip(points,labels) if label]+[p for link in result['links'] for p in points[link['from_index']:link['from_index']+2]])
                    result=atlas.plan(optimized,observations,original,loop=loop,links=remap_links(result['links'],points,optimized))
            if recording is not None:
                from map_atlas import review_recorded_actions
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

    # ── POST /api/actions/{name}/toggle ────────────────────────────
    def _handle_toggle(self, name: str):
        body = self._read_body()
        enabled = body.get("enabled", True)
        log.info(f"Toggle request: {name} -> enabled={enabled}")

        if _main_loop is None:
            log.warning("Toggle failed: _main_loop is None")
            self._json_response({"error": "bot not started"}, 503)
            return

        # Access the real __main__ module (mcp_server runs as __main__)
        main_mod = sys.modules["__main__"]
        future = asyncio.run_coroutine_threadsafe(
            main_mod._async_toggle_action(name, enabled), _main_loop
        )
        try:
            result = future.result(timeout=5)
            log.info(f"Toggle result: {result}")
            if result.startswith("Error:"):
                return self._json_response({"error": result}, 409)
            self._json_response({"ok": True, "message": result})
        except Exception as e:
            log.error(f"Toggle error: {e}")
            self._json_response({"error": str(e)}, 500)

    # ── POST /api/actions/{name}/restart ───────────────────────────
    def _handle_restart(self, name: str):
        if _main_loop is None:
            self._json_response({"error": "bot not started"}, 503)
            return

        main_mod = sys.modules["__main__"]
        future = asyncio.run_coroutine_threadsafe(
            main_mod._async_restart_action(name), _main_loop
        )
        try:
            result = future.result(timeout=5)
            self._json_response({"ok": True, "message": result})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)


    # ── POST /api/actions/{name}/config ──────────────────────────
    def _handle_action_config(self, name: str):
        body = self._read_body()
        if not body:
            self._json_response({"error": "empty body"}, 400)
            return
        return self._run_control("_async_update_action_config", name, body)

    # ── DELETE /api/actions/{name} ───────────────────────────────
    def _handle_delete(self, name: str):
        action_file = ACTIONS_DIR / f"{name}.py"
        if not action_file.exists():
            self._json_response({"error": f"action '{name}' not found"}, 404)
            return

        # Disable first if running
        if _main_loop is not None:
            main_mod = sys.modules["__main__"]
            try:
                future = asyncio.run_coroutine_threadsafe(
                    main_mod._async_toggle_action(name, False), _main_loop
                )
                future.result(timeout=5)
            except Exception:
                pass

        # Delete the file
        try:
            action_file.unlink()
            log.info(f"Deleted action: {name}")
            self._json_response({"ok": True, "message": f"Deleted {name}"})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    # ── Cavebot endpoints ─────────────────────────────────────────

    def _handle_cavebot_record_start(self):
        body = self._read_body()
        name = body.get("name", "").strip()
        if not name:
            self._json_response({"error": "name is required"}, 400)
            return
        if _main_loop is None or _state is None:
            self._json_response({"error": "bot not started"}, 503)
            return
        import cavebot
        err = cavebot.start_recording(_state, name, mode=body.get("mode", "manual"))
        if err:
            self._json_response({"error": err}, 400)
        else:
            self._json_response({"ok": True, "message": f"Recording '{name}' started"})

    def _handle_cavebot_record_stop(self):
        if _state is None:
            self._json_response({"error": "bot not started"}, 503)
            return
        body = self._read_body()
        discard = body.get("discard", False)
        import cavebot
        rec = cavebot.stop_recording(_state, discard=discard)
        if rec is None:
            msg = "Recording discarded" if discard else "No recording in progress"
            self._json_response({"ok": True, "message": msg})
        else:
            count = len(rec.get("waypoints", []))
            self._json_response({"ok": True, "message": f"Saved '{rec['name']}' ({count} waypoints)"})

    def _handle_cavebot_play(self):
        body = self._read_body()
        name = body.get("name", "").strip()
        loop = body.get("loop", False)
        if not name:
            self._json_response({"error": "name is required"}, 400)
            return
        if _main_loop is None or _state is None:
            self._json_response({"error": "bot not started"}, 503)
            return

        main_mod = sys.modules["__main__"]
        future = asyncio.run_coroutine_threadsafe(
            main_mod._async_play_recording(name, loop), _main_loop
        )
        try:
            result = future.result(timeout=5)
            if not result.startswith("Playing recording"):
                return self._json_response({"error": result}, 409)
            self._json_response({"ok": True, "message": result})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_cavebot_play_stop(self):
        if _main_loop is None or _state is None:
            self._json_response({"error": "bot not started"}, 503)
            return

        main_mod = sys.modules["__main__"]
        future = asyncio.run_coroutine_threadsafe(
            main_mod._async_stop_playback(), _main_loop
        )
        try:
            result = future.result(timeout=5)
            self._json_response({"ok": True, "message": result})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_cavebot_logs_spacer(self):
        if _state is None:
            self._json_response({"error": "bot not started"}, 503)
            return
        if "cavebot" not in _state.action_logs:
            import collections
            _state.action_logs["cavebot"] = collections.deque(maxlen=500)
        for _ in range(20):
            _state.action_logs["cavebot"].append("")
        self._json_response({"ok": True, "message": "Spacer added"})

    def _handle_list_recordings(self):
        import cavebot
        recs = cavebot.list_recordings()
        self._json_response({"recordings": recs})

    def _handle_get_recording(self, name: str):
        import cavebot
        rec = cavebot.load_recording(name)
        if rec is None:
            self._json_response({"error": f"Recording '{name}' not found"}, 404)
        else:
            self._json_response(rec)

    def _handle_get_actions_map(self, name: str):
        import cavebot
        rec = cavebot.load_recording(name)
        if rec is None:
            self._json_response({"error": f"Recording '{name}' not found"}, 404)
            return
        actions_map = cavebot.build_actions_map(rec)
        text_preview = cavebot.actions_map_to_text(actions_map)
        self._json_response({
            "name": name,
            "actions_map": actions_map,
            "text_preview": text_preview,
            "node_count": len(actions_map),
        })

    def _handle_remove_waypoints(self, name: str):
        body = self._read_body()
        indices = body.get("indices", [])
        if not isinstance(indices, list):
            self._json_response({"error": "indices must be a list"}, 400)
            return
        import cavebot
        rec = cavebot.remove_waypoints(name, indices)
        if rec is None:
            self._json_response({"error": f"Recording '{name}' not found"}, 404)
        else:
            self._json_response({"waypoints": rec.get("waypoints", [])})

    def _handle_delete_recording(self, name: str):
        import cavebot
        if cavebot.delete_recording(name):
            self._json_response({"ok": True, "message": f"Deleted '{name}'"})
        else:
            self._json_response({"error": f"Recording '{name}' not found"}, 404)


_started = False


# ── Playback stats helper ─────────────────────────────────────────

def _build_playback_stats(st, gs) -> dict:
    """Compute hunting session statistics for the dashboard."""
    elapsed = time.time() - st.playback_start_time if st.playback_start_time else 0
    elapsed_hours = elapsed / 3600 if elapsed > 0 else 0
    xp_gained = gs.experience - st.playback_start_experience if st.playback_start_experience is not None else 0
    xp_per_hour = int(xp_gained / elapsed_hours) if elapsed_hours > 0 else 0
    senzu_per_hour = round(st.playback_senzu_used / elapsed_hours, 1) if elapsed_hours > 0 else 0

    # Predicted level in 1 hour: linear extrapolation from current XP rate
    predicted_level = gs.level
    if xp_per_hour > 0 and gs.experience > 0:
        predicted_xp = gs.experience + xp_per_hour
        from farming_telemetry import experience_for_level
        lvl = gs.level
        while True:
            next_lvl = lvl + 1
            xp_needed = experience_for_level(next_lvl)
            if xp_needed > predicted_xp:
                break
            lvl = next_lvl
            if lvl > gs.level + 100:  # safety cap
                break
        predicted_level = lvl

    # Sample senzu_per_hour into time-series (every 60s)
    now = time.time()
    if (st.playback_start_time
            and elapsed > 10
            and now - st._last_senzu_sample_time >= 60):
        st.playback_senzu_series.append([int(elapsed), senzu_per_hour])
        # Cap series to ~24h of 60s samples (1440 entries)
        if len(st.playback_senzu_series) > 1440:
            st.playback_senzu_series = st.playback_senzu_series[-1440:]
        st._last_senzu_sample_time = now

    return {
        "loop_count": st.playback_loop_count,
        "kills": gs.session_kills,
        "senzu_used": st.playback_senzu_used,
        "xp_gained": xp_gained,
        "xp_per_hour": xp_per_hour,
        "predicted_level_1h": predicted_level,
        "session_seconds": int(elapsed),
        "senzu_per_hour": senzu_per_hour,
        "senzu_series": st.playback_senzu_series,
        "segment_stats": dict(getattr(st, "segment_stats", {})),
    }


# ── Shared state builder (used by HTTP and WebSocket) ──────────────

def _build_state_json() -> str:
    """Build the full state JSON string. Thread-safe read of global state."""
    st = _state
    if st is None:
        return json.dumps({"connected": False, "actions": []})

    main_mod = sys.modules.get("__main__")
    INTERNAL_ACTIONS = getattr(main_mod, "INTERNAL_ACTIONS", {"dll_bridge"})
    actions_settings = st.settings.get("actions", {})
    action_names = sorted(
        p.stem for p in ACTIONS_DIR.glob("*.py")
        if p.stem != "__init__" and p.stem not in INTERNAL_ACTIONS
    ) if ACTIONS_DIR.exists() else []

    action_tasks = dict(st._action_tasks)
    actions = []
    for name in action_names:
        cfg = actions_settings.get(name, {})
        enabled = cfg.get("enabled", False)
        task = action_tasks.get(name)
        running = task is not None and not task.done()
        desc = ""
        try:
            src = (ACTIONS_DIR / f"{name}.py").read_text(encoding="utf-8")
            for line in src.splitlines():
                s = line.strip().strip('"').strip("'")
                if s:
                    desc = s
                    break
        except OSError:
            pass
        logs = list(st.action_logs.get(name, []))
        # Expose non-standard config keys (everything except "enabled")
        action_config = {k: v for k, v in cfg.items() if k != "enabled"}
        entry = {
            "name": name, "enabled": enabled,
            "running": running, "description": desc,
            "completed": name in st._action_completed,
        }
        if action_config:
            entry["config"] = action_config
        if logs:
            entry["logs"] = logs
        actions.append(entry)

    pkt_server = st.game_proxy.packets_from_server if st.game_proxy else 0
    pkt_client = st.game_proxy.packets_from_client if st.game_proxy else 0

    # Proxy connection sequence (for debugging)
    gp = st.game_proxy
    t0 = getattr(gp, '_ts_created', None) if gp else None

    def _rel(ts):
        """Return seconds relative to proxy creation, or None."""
        if ts is None or t0 is None:
            return None
        return round(ts - t0, 2)

    proxy_sequence = {
        "proxy_created": gp is not None,
        "listening": gp is not None and getattr(gp, '_server', None) is not None,
        "client_connected": gp is not None and getattr(gp, 'client_writer', None) is not None,
        "server_connected": gp is not None and getattr(gp, 'server_writer', None) is not None,
        "xtea_captured": gp is not None and getattr(gp, 'xtea_keys', None) is not None,
        "logged_in": gp is not None and getattr(gp, 'logged_in', False),
        "packets_flowing": pkt_server > 0 or pkt_client > 0,
        "timestamps": {
            "proxy_created": 0 if t0 else None,
            "listening": _rel(getattr(gp, '_ts_listening', None)) if gp else None,
            "client_connected": _rel(getattr(gp, '_ts_client_connected', None)) if gp else None,
            "server_connected": _rel(getattr(gp, '_ts_server_connected', None)) if gp else None,
            "xtea_captured": _rel(getattr(gp, '_ts_xtea_captured', None)) if gp else None,
            "logged_in": _rel(getattr(gp, '_ts_logged_in', None)) if gp else None,
            "packets_flowing": 0 if (pkt_server > 0 or pkt_client > 0) else None,
        },
    }

    gs = st.game_state
    stats_age = time.time() - gs.stats_updated_at if gs.stats_updated_at else -1
    player = {
        "hp": gs.hp, "max_hp": gs.max_hp,
        "mana": gs.mana, "max_mana": gs.max_mana,
        "level": gs.level, "experience": gs.experience,
        "position": list(gs.position),
        "magic_level": gs.magic_level, "soul": gs.soul,
        "stats_age": round(stats_age, 1),
    }

    creatures_snapshot = dict(gs.creatures)

    player_z = gs.position[2] if gs.position[2] != 0 else 0
    if player_z == 0 and gs.player_id in creatures_snapshot:
        player_z = creatures_snapshot[gs.player_id].get("z", 0)
    if player_z == 0:
        z_counts: dict[int, int] = {}
        for info in creatures_snapshot.values():
            cz = info.get("z", 0)
            if 1 <= cz <= 15:
                z_counts[cz] = z_counts.get(cz, 0) + 1
        if z_counts:
            player_z = max(z_counts, key=z_counts.get)

    creatures = [
        {"id": cid, "health": info.get("health", 0), "name": info.get("name", ""),
         "x": info.get("x", 0), "y": info.get("y", 0), "z": info.get("z", 0)}
        for cid, info in creatures_snapshot.items()
        if player_z == 0 or info.get("z") == player_z
    ]

    # DLL status
    dll_bridge_task = action_tasks.get("dll_bridge")
    dll_bridge_running = dll_bridge_task is not None and not dll_bridge_task.done()
    dll_injected = False
    try:
        bridge = getattr(st.game_state, "dll_bridge", None)
        if bridge is not None:
            dll_injected = bridge.connected
    except Exception:
        pass

    # Cavebot state
    import cavebot as _cb
    rec_waypoints = list(st.recording_waypoints) if st.recording_active else []
    cavebot_logs = list(st.action_logs.get("cavebot", []))
    cavebot_state = {
        "recording": {
            "active": st.recording_active,
            "name": st.recording_name,
            "mode": getattr(st, "recording_mode", "manual"),
            "hunt_progress": getattr(st, "recording_hunt_progress", None) if st.recording_active else None,
            "waypoint_count": len(rec_waypoints),
            "waypoints": rec_waypoints[-20:],
            "position_count": len(getattr(st,"recording_observed_path",[])) if st.recording_active else 0,
            "observed_path": list(getattr(st,"recording_observed_path",[])[-512:]) if st.recording_active else [],
            "trace_limited": getattr(st,"recording_trace_limited",False) if st.recording_active else False,
        },
        "playback": {
            "active": st.playback_active,
            "recording_name": st.playback_recording_name,
            "index": st.playback_index,
            "total": st.playback_total,
            "loop": st.playback_loop,
            "logs": cavebot_logs[-100:],
            "minimap": st.playback_minimap,
            "actions_map_count": len(st.playback_actions_map),
            "stats": _build_playback_stats(st, gs),
        },
        "recordings": _cb.list_recordings(),
    }

    return json.dumps({
        "connected": st.connected,
        "pause_hotkey": st.pause_hotkey.snapshot() if getattr(st,"pause_hotkey",None) else None,
        "profile_selection": {"combat":st.settings.get("selected_combat_profile"),"bot":st.settings.get("selected_bot_profile"),"routes":st.settings.get("route_combat_profiles",{})},
        "alerts": st.alert_center.snapshot() if getattr(st, "alert_center", None) else None,
        "passive_map": st.passive_map_monitor.snapshot() if getattr(st, "passive_map_monitor", None) else {
            "status": "waiting", "message": "Czekam na uruchomienie pasywnego podglądu.", "cells": [], "passive": True, "stale": True},
        "automation_paused": st.automation_paused,
        "automation_pause_kind": st.automation_pause_kind,
        "potion_diagnostics": st.potion_diagnostics.snapshot(),
        "senzu_usage": gs.senzu_usage.snapshot() if getattr(gs, "senzu_usage", None) else None,
        "barrier_band": st.band_scheduler.snapshot(),
        "speed_up": st.speed_scheduler.snapshot() if getattr(st,"speed_scheduler",None) else None,
        "commands": st.game_proxy.command_snapshot() if st.game_proxy and hasattr(st.game_proxy,'command_snapshot') else None,
        "actions": actions,
        "packets_from_server": pkt_server,
        "packets_from_client": pkt_client,
        "player": player,
        "packet_observer": st.packet_observer.snapshot() if getattr(st,"packet_observer",None) else None,
        "creatures": creatures,
        "dll_injected": dll_injected,
        "dll_bridge_connected": dll_bridge_running and dll_injected,
        "cavebot": cavebot_state,
        "combat_profile": validate_combat(st.settings.get("combat_profile")),
        "adaptive_cavebot": {**(st.adaptive_cavebot.snapshot() if getattr(st,"adaptive_cavebot",None) else {"status":"idle","reason":"Wybierz zapisany szkic."}),"preparing":getattr(st,"_adaptive_prepare_token",None) is not None},
        "proxy_sequence": proxy_sequence,
    })


# ── WebSocket push server ──────────────────────────────────────────

_ws_clients: set = set()


async def _ws_handler(websocket):
    """Handle a single WebSocket client connection."""
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
    _ws_clients.add(websocket)
    log.info(f"WS client connected ({len(_ws_clients)} total)")
    try:
        # Send initial state immediately
        await websocket.send(_build_state_json())
        # Keep connection alive — just wait for disconnect
        async for _ in websocket:
            pass  # ignore any messages from client
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)
        log.info(f"WS client disconnected ({len(_ws_clients)} total)")


async def _ws_push_loop():
    """Push state to all connected WebSocket clients every WS_PUSH_INTERVAL."""
    while True:
        if _ws_clients:
            msg = _build_state_json()
            dead = []
            for ws in list(_ws_clients):
                try:
                    await ws.send(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.discard(ws)
        await asyncio.sleep(WS_PUSH_INTERVAL)


async def _create_ws_server():
    import websockets
    return await websockets.serve(
        _ws_handler, "127.0.0.1", WS_PORT,
        origins=[DASHBOARD_ORIGIN], max_size=4096, max_queue=4,
    )


async def _start_ws_server():
    """Start the WebSocket server on the asyncio event loop."""
    try:
        server = await _create_ws_server()
        log.info(f"WebSocket server listening on ws://127.0.0.1:{WS_PORT}")
        asyncio.create_task(_ws_push_loop())
        await server.wait_closed()
    except ImportError:
        log.warning("websockets not installed — WS push disabled (pip install websockets)")
    except OSError as e:
        log.error(f"WebSocket server failed to bind port {WS_PORT}: {e}")
    except Exception as e:
        log.error(f"WebSocket server failed: {e}")


def _start_game_monitors(loop, bot_state):
    if getattr(bot_state, "passive_map_monitor", None) is None:
        from passive_map import PassiveMapMonitor
        bot_state.passive_map_monitor = PassiveMapMonitor(bot_state)
        bot_state.passive_map_monitor.start()

    if not getattr(bot_state,"pause_hotkey",None):
        from pause_hotkey import PauseHotkey
        bot_state.pause_hotkey=PauseHotkey(loop,lambda:getattr(sys.modules["__main__"],"_async_toggle_all")())
        bot_state.pause_hotkey.start()

    if not getattr(bot_state, "_alert_monitor_future", None):
        from game_alerts import monitor_alerts
        bot_state._alert_monitor_future = asyncio.run_coroutine_threadsafe(monitor_alerts(bot_state), loop)


def start_api(loop: asyncio.AbstractEventLoop, bot_state, *, panel_only=False) -> bool:
    """Serve the local panel. Game monitors belong to the connected session."""
    global _main_loop, _state, _started
    _main_loop = loop
    _state = bot_state
    if not panel_only:
        _start_game_monitors(loop, bot_state)
    if _started:
        return True
    _started = True

    # HTTP API (daemon thread)
    try:
        server = HTTPServer(("127.0.0.1", API_PORT), _Handler)
    except OSError as e:
        log.error(f"Failed to start HTTP server on port {API_PORT}: {e}")
        _started = False
        return False
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    log.info(f"Dashboard API listening on http://127.0.0.1:{API_PORT}")

    # WebSocket push server (asyncio)
    asyncio.run_coroutine_threadsafe(_start_ws_server(), loop)
    return True
