"""Atlas route executor. Explicit start only; one acknowledged operation at a time."""
import asyncio
import time
from time import perf_counter
import logging
import random
from contextvars import ContextVar
from dataclasses import dataclass
from copy import copy,deepcopy
from route_identity import navigation_revision,same_route
from collections import deque
from cavebot_combat import CombatRuntime,validate_combat
from protocol import Direction,ClientOpcode,build_walk_packet,build_use_item_packet,build_use_item_ex_packet,build_move_item_packet
from combat_wave import TURN_OPCODES
from navigation_tools import WEB_ITEM_IDS, covering_body, cuttable_web, supported_transition, transition_tools, web_edge

class MovementDeferred(ValueError):
    """No ordinary step sent: retry from a fresh map while combat stays alive."""

class WebCutUnconfirmed(ValueError):
    """The tool request was sent; await an observable change before stepping."""

class InputsUnavailable(ValueError):
    """Temporary observer gap; wait without changing the user's enable flags."""


def fresh_health(gs):
    """A missing/stale observation has no HP value. Read both fields from the same sample."""
    if getattr(gs, 'client_state_required', False):
        from client_player_state import FRESH_SECONDS
        sample = gs.client_snapshot
        if not sample or not 0 <= time.monotonic() - sample['monotonic'] <= FRESH_SECONDS:
            return None
        hp, maximum = sample.get('hp'), sample.get('max_hp')
    else:
        if not 0 <= time.time() - getattr(gs, 'stats_updated_at', 0) <= 2:
            return None
        hp, maximum = gs.hp, gs.max_hp
    return (hp, maximum) if hp is not None and maximum and 0 <= hp <= maximum else None

log=logging.getLogger(__name__)
NAME="adaptive_cavebot"
LEGACY={"cavebot","cavebot2"}
OFFSETS={(0,-1):Direction.NORTH,(1,0):Direction.EAST,(0,1):Direction.SOUTH,(-1,0):Direction.WEST,
         (1,-1):Direction.NORTHEAST,(1,1):Direction.SOUTHEAST,(-1,1):Direction.SOUTHWEST,(-1,-1):Direction.NORTHWEST}
MANUAL_MOVES={ClientOpcode.NEW_WALK,ClientOpcode.AUTO_WALK,ClientOpcode.STOP_WALK,ClientOpcode.FOLLOW,*[ClientOpcode(n) for n in (0x65,0x66,0x67,0x68,0x6a,0x6b,0x6c,0x6d)]}
WALK_OPCODES={0x65,0x66,0x67,0x68,0x6a,0x6b,0x6c,0x6d}

@dataclass
class WalkTiming:
    number: int
    target: tuple
    position_ms: float = 0.
    map_ms: float | None = None
    history: object = None

    def report(self, outcome):
        if self.history and self.map_ms is not None:
            self.history.route_result("route_map", outcome, self.target, self.map_ms)
        log.info("Walk timing step=%d target=%s position_ms=%.1f map_ms=%s outcome=%s",
                 self.number, self.target, self.position_ms,
                 "-" if self.map_ms is None else f"{self.map_ms:.1f}", outcome)

def distance(a,b):return max(abs(a[0]-b[0]),abs(a[1]-b[1])) if a[2]==b[2] else 100000
def walkable(cell):
    return bool(cell and cell.get("kind")=="clear" and
                cell.get("occupants",0)<=cell.get("occupant_types",{}).get("self",0) and
                all(f["kind"] in {"rope_spot","ladder"} for f in cell.get("navigation",[])))

def web_passable(tile):
    return (walkable(tile) and not tile.get('navigation') and not tile.get('transitions')
            and not any(i['id'] in WEB_ITEM_IDS for i in tile['items']))

def step_clear(cells,origin,target,blocked=(),door=False):
    tiles={(c['x'],c['y'],c['z']):c for c in cells}
    def allowed(p):
        c=tiles.get(tuple(p))
        if tuple(p) in blocked or not c:return False
        if door and list(p)==target:
            return (c.get('kind')=='clear' and c.get('occupants',0)<=c.get('occupant_types',{}).get('self',0) and
                    any((f['kind'] in {'door','level_gate'} or (f['kind']=='passage' and f.get('behavior_hint') in {'use_enters','open_then_step'}))
                        and f.get('door_state_hint')=='open' for f in c.get('navigation',[])))
        return walkable(c)
    # The client enters only the diagonal destination, not the two side tiles.
    return distance(origin,target)==1 and allowed(target)

def detour(cells,start,end,blocked=()):
    """Bounded local BFS: only fresh visible terrain, no unobserved transitions."""
    start,end=tuple(start),tuple(end);tiles={(c['x'],c['y'],c['z']):c for c in cells};blocked=set(blocked)
    allowed=lambda p:p not in blocked and walkable(tiles.get(p))
    queue=deque([start]);previous={start:None}
    while queue and len(previous)<=512:
        p=queue.popleft()
        if p==end:
            path=[]
            while p is not None:path.append(list(p));p=previous[p]
            return path[::-1]
        for dx,dy in OFFSETS:
            q=(p[0]+dx,p[1]+dy,p[2])
            if q in previous or not allowed(q):continue
            previous[q]=p;queue.append(q)
    return None

def varied_detour(cells,start,end,reference,blocked=(),rng=None):
    """One bounded bend per leg; only known walkable cells, at most two extra steps."""
    # Stay within 2 SQM of this ordinary leg, without revisiting cells.
    corridor=[c for c in cells if any(distance((c['x'],c['y'],c['z']),p)<=2 for p in reference)]
    shortest=detour(corridor,start,end,blocked)
    if not shortest or len(shortest)<7:return shortest
    rng=rng or random.SystemRandom()
    mid=shortest[len(shortest)//2];options=[shortest]
    for c in corridor:
        anchor=[c['x'],c['y'],c['z']]
        if anchor==mid or distance(anchor,mid)>2 or not walkable(c):continue
        a=detour(corridor,start,anchor,blocked);b=detour(corridor,anchor,end,blocked)
        if not a or not b:continue
        path=a[:-1]+b
        if len(path)>len(shortest)+2 or len({tuple(v) for v in path})!=len(path):continue
        # Prefer broad, simple bends; avoid a random zigzag at every step.
        dirs=[(q[0]-p[0],q[1]-p[1]) for p,q in zip(path,path[1:])]
        if sum(a!=b for a,b in zip(dirs,dirs[1:]))>3:continue
        if path not in options:options.append(path)
    # Compare alternatives against the whole visible room, including the
    # free space just outside the permitted band around the route.
    terrain = {(c['x'],c['y'],c['z']) for c in cells if walkable(c)} - set(blocked)
    def clearance_cost(path):
        cost = len(path) - len(shortest)
        for x,y,z in path[1:-1]:
            free = sum((x+dx,y+dy,z) in terrain for dx,dy in OFFSETS)
            cost += 8 - free
        return cost
    costs = [clearance_cost(path) for path in options]
    best = min(costs)
    return rng.choice([path for path,cost in zip(options,costs) if cost == best])


def tool_location(snapshot,tool_ids):
    for iid in tool_ids:
        for row in snapshot['sources']:
            if row['item_id']==iid:return (65535,64|row['container'],row['index']),iid,row['index']
        # DBV exposes AiO in equipment slot 10. All ten slots are read
        # with the same verified inventory accessor and use the same wire
        # position (65535, slot, 0); no hotkey/source guessing is needed.
        for slot in range(1,11):
            if snapshot['slots'].get(slot)==iid:return (65535,slot,0),iid,0
    return None


class AdaptiveCavebot:
    # Scope follows the coroutine, never another concurrently running domain.
    @property
    def _route_command(self):return self._route_context.get()[0]
    @_route_command.setter
    def _route_command(self,value):self._route_context.set((value,self._route_context.get()[1]))
    @property
    def _route_boundary(self):return self._route_context.get()[1]
    @_route_boundary.setter
    def _route_boundary(self,value):self._route_context.set((self._route_context.get()[0],value))
    @property
    def _route_attempt(self):return self._attempt_context.get()
    @_route_attempt.setter
    def _route_attempt(self,value):self._attempt_context.set(value)
    @property
    def last_receipt(self):return self._receipt_context.get()
    @last_receipt.setter
    def last_receipt(self,value):self._receipt_context.set(value)

    def __init__(self,state):
        self._route_context=ContextVar('route_command',default=(False,False))
        self._attempt_context=ContextVar('route_attempt',default=None)
        self._receipt_context=ContextVar('command_receipt',default=None)
        self.domains=None;self.combat_command_at=0;self.session_lease=None
        self.state=state;self.status="idle";self.reason="Wybierz zapisany szkic.";self.path=[];self.edges=[]
        self.index=0;self.loop_start=0;self.laps=0;self.loop=True;self.name="";self.scope=None;self.min_hp=30;self.last_command=0
        self.blocked={};self.failures=0;self.logs=deque(maxlen=40);self.generation=0
        self.recovery_attempts=deque(maxlen=3);self.recovery_count=0
        self.checkpoints=[];self.route_draft=None;self.last_waypoint=None;self.tolerated_waypoints=0
        self._saved_plan = None
        self._unresolved_destination = None
        self.navigation_hold=None
        self._held_web=None
        self._pending_body_move=None
        self.variation_until=0;self.blockage=None;self._blockage_key=None;self._blockage_at=0
        self.manual_until=0;self.motion_generation=0;self.recovery_next_at=0;self._route_command=False;self._route_boundary=False;self._route_attempt=None
        self._walk_number = 0
        self._map_after_walk = None
        self._uncertain_walk_origin = None
        self.combat_only=False;self.draft_id=None;self.combat=CombatRuntime(self,validate_combat())
        self.reporter = None
        self._resuming_report = False

    def _observe_report(self, method, *args):
        if self.reporter is None:
            return
        try:
            getattr(self.reporter, method)(*args)
        except Exception:
            log.exception('Cavebot report unavailable')
            self.reporter = None

    def waypoint_progress(self):
        upcoming=next((c for c in self.checkpoints if c['index']>self.index),None)
        return dict(current=upcoming['waypoint'] if upcoming else self.last_waypoint,
                    last_reached=self.last_waypoint,total=len(self.route_draft['points']) if self.route_draft else 0,
                    draft_updated=self.route_draft.get('updated') if self.route_draft else None,
                    navigation_revision=navigation_revision(self.route_draft) if self.route_draft else None,
                    tolerated=self.tolerated_waypoints)

    def mark_waypoint(self):
        for c in self.checkpoints:
            if c['index']==self.index:self.last_waypoint=c['waypoint']

    def session_cursor(self, pending=None):
        plan = dict(path=self.path, edges=self.edges, checkpoints=self.checkpoints, loop_start=self.loop_start,
                    scope=self.scope, draft_id=self.draft_id, loop=self.loop, min_hp=self.min_hp)
        cursor = dict(index=self.index, last_waypoint=self.last_waypoint, laps=self.laps,
                      tolerated_waypoints=self.tolerated_waypoints, pending=pending,
                      revision=navigation_revision(self.route_draft))
        return cursor, plan

    def save_session_cursor(self, pending=None):
        journal = getattr(self.state.game_state, "engine_journal", None)
        if journal is None or self.combat_only or not self.route_draft:
            return
        cursor, plan = self.session_cursor(pending)
        changed = plan != self._saved_plan
        if changed:
            plan = deepcopy(plan)
        journal.save_route(cursor, plan if changed else None)
        if changed:
            self._saved_plan = plan

    @classmethod
    def from_session_cursor(cls, state):
        journal = getattr(state.game_state, "engine_journal", None)
        monitor = getattr(state, "passive_map_monitor", None)
        if journal is None or monitor is None or not monitor.atlas:
            return None
        saved = journal.route()
        if not saved or saved["scope"] != monitor.atlas.scope:
            return None
        draft = next((d for d in monitor.atlas.drafts() if d["id"] == saved["draft_id"]), None)
        if not draft or navigation_revision(draft) != saved["revision"]:
            return None
        controller = cls(state)
        for key in ("path", "edges", "checkpoints", "loop_start", "scope", "draft_id", "loop",
                    "index", "last_waypoint", "laps", "tolerated_waypoints"):
            setattr(controller, key, saved[key])
        controller.route_draft = deepcopy(draft)
        controller.name = draft["name"]
        controller.min_hp = saved["min_hp"]
        controller.guard()
        controller.map_now()
        controller._unresolved_destination = saved["pending"]
        controller.status = "paused"
        controller.reason = "Odzyskano postęp po restarcie. Możesz wznowić trasę."
        return controller

    def map_preview(self):
        """A bounded view of decisions already made, in world coordinates."""
        if self.status != "running" or self.combat_only or not self.path:
            return None
        origin = tuple(self.state.game_state.position or ())
        if len(origin) != 3:
            return None
        combat = self.combat
        points = [origin]
        stop = None
        if combat.phase == 'positioning' and combat.placement:
            start, placement_path, expires = combat.placement
            offset = self.index - start
            if (time.monotonic() < expires and 0 <= offset < len(placement_path)
                    and tuple(placement_path[offset]) == origin):
                points = list(placement_path[offset:offset + 9])
                stop = combat.pack_destination or points[-1]
        elif combat.phase == 'fight' and combat.lure_stop_reason:
            stop = origin
        elif (combat.phase in {'route', 'lure'} and not combat.lure_waiting
              and not self.navigation_hold and not self.blockage
              and tuple(self.path[self.index]) == origin):
            for target in self.path[self.index + 1:self.index + 9]:
                point = tuple(target)
                if point[2] != origin[2] or max(abs(a-b) for a,b in zip(point, points[-1])) != 1:
                    break
                points.append(point)
        if len(points) == 1 and stop is None:
            return None
        return {"path": points, "stop": stop}

    def snapshot(self):
        return {"status":self.status,"reason":self.reason,"map_preview":self.map_preview(),"domains":self.domains.snapshot() if self.domains else None,"navigation_hold":self.navigation_hold,"name":self.name,"index":self.index,"total":len(self.path),
                "mode":"combat" if self.combat_only else "route","scope":self.scope,"waypoint":self.waypoint_progress(),"resumable":bool(self.path and self.status in {"idle","paused","ready"}),"loops":self.laps,"loop":self.loop,"draft_id":self.draft_id,"combat":self.combat.snapshot(),"approach_remaining":max(0,self.loop_start-self.index) if self.path else 0,"position":self.path[self.index] if self.path else None,"logs":list(self.logs),"recovery_count":self.recovery_count,"blockage":self.blockage}

    def pause(self,reason):
        if self.status == 'running':
            self._observe_report('lifecycle','pause',reason)
        self.generation+=1;self.status="paused";self.reason=reason;self.logs.append(reason)
        self.combat.reset_lure();self.combat.battle_started=None;self.combat.last_health=None

    def stop(self,reason="Zatrzymano trasę.",*,preserve_progress=False):
        if self.status == 'running':
            self._observe_report('lifecycle','stop',reason)
        self.generation+=1;self.status="idle";self.reason=reason;self.navigation_hold=None;self._held_web=None;self._pending_body_move=None
        if not preserve_progress:
            self.path=[];self.edges=[];self.index=0;self.checkpoints=[];self.last_waypoint=None;self.route_draft=None
            journal = getattr(self.state.game_state, "engine_journal", None)
            if journal is not None and journal.current():
                journal.save_route(None)
            self._saved_plan = None
        elif self.path and not self.combat_only:
            self.reason='Zatrzymano. Postęp trasy zachowany — możesz wznowić.' 
        self.combat.reset_lure();self.combat.battle_started=None;self.combat.last_health=None

    def retry_navigation(self):
        self.guard();self.map_now()
        if self.status!='running' or not self.navigation_hold:raise ValueError('Brak wstrzymanego przejścia.')
        pos=list(self.state.game_state.position)
        if self.index+1<len(self.path) and pos==self.path[self.index+1]:
            self.index+=1;self.mark_waypoint()
        elif pos!=self.path[self.index]:
            raise ValueError('Pozycja zmieniła się. Zatrzymaj i uruchom ponownie; walka pozostaje aktywna.')
        self.navigation_hold=None;self._held_web=None;self.reason='Ponownie sprawdzam przejście.'

    def guard(self,combat=False):
        st=self.state;gs=st.game_state
        if getattr(gs, "engine_recovery_required", False) and not getattr(gs, "engine_journal", None):
            raise InputsUnavailable("Czekam na potwierdzenie sesji.")
        if self.session_lease and not self.session_lease.valid():raise ValueError("Sesja zmieniła się — uruchom automat ponownie.")
        if not st.connected:raise ValueError("Brak połączenia z grą.")
        if not st.game_proxy.logged_in or st.game_proxy.xtea_keys is None:raise ValueError("Połączenie proxy nie jest gotowe do ruchu.")
        if st.automation_paused:raise ValueError("Automatyka jest wstrzymana.")
        if not combat and getattr(st,"recording_active",False):raise ValueError("Zatrzymaj nagrywanie przed uruchomieniem trasy.")
        health = fresh_health(gs)
        if health is None:
            if getattr(gs, 'client_state_required', False):
                raise InputsUnavailable("Brak świeżej pozycji i HP.")
            raise InputsUnavailable("Brak świeżych statystyk.")
        hp, maximum = health
        if hp == 0 or not combat and 100*hp/maximum < self.min_hp:raise ValueError("HP poniżej progu pauzy nawigacji.")
        monitor=getattr(st,"passive_map_monitor",None)
        if not combat and (not monitor or not monitor.atlas or monitor.atlas.scope!=self.scope):raise ValueError("Zmienił się profil atlasu.")
        for name,task in st._action_tasks.items():
            if not combat and name in LEGACY and not task.done():raise ValueError("Inny silnik ruchu jest uruchomiony.")
            if self.combat.config['enabled'] and name in {'auto_targeting','auto_targeting2','auto_combat','auto_rune_and_spell','aoe_spell'} and not task.done():raise ValueError('Inna akcja walki jest uruchomiona.')
        if not combat and getattr(st,"_auto_task",None) and not st._auto_task.done():raise ValueError("Inny automatyczny ruch jest uruchomiony.")

    def prepare_combat_only(self,config):
        # This mode never owns movement and does not need a saved route.
        if config is not None and not isinstance(config,dict):raise ValueError('Nieprawidłowy profil walki.')
        self.combat_only=True;self.min_hp=0;self.draft_id=None;self.edges=[];self.index=0;self.loop_start=0;self.laps=0
        monitor=getattr(self.state,'passive_map_monitor',None)
        self.scope=getattr(getattr(monitor,'atlas',None),'scope',None)
        validated=validate_combat({**(config or {}),'enabled':True,'mode':'stop'})
        self.combat=CombatRuntime(self,validated);self.guard();self.map_now()
        self.path=[list(self.state.game_state.position)];self.loop=False
        self.name='Sama walka';self.reason='Walka przygotowana. Ruch pozostaje ręczny.';self.status='ready'

    def prepare(self,scope,draft_id,loop=True,min_hp=0):
        inputs=self._prepare_inputs(scope,draft_id,loop,min_hp)
        self._plan_prepared(*inputs,loop)

    async def prepare_async(self,scope,draft_id,loop=True,min_hp=0):
        inputs=self._prepare_inputs(scope,draft_id,loop,min_hp)
        atlas,draft,pos,_=inputs
        proxy=self.state.game_proxy;player_id=self.state.game_state.player_id
        # Even a small saved route can take longer than the 500 ms sampler TTL.
        # Keep the event loop free for DLL samples, potions and Stop requests.
        await asyncio.to_thread(self._plan_prepared,*inputs,loop)
        self.guard();self.map_now()
        if (self.state.game_proxy is not proxy or self.state.game_state.player_id!=player_id or
            self.state.passive_map_monitor.atlas is not atlas):
            raise ValueError('Sesja zmieniła się podczas planowania. Uruchom trasę ponownie.')
        if list(self.state.game_state.position)!=pos:
            raise ValueError('Pozycja zmieniła się podczas planowania. Uruchom trasę ponownie.')
        current=next((d for d in atlas.drafts() if d['id']==draft_id),None)
        if current!=draft:
            raise ValueError('Szkic zmienił się podczas planowania. Uruchom aktualną wersję ponownie.')

    def _prepare_inputs(self,scope,draft_id,loop,min_hp):
        if type(loop)is not bool or type(min_hp)is not int or not 0<=min_hp<=90:raise ValueError("Nieprawidłowe ustawienia nawigacji.")
        self.combat_only=False;self.scope=scope;self.min_hp=min_hp;self.guard()
        monitor=self.state.passive_map_monitor
        draft=deepcopy(next((d for d in monitor.atlas.drafts() if d['id']==draft_id),None))
        if draft is None:raise ValueError("Najpierw zapisz i wybierz szkic w tym atlasie.")
        self.route_draft=deepcopy(draft)
        self.draft_id=draft_id;self.combat=CombatRuntime(self,validate_combat(self.state.settings.get('combat_profile',draft.get('combat'))))
        if self.combat.config['enabled']:
            conflicts={'auto_targeting','auto_targeting2','auto_combat','auto_rune_and_spell','aoe_spell'}
            if any(n in conflicts and not t.done() for n,t in self.state._action_tasks.items()):raise ValueError('Wyłącz osobne akcje ataku i czarów przed walką V2.')
        points=draft['points'];pos=list(self.state.game_state.position)
        observations=deepcopy(list(monitor.learner.records.values())) if monitor.learner else []
        sample=monitor.snapshot()
        if (sample.get('status')!='ready' or sample.get('stale') or sample.get('age_seconds',99)>.6 or list(sample.get('center',[]))!=pos):
            raise ValueError("Brak świeżej mapy przy postaci; poczekaj na odczyt przed Start.")
        return monitor.atlas,draft,pos,observations

    def _plan_prepared(self,atlas,draft,pos,observations,loop):
        # Worker owns only this unpublished controller; no live state or commands.
        points=draft['points']
        route=atlas.plan(points,observations,loop=loop,links=draft.get("links",[]))
        if not route['complete']:raise ValueError("Trasa lub powrót pętli ma lukę: "+route['gaps'][0]['reason'])
        if loop and not route['loop_closed']:raise ValueError(route.get('return_error') or 'Brak znanej drogi powrotnej dla powtarzania.')
        if len(route['path'])<2:raise ValueError("Trasa nie zawiera ruchu.")
        unsupported=next(((i,e) for i,e in enumerate(route['transitions'],1) if not supported_transition(e)),None)
        if unsupported:
            i,e=unsupported
            raise ValueError(f"T{i} na {tuple(e['entry'])}: użycie narzędzia lub czaru ({e['action']}, ID {e.get('tool_id')}) nie jest obsługiwane lub nie ma pełnych danych przejścia.")
        if route['avoid_tiles']:raise ValueError("Trasa zawiera pola oznaczone jako omijane. Zmień szkic przed uruchomieniem.")
        # Pick the reachable waypoint with the shortest known ordinary approach,
        # rather than requiring A or choosing a geometrically close wall.
        joins={leg['from_index']:leg['start'] for leg in reversed(route['legs']) if not leg.get('returning')}
        joins.setdefault(len(points)-1,route['return_start'])
        best=None;seen=set()
        for i in sorted(joins,key=lambda i:(distance(pos,points[i]),i)):
            p=points[i];key=tuple(p)
            if p[2]!=pos[2] or key in seen:continue
            seen.add(key)
            if best and distance(pos,p)>=len(best[1]['path'])-1:continue
            entry={'complete':True,'path':[pos],'transitions':[],'avoid_tiles':[]} if pos==p else atlas.plan([pos,p],observations)
            if (entry['complete'] and not entry['avoid_tiles'] and all(supported_transition(e) for e in entry['transitions'])
                and (best is None or len(entry['path'])<len(best[1]['path']))):best=(i,entry)
        if best is None:raise ValueError('Brak znanego dojścia do waypointów na obecnym piętrze. Podejdź do dostępnej części trasy.')
        join,entry=best;offset=joins[join];approach=entry['path'];route_path=route['path']
        if loop:route_path=route_path[offset:-1]+route_path[:offset+1]
        else:route_path=route_path[offset:]
        self.path=approach[:-1]+route_path;self.loop_start=len(approach)-1;self.edges=entry['transitions']+route['transitions']
        anchors={(leg['start'],leg['from_index']) for leg in route['legs']} | {(leg['end'],leg['to_index']) for leg in route['legs']}
        size=len(route['path'])-1
        self.checkpoints=sorted([dict(index=len(approach)-1+(n-offset if n>=offset else n-offset+size),waypoint=w)
                                 for n,w in anchors if loop or n>=offset],key=lambda c:(c['index'],c['waypoint']))
        self.last_waypoint=None;self.tolerated_waypoints=0
        self.loop=loop;self.index=0;self.laps=0;self.name=draft['name'];self.status='ready';self.reason=f'Trasa przygotowana od waypointu {join+1}.'
        self.blocked={};self.failures=0;self.recovery_attempts.clear();self.recovery_count=0
        self.mark_waypoint()

    def resumable_for(self,scope,draft_id,loop):
        return bool(self.path and not self.combat_only and self.status in {'idle','paused','ready'} and
                    self.scope==scope and self.draft_id==draft_id and self.loop==loop)

    async def resume_candidate(self):
        self.guard();self.map_now()
        if self.combat_only:return self
        if (self._unresolved_destination is not None and
                list(self.state.game_state.position) != self._unresolved_destination):
            raise ValueError("Brak potwierdzenia ostatniego ruchu. Sprawdź pozycję przed wznowieniem.")
        atlas=self.state.passive_map_monitor.atlas
        stored=next((d for d in atlas.drafts() if d['id']==self.draft_id),None)
        if not stored:
            raise ValueError('Brak zapisanego szkicu do wznowienia.')
        draft=stored
        if not same_route(stored,self.route_draft):
            if self.status!='paused' or not self.route_draft:
                raise ValueError('Zmieniono przebieg trasy. Kliknij Uruchom w panelu Cavebota, aby użyć zapisanego szkicu.')
            # Pause continues the same run, including its waypoint numbering.
            draft=self.route_draft
        c=copy(self)
        c.reporter = None
        c._resuming_report = True
        c._unresolved_destination = None
        # Apply saved combat changes only on explicit resume. Keep spell/global
        # cooldown deadlines; new target/lure state must use the new rules.
        config=validate_combat(self.state.settings.get('combat_profile',draft.get('combat')))
        if config!=self.combat.config:
            c.combat=CombatRuntime(c,config)
            c.combat.deadlines=deepcopy(self.combat.deadlines)
            c.combat.cast_after=self.combat.cast_after
        else:
            c.combat=copy(self.combat);c.combat.owner=c
        c.route_draft=deepcopy(draft);c.name=draft['name']
        for key in ('path','edges','checkpoints','blocked','logs','recovery_attempts'):
            setattr(c,key,deepcopy(getattr(self,key)))
        pos=list(self.state.game_state.position);proxy=self.state.game_proxy;player=self.state.game_state.player_id
        observations=deepcopy(list(self.state.passive_map_monitor.learner.records.values())) if self.state.passive_map_monitor.learner else []
        # Pause may arrive after a step/use was sent but before its acknowledgment
        # advanced the cursor. A fresh exact next position completes that step;
        # in particular never ask the player to repeat an acknowledged descent.
        if c.index+1<len(c.path) and pos==c.path[c.index+1]:
            c.index+=1;c.mark_waypoint()
        if pos!=c.path[c.index]:
            await asyncio.to_thread(c._plan_resume,atlas,pos,observations)
        c.guard();c.map_now()
        if (self.state.game_proxy is not proxy or self.state.game_state.player_id!=player or
            self.state.passive_map_monitor.atlas is not atlas or list(self.state.game_state.position)!=pos):
            raise ValueError('Pozycja lub sesja zmieniła się podczas wznawiania. Spróbuj ponownie.')
        if next((d for d in atlas.drafts() if d['id']==self.draft_id),None)!=stored:
            raise ValueError('Szkic zmienił się podczas wznawiania.')
        c.status='ready';c.reason='Wznowienie od zapamiętanego odcinka.'
        return c

    def _plan_resume(self,atlas,pos,observations):
        anchor=self.path[self.index]
        if pos[2]!=anchor[2]:raise ValueError('Wznowienie: wróć na piętro zapamiętanego odcinka.')
        entry=atlas.plan([pos,anchor],observations)
        if not entry['complete'] or entry['avoid_tiles'] or entry['transitions']:
            raise ValueError('Brak bezpiecznego dojścia do zapamiętanego odcinka bez przejść. Podejdź bliżej.')
        offset=self.index;begin=self.loop_start;size=len(self.path)-1-begin
        old=self.path;anchors=self.checkpoints;approach=entry['path']
        if self.loop and offset>=begin:
            cycle=old[offset:-1]+old[begin:offset+1]
            rows=[dict(index=(r['index']-offset if r['index']>=offset else r['index']-offset+size)+len(approach)-1,waypoint=r['waypoint'])
                  for r in anchors if r['index']>=begin]
        else:
            cycle=old[offset:]
            rows=[dict(index=r['index']-offset+len(approach)-1,waypoint=r['waypoint']) for r in anchors if r['index']>offset]
        self.path=approach[:-1]+cycle;self.checkpoints=sorted(rows,key=lambda r:(r['index'],r['waypoint']))
        self.loop_start=len(approach)-1+max(0,begin-offset);self.index=0

    def replace_path(self,start,end,local):
        """Keep original waypoint identities when a local path changes length."""
        delta=len(local)-(end-start+1);rows=[]
        for row in self.checkpoints:
            n=row['index']
            if n<start:rows.append(row)
            elif n>end:rows.append({**row,'index':n+delta})
            elif n==end and self.path[n]==local[-1]:rows.append({**row,'index':start+len(local)-1})
            elif n==start and self.path[n]==local[0]:rows.append(row)
        if self.loop_start>end:self.loop_start+=delta
        elif start <= self.loop_start <= end:
            anchor = self.path[self.loop_start]
            # A detour can keep the start of the lap anywhere in the new leg.
            # If the anchor was bypassed, the rejoin tile becomes the new start.
            if self.loop_start == end and local[-1] == anchor:
                self.loop_start = start + len(local) - 1
            else:
                self.loop_start = start + (local.index(anchor) if anchor in local else len(local) - 1)
        if self.variation_until>end:self.variation_until+=delta
        elif start<=self.variation_until<=end:self.variation_until=start
        self.path[start:end+1]=local;self.checkpoints=sorted(rows,key=lambda r:(r['index'],r['waypoint']))

    def checkpoint_limit(self,default):
        return min(default,next((r['index']+1 for r in self.checkpoints if r['index']>self.index),default))

    def skip_repeated_sweep(self):
        """A→B→A→B covers exactly the same tiles as A→B, including the return corridor."""
        i=self.index
        if self.combat_only or i<self.loop_start:
            return False
        for width in range(1,min(16,(len(self.path)-1-i)//3)+1):
            end=i+2*width
            if self.path[end]!=self.path[i]:
                continue
            outbound=self.path[i:i+width+1]
            if (self.path[i+width:end+1]!=outbound[::-1] or self.path[end:end+width+1]!=outbound
                    or any(p[2]!=outbound[0][2] for p in outbound)):
                continue
            labels=(self.route_draft or {}).get('labels') or ()
            if any(i<r['index']<=end and r['waypoint']<len(labels) and labels[r['waypoint']]
                   for r in self.checkpoints):
                continue
            if any(e['origin'] in outbound or e['exit'] in outbound for e in self.edges):
                continue
            self.index=end
            self.mark_waypoint()
            self.save_session_cursor()
            return True
        return False

    async def reconnect_route_step(self, origin, target, closing=False):
        """Fill a gap with ordinary steps, each still checked on the live map."""
        if origin[2] != target[2]:
            return False
        monitor = self.state.passive_map_monitor
        index = self.index
        generation, motion = self.generation, self.motion_generation
        observations = deepcopy(list(monitor.learner.records.values())) if monitor.learner else []
        plan = await asyncio.to_thread(monitor.atlas.plan, [origin, target], observations)
        if (self.status != 'running' or self.generation != generation or self.motion_generation != motion
                or self.index != index or self.path[index:index + 1] != [origin]
                or list(self.state.game_state.position) != origin):
            return False
        bridge = plan['path']
        if (not plan['complete'] or plan['transitions'] or plan['avoid_tiles'] or len(bridge) < 2
                or bridge[0] != origin or bridge[-1] != target
                or any(distance(a, b) != 1 for a, b in zip(bridge, bridge[1:]))):
            return False
        if closing:
            if index != len(self.path) - 1 or self.path[self.loop_start] != target:
                return False
            self.path.extend(bridge[1:])
        else:
            if self.path[index + 1:index + 2] != [target]:
                return False
            self.replace_path(index + 1, index + 1, bridge[1:])
        self.logs.append(f'Uzupełniono odcinek {tuple(origin)} → {tuple(target)}: {len(bridge)-1} kroków.')
        return True

    def vary_leg(self,sample):
        if self.index<self.variation_until:return False
        end=min(len(self.path)-1,self.index+8,self.checkpoint_limit(len(self.path))-1)
        protected={tuple(e[k]) for e in self.edges for k in ('origin','entry','exit')}
        if self.index<self.loop_start:end=min(end,self.loop_start)
        for j in range(self.index+1,end+1):
            if tuple(self.path[j]) in protected or self.path[j][2]!=self.path[self.index][2]:end=j-1;break
        if end-self.index<6:return False
        local=varied_detour(sample['cells'],self.path[self.index],self.path[end],self.path[self.index:end+1],set(self.blocked)|protected)
        if not local:return False
        self.replace_path(self.index,end,local);self.variation_until=self.index+len(local)-1
        return True

    def describe_blockage(self,sample,target,*,cancelled=False):
        pos=list(self.state.game_state.position)
        cell=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==target),None)
        if not cell:cause='brak świeżego odczytu pola'
        elif cell.get('kind')=='unknown':cause=cell.get('reason') or 'niepełny odczyt pola'
        elif cell.get('kind')=='no_ground':cause='brak potwierdzonego podłoża'
        elif cell.get('occupants',0)>cell.get('occupant_types',{}).get('self',0):cause='pole zajęte: '+str(cell.get('occupant_types',{}))
        elif cell.get('kind')!='clear':cause='przeszkoda terenowa'
        elif any(f['kind'] not in {'rope_spot','ladder'} for f in cell.get('navigation',[])):cause='obiekt wymaga jawnego przejścia'
        elif cancelled:cause='serwer anulował krok; sprawdzam obejście'
        elif tuple(target) in self.blocked:cause='poprzedni krok bez potwierdzenia; krótka przerwa przed ponowieniem'
        else:cause='brak bezpiecznego połączenia z kolejnym odcinkiem'
        detail=dict(origin=pos,target=list(target),cause=cause,waypoint=self.waypoint_progress()['current'],
                    tile_kind=cell.get('kind') if cell else None,
                    item_ids=[i['id'] for i in (cell or {}).get('items',[])],
                    occupants=(cell or {}).get('occupant_types',{}),navigation=(cell or {}).get('navigation',[]))
        key=repr(detail);now=time.monotonic()
        if key!=self._blockage_key or now-self._blockage_at>=5:
            self._blockage_key=key;self._blockage_at=now;self.logs.append(f'Pole {tuple(target)}: {cause}.')
            log.info('V2 movement wait: %s',detail)
            history=getattr(self.state.game_state,'movement_history',None)
            if history:history.web_blocked(pos,target,cell,cause)
        self.blockage=detail
        self.reason=f'Pole {tuple(target)}: {cause}. Czekam; '+('walka aktywna.' if self.combat.config['enabled'] else 'ruch będzie ponowiony.')

    def tolerate_waypoint(self,sample,*,recovering=False):
        """A blocked ordinary anchor within 2 SQM may be passed via fresh terrain.

        A transition or object action is never credited by proximity. Nor is a
        one-shot final destination: there must be a verified continuation.
        """
        if self.combat_only or self.status!='running':return False
        self.guard()
        if (sample.get('status')!='ready' or sample.get('stale') or sample.get('age_seconds',99)>.6 or
            list(sample.get('center',[]))!=list(self.state.game_state.position)):return False
        checkpoint=next((r for r in self.checkpoints if r['index']>self.index),None)
        if not checkpoint or checkpoint['index'] >= len(self.path):return False
        k=checkpoint['index'];point=self.path[k];pos=list(self.state.game_state.position)
        expected = self.path[self.index]
        displaced = pos != expected
        if displaced and (not recovering or distance(pos, expected) > 6):return False
        if not 0<distance(pos,point)<=2:return False
        protected={tuple(e[key]) for e in self.edges for key in ('origin','entry','exit')}
        cell=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==point),None)
        if (not cell or cell.get('navigation') or tuple(point) in protected or
            cell.get('kind') not in {'clear','blocked','unknown','no_ground'}):return False
        occupied=cell.get('occupants',0)>cell.get('occupant_types',{}).get('self',0)
        if not occupied and cell['kind']=='clear' and self.blocked.get(tuple(point),0)<=time.monotonic():return False
        # Never skip a transition's approach/exit, even on the same floor.
        if any(tuple(p) in protected or p[2]!=pos[2] for p in self.path[self.index+1:k+1]):return False
        excluded = set(self.blocked) | protected | {tuple(point)}
        # Keep the lap continuous for the next visit. The player has already
        # covered this part by hand, so advance past it without issuing steps.
        bridge = detour(sample['cells'], expected, pos, excluded) if displaced else [pos]
        if not bridge or len(bridge) > 13:return False
        wrap=k==len(self.path)-1 and self.loop
        if wrap:
            begin=self.loop_start
            next_row=next((r for r in self.checkpoints if r['index']>begin),None)
            if not next_row or next_row['index']>=self.index:return False
            candidates=range(begin+1,min(next_row['index']+1,begin+9))
        else:
            next_row=next((r for r in self.checkpoints if r['index']>k),None)
            if not next_row:return False
            candidates=range(k+1,min(next_row['index']+1,k+9))
        usable=[]
        for j in candidates:
            if tuple(self.path[j]) in protected or self.path[j][2]!=pos[2]:break
            usable.append(j)
        for j in reversed(usable):
            local=detour(sample['cells'],pos,self.path[j],excluded | ({tuple(expected)} if displaced else set()))
            if not local or (len(local)<2 and not displaced):continue
            replacement = bridge[1:] + local[1:]
            number=checkpoint['waypoint'];gap=distance(pos,point)
            if wrap:
                joined=next((r['waypoint'] for r in self.checkpoints if r['index']==j),None)
                self.loop_start=j
                self.replace_path(self.index+1,len(self.path)-1,replacement)
                if joined is not None:self.checkpoints.append(dict(index=len(self.path)-1,waypoint=joined))
            else:self.replace_path(self.index+1,j,replacement)
            self.index += len(bridge) - 1
            self.last_waypoint=number;self.tolerated_waypoints+=1
            self.logs.append(f'Waypoint {number+1}: zaliczony z {gap} SQM, obejście przeszkody po świeżej mapie.')
            return True
        return False

    def next_ordinary_tile(self, sample):
        if self.index+1 >= len(self.path):
            return None
        origin, target = self.path[self.index:self.index+2]
        if distance(origin,target) != 1 or any(e[key] in (origin,target)
                for e in self.edges for key in ('origin','entry','exit')):
            return None
        tile = next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==target),None)
        if not walkable(tile) or tile.get('navigation') or tile.get('stairs'):
            return None
        return target

    def recover_local(self,sample):
        """Rejoin the current ordinary leg; local contention waits without stopping combat."""
        if self.combat_only or self.status!='running':return False
        self.guard();pos=list(self.state.game_state.position);now=time.monotonic()
        expected=self.path[self.index]
        if distance(pos,expected)>6:raise ValueError('Recovery: poza lokalnym zasięgiem lub inne piętro — sprawdź drogę powrotną.')
        if pos == self.next_ordinary_tile(sample):
            self.index += 1
            self.mark_waypoint()
            self.save_session_cursor()
            return True
        if now<self.recovery_next_at:return False
        self.recovery_next_at=now+.25
        if pos != expected and self.tolerate_waypoint(sample, recovering=True):
            self.recovery_attempts.append(now)
            self.recovery_count += 1
            self.reason='Kontynuuję trasę od ręcznie osiągniętego pola.'
            return True
        stop=min(len(self.path),self.index+9,self.loop_start+1 if self.index<self.loop_start else len(self.path))
        stop=self.checkpoint_limit(stop)
        for j in range(self.index,stop):
            if j>self.index and any(e['origin']==self.path[j-1] and e['exit']==self.path[j] for e in self.edges):break
            local=detour(sample['cells'],pos,self.path[j],self.blocked)
            if not local or len(local)>13:continue
            self.replace_path(self.index,j,local);self.recovery_attempts.append(now);self.recovery_count+=1
            self.logs.append(f'Recovery: powrót do trasy po {len(local)-1} widocznych polach, piętro {pos[2]}.')
            self.reason='Powrót do trasy.';return True
        self.reason='Powrót do trasy: droga zajęta — czekam; walka aktywna.'
        return False

    async def fresh_map(self):
        timing = self._map_after_walk
        if timing is None:
            return await self._await_fresh_map()
        if tuple(self.state.game_state.position) != timing.target:
            self._finish_walk_timing("position_changed")
            return await self._await_fresh_map()
        started = perf_counter()
        outcome = "map_interrupted"
        try:
            sample = await self._await_fresh_map()
            outcome = "ready" if tuple(sample["center"]) == timing.target else "position_changed"
            return sample
        except InputsUnavailable:
            outcome = "map_timeout"
            raise
        finally:
            # Count only time actually spent obtaining the map. Combat or a
            # deliberate stop between the two phases is not map latency.
            timing.map_ms = (timing.map_ms or 0.) + (perf_counter() - started) * 1000
            if outcome == "map_timeout":
                timing.report(outcome)  # cumulative if navigation retries
            else:
                self._finish_walk_timing(outcome)

    async def _await_fresh_map(self):
        deadline=time.monotonic()+.8
        monitor = self.state.passive_map_monitor
        request = getattr(monitor, "request_refresh", None)
        while True:
            self.guard()
            if self.status!='running':raise ValueError(self.reason)
            sample=monitor.snapshot()
            if (sample.get('status')=='ready' and not sample.get('stale') and
                    sample.get('age_seconds',99)<.6 and list(sample.get('center',[]))==list(self.state.game_state.position)):
                return sample
            if time.monotonic()>=deadline:raise InputsUnavailable("Mapa chwilowo nieaktualna; nawigacja czeka na odczyt.")
            if request is not None:
                request()
                request = None
            await asyncio.sleep(.01)

    async def send(self,payload,validate=None,skip_if_changed=False,defer_movement=False):
        self.last_receipt=None
        if self.combat_only and (not payload or payload[0] not in {ClientOpcode.ATTACK,ClientOpcode.SAY,*TURN_OPCODES}):
            raise ValueError('Tryb samej walki nie może wysyłać ruchu ani używać obiektów.')
        # Position acknowledgment and fresh terrain already gate every step.
        # Do not impose the old 200 ms item-use interval on ordinary walking.
        interval=.04 if payload and payload[0] in WALK_OPCODES else .2
        route_command=self._route_command;boundary=self._route_boundary;motion=self.motion_generation
        combat_command=bool(payload and payload[0] in {ClientOpcode.ATTACK,ClientOpcode.SAY,*TURN_OPCODES})
        previous=self.combat_command_at if combat_command else self.last_command
        await asyncio.sleep(max(0,interval-(time.monotonic()-previous)))
        self.guard(combat=combat_command)
        if self.status!='running':raise ValueError(self.reason)
        sent=time.monotonic();generation=self.generation
        if combat_command:self.combat_command_at=sent
        else:self.last_command=sent
        origin=list(self.state.game_state.position)
        from session_lifetime import SessionLease,current_task,task_alive
        lease=SessionLease(self.state);owner=current_task() if getattr(self.state.game_proxy,'command_delivery',False) else None
        attempt=dict(valid=None,checks=0,sent=sent,motion=motion)
        if route_command:self._route_attempt=attempt
        def allowed():
            try:
                self.guard(combat=combat_command)
                journal = getattr(lease.game, "engine_journal", None)
                valid=(lease.valid() and (journal is None or journal.current()) and task_alive(owner) and self.status=='running' and self.generation==generation and time.monotonic()-sent<.5 and
                    (combat_command and payload[0] in {ClientOpcode.ATTACK,ClientOpcode.SAY} or self.combat_only or list(self.state.game_state.position)==origin) and
                    (not route_command or (motion==self.motion_generation and time.monotonic()>=self.manual_until and self.combat.route_step_allowed(boundary))) and
                    (validate is None or validate()))
            except Exception:valid=False
            attempt['valid']=valid;attempt['checks']+=1
            # The transport checks the guard again immediately before writing.
            if valid and route_command and attempt['checks'] >= 2 and payload[0] in WALK_OPCODES:
                attempt['walk_checked_at'] = perf_counter()
            return valid
        if not allowed():
            # A moving/dying monster or changed cast condition is normal. Keep
            # fatal/lifetime checks above; combat and ordinary movement requests may be skipped.
            if skip_if_changed and payload and payload[0] in {ClientOpcode.ATTACK,ClientOpcode.SAY,*TURN_OPCODES}:
                return False
            if route_command:raise MovementDeferred('Ponownie sprawdzam grupę i drogę; walka aktywna.')
            if defer_movement and payload and payload[0] in WALK_OPCODES:return False
            raise ValueError('Warunki ruchu zmieniły się przed wysłaniem; wstrzymano trasę.')
        from command_delivery import delivery,attempted
        receipt=await self.state.game_proxy.inject_to_server(payload,guard=allowed)
        self.last_receipt=receipt
        await delivery(receipt)
        if receipt is not None:
            attempt['receipt']=receipt
            if not attempted(receipt):
                attempt['valid']=False
                self.logs.append(f'Komenda {receipt.id}: {receipt.status} ({receipt.reason}).')
                if route_command:raise MovementDeferred('Krok nie został wysłany — sprawdzam drogę; walka aktywna.')
                return False
        return True

    def map_now(self):
        sample=self.state.passive_map_monitor.snapshot()
        if (sample.get('status')!='ready' or sample.get('stale') or sample.get('age_seconds',99)>.6 or
                list(sample.get('center',[]))!=list(self.state.game_state.position)):
            raise InputsUnavailable('Mapa nie jest aktualna.')
        return sample

    async def wait_position(self,expected,origin,timeout=2.5,intermediate=None,recover=False):
        deadline=time.monotonic()+timeout;motion=self.motion_generation;attempt=self._route_attempt if self._route_command else None
        while time.monotonic()<deadline:
            if self.status!='running':raise ValueError(self.reason)
            try:
                self.guard()
            except InputsUnavailable:
                # Delivery already happened. Keep this confirmation pending so a late
                # position from the same step cannot trigger a second dispatch.
                await asyncio.sleep(.05)
                continue
            pos=list(self.state.game_state.position)
            if pos==expected:
                from command_delivery import confirm
                confirm(attempt.get("receipt") if attempt else getattr(self,"last_receipt",None),"position_observed")
                return True
            if attempt and (attempt['valid'] is False or attempt['checks']<2 and time.monotonic()-attempt['sent']>.6):
                raise MovementDeferred('Krok nie został wysłany — ponownie sprawdzam drogę; walka aktywna.')
            if motion!=self.motion_generation or time.monotonic()<self.manual_until:
                if recover:return False
                raise MovementDeferred('Ręczny ruch — ponownie sprawdzam pozycję.')
            if recover and attempt and pos == origin and attempt.get('motion') == self.motion_generation:
                cancel = self.state.game_state.last_walk_cancel
                checked_at = attempt.get('walk_checked_at')
                # A cancel reports the resulting position, with no command ID.
                # A late reply to an unresolved walk from here is ambiguous.
                if (cancel and checked_at is not None
                        and cancel[0] > checked_at and cancel[1] == tuple(origin)
                        and self._uncertain_walk_origin != cancel[1]):
                    attempt['cancelled'] = True
                    return False
            if (self._route_command and self.combat.lure_stop_reason and pos==origin
                    and not self.combat.positioning_step(origin,expected)):
                raise MovementDeferred('Grupa wymaga walki przed dalszą trasą.')
            if pos!=origin and pos!=intermediate:
                if recover:return False
                raise ValueError(f"Nieoczekiwana pozycja {tuple(pos)}; sprawdź przejście.")
            await asyncio.sleep(.01)
        return False

    async def step(self,target,door=False,validate=None):
        pos=list(self.state.game_state.position)
        if distance(pos,target)!=1:raise ValueError("Krok nie jest sąsiednim polem.")
        self.map_now()  # fail closed on missing/stale input before scheduling
        sent=await self.send(build_walk_packet(OFFSETS[target[0]-pos[0],target[1]-pos[1]]),
                        lambda:list(self.state.game_state.position)==pos and step_clear(self.map_now()['cells'],pos,target,self.blocked,door=door) and (validate is None or validate()),defer_movement=not door)
        if not sent:raise MovementDeferred('Pole zmieniło się — ponownie sprawdzam drogę; walka aktywna.')
        return await self._confirm_walk(target, pos, recover=not door)

    def _finish_walk_timing(self, outcome):
        if self._map_after_walk is not None:
            self._map_after_walk.report(outcome)
            self._map_after_walk = None

    async def _confirm_walk(self, target, origin, timeout=2.5, intermediate=None, recover=False):
        self._finish_walk_timing("no_following_map")
        self._walk_number += 1
        timing = WalkTiming(self._walk_number, tuple(target))
        history = getattr(getattr(self.state, "game_state", None), "movement_history", None)
        timing.history = history
        if history:
            monitor = getattr(self.state, "passive_map_monitor", None)
            sample = getattr(monitor, "_result", None)
            history.request("cavebot", origin, target, sample if sample and 0 <= time.time()-sample.get("captured_at",0)<.8 else None)
        # Delivery has returned. Measure the executor's actual confirmation
        # wait, including an already available position, on Windows QPC.
        started = perf_counter()
        outcome = "position_interrupted"
        interruption = None
        try:
            arrived = await self.wait_position(target, origin, timeout, intermediate, recover)
            outcome = "position_confirmed" if arrived else "position_unconfirmed"
            if not arrived and self._route_command and self._route_attempt and self._route_attempt.get('cancelled'):
                outcome = "position_cancelled"
            return arrived
        except (ValueError, asyncio.CancelledError) as exc:
            interruption = str(exc) or 'Anulowano zadanie ruchu.'
            raise
        finally:
            self._observe_report('interrupted', target, outcome, interruption)
            if recover:
                self._uncertain_walk_origin = tuple(origin) if outcome in {"position_unconfirmed", "position_interrupted"} else None
            timing.position_ms = (perf_counter() - started) * 1000
            if history:
                history.route_result("route_position", outcome, target, timing.position_ms)
            if outcome == "position_confirmed":
                self._map_after_walk = timing
            else:
                timing.report(outcome)

    async def use_route_tool(self, edge, item, ready, missing_tool):
        from passive_map_win32 import ReadOnlyClient
        from client_player_state import ClientPlayerReader
        from client_equipment import EquipmentReader

        tools = transition_tools(edge)
        source = ReadOnlyClient()
        try:
            reader = EquipmentReader(ClientPlayerReader(source, self.state.game_state.player_id), tools)
            location = tool_location(await asyncio.to_thread(reader.snapshot), tools)
            if not location:
                raise ValueError(missing_tool)
            where, tool, stack = location
            def valid():
                return (list(self.state.game_state.position) == edge['origin'] and ready()
                        and tool_location(reader.snapshot(), [tool]) == location)
            sent = await self.send(build_use_item_ex_packet(*where,tool,stack,*edge['entry'],item['stack'],target_item_id=item['id']),valid)
            if sent and edge.get('action') == 'clear_web':
                history = getattr(self.state.game_state, 'movement_history', None)
                if history:
                    history.web_tool('cavebot', edge['entry'], tool, item['id'], item['stack'], {'items':[item]})
        finally:
            source.close()

    async def uncover_tool_target(self, edge, sample, tile):
        target, origin = edge['entry'], edge['origin']
        body = covering_body(tile)
        if not body:
            self._pending_body_move = None
            return
        if self._pending_body_move == target:
            raise MovementDeferred('Czekam na potwierdzenie odsunięcia ciała z pola narzędzia.')

        def drop_site(c):
            p = [c['x'], c['y'], c['z']]
            return (distance(p, target) == 1 and distance(p, origin) <= 1
                    and walkable(c) and not c.get('stairs') and not c.get('navigation')
                    and not c.get('transitions') and not c.get('server_blocking')
                    and len(c['items']) == 1 and 'ground' in c['items'][0]['flags'])

        sites = [c for c in sample['cells'] if drop_site(c)]
        if not sites:
            raise MovementDeferred('Brak wolnego pola na odsunięcie ciała.')
        drop = min(([c['x'],c['y'],c['z']] for c in sites), key=lambda p: (p == origin, p))
        def still_valid():
            cells = self.map_now()['cells']
            src = next((c for c in cells if [c['x'],c['y'],c['z']] == target), None)
            return (list(self.state.game_state.position) == origin and src and not src.get('occupants')
                    and src['items'] == tile['items'] and covering_body(src) == body
                    and any([c['x'],c['y'],c['z']] == drop and drop_site(c) for c in cells))
        if await self.send(build_move_item_packet(target, body['id'], body['stack'], drop, 1), still_valid):
            self._pending_body_move = list(target)
        raise MovementDeferred('Sprawdzam pole po odsunięciu ciała.')

    async def clear_web(self, edge, sample):
        if not supported_transition(edge) or list(self.state.game_state.position) != edge['origin']:
            raise ValueError('Sprawdź pole podejścia do pajęczyny.')
        target = edge['entry']
        def read_tile(current):
            return next((c for c in current['cells'] if [c['x'],c['y'],c['z']] == target), None)
        def vacant(tile):
            return tile and not tile.get('occupants', 0)

        tile = read_tile(sample)
        if tile and not vacant(tile):
            raise MovementDeferred('Pole pajęczyny jest zajęte.')
        if not web_passable(tile):
            obj = cuttable_web(tile)
            if obj is None:
                raise ValueError('Pole pajęczyny nie ma potwierdzonej warstwy do przecięcia.')
            await self.uncover_tool_target(edge, sample, tile)
            def ready():
                current = read_tile(self.map_now())
                return vacant(current) and not covering_body(current) and cuttable_web(current) == obj
            await self.use_route_tool(edge, obj, ready, 'Brak AiO 8899 lub 8900 w ekwipunku lub otwartym plecaku.')
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                tile = read_tile(await self.fresh_map())
                if web_passable(tile):
                    break
                await asyncio.sleep(.03)
            else:
                raise WebCutUnconfirmed('Po użyciu AiO mapa nie potwierdziła wolnego pola pajęczyny.')
        if list(self.state.game_state.position) != edge['origin']:
            raise MovementDeferred('Pozycja zmieniła się przy pajęczynie; ponownie sprawdzam trasę.')
        if not await self.step(target, validate=lambda:web_passable(read_tile(self.map_now()))):
            raise MovementDeferred('Czekam na potwierdzenie kroku przez pole pajęczyny.')

    async def dig_transition(self,edge,sample):
        if not supported_transition(edge):raise ValueError('Brak potwierdzonego odkopania i zejścia.')
        origin=list(self.state.game_state.position);entry=edge['entry'];dest=edge['exit']
        closed,opened=edge['item_id'],edge['prepared_item_id']
        if origin!=edge['origin']:raise ValueError('Podejście do dziury zmieniło się.')
        def read_hole(current):
            tile=next((c for c in current['cells'] if [c['x'],c['y'],c['z']]==entry),None)
            if not tile:
                raise InputsUnavailable('Brak aktualnego odczytu dziury.')
            ids={i['id'] for i in tile['items']}
            if (closed in ids)==(opened in ids):raise ValueError('Stan dziury nie pasuje do wyuczonego przejścia.')
            return tile,opened in ids
        tile,is_open=read_hole(sample)
        if tile.get('occupants',0)>tile.get('occupant_types',{}).get('self',0):
            raise MovementDeferred('Pole dziury jest zajęte. Czekam na zwolnienie przejścia.')
        if not is_open:
            await self.uncover_tool_target(edge, sample, tile)
            obj=next(i for i in tile['items'] if i['id']==closed)
            def ready_to_dig():
                current,open_now=read_hole(self.map_now())
                return not open_now and not current.get('occupants') and current['items']==tile['items']
            await self.use_route_tool(edge,obj,ready_to_dig,
                'Brak łopaty 3457 lub AiO 8899/8900 w ekwipunku i otwartych plecakach. Załóż narzędzie albo otwórz jego plecak.')
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                tile,is_open=read_hole(await self.fresh_map())
                if is_open:break
                await asyncio.sleep(.03)
            if not is_open:raise ValueError('Dziura nie potwierdziła odkopania; wstrzymano trasę bez ponawiania.')
        def ready_to_enter():
            current,opened_now=read_hole(self.map_now())
            return opened_now and not current.get('occupants')
        await self.send(build_walk_packet(OFFSETS[entry[0]-origin[0],entry[1]-origin[1]]),ready_to_enter)
        if not await self._confirm_walk(dest,origin,4,entry):raise ValueError('Zejście nie potwierdziło wyjścia; wstrzymano trasę.')

    async def rope_transition(self,edge,sample):
        if edge['action']!='use_with' or not supported_transition(edge):
            raise ValueError('Brak potwierdzonego użycia rope/AiO i wyjścia piętro wyżej.')
        origin=list(self.state.game_state.position);entry=edge['entry'];dest=edge['exit']
        if origin!=edge['origin']:raise ValueError('Podejście do rope spotu zmieniło się.')
        def read_spot(current):
            tile=next((c for c in current['cells'] if [c['x'],c['y'],c['z']]==entry),None)
            if not tile or tile.get('occupants',0)>tile.get('occupant_types',{}).get('self',0):
                raise ValueError('Brak odczytu rope spotu lub pole jest zajęte.')
            items=[i for i in tile['items'] if i['id']==edge['item_id']]
            if len(items)!=1:raise ValueError('Obiekt rope spotu nie pasuje do wyuczonego przejścia.')
            return tile,items[0]
        tile,obj=read_spot(sample)
        from passive_map_win32 import ReadOnlyClient
        from client_player_state import ClientPlayerReader
        from client_equipment import EquipmentReader
        source=None
        try:
            source=ReadOnlyClient();tools=transition_tools(edge)
            reader=EquipmentReader(ClientPlayerReader(source,self.state.game_state.player_id),tools)
            inventory=await asyncio.to_thread(reader.snapshot);location=tool_location(inventory,tools)
            if not location:raise ValueError('Brak rope 3003 lub AiO 8899/8900 w ekwipunku i otwartych plecakach. Załóż narzędzie albo otwórz jego plecak.')
            where,tool,stack=location
            def ready_to_use():
                fresh,_=read_spot(self.map_now())
                return (list(self.state.game_state.position)==origin and fresh['items']==tile['items']
                        and tool_location(reader.snapshot(),[tool])==location)
            self.reason=f'Przejście rope/AiO: {tuple(entry)} → {tuple(dest)}.'
            await self.send(build_use_item_ex_packet(*where,tool,stack,*entry,obj['stack'],target_item_id=obj['id']),ready_to_use)
            if not await self.wait_position(dest,origin,4):
                raise ValueError('Rope/AiO nie potwierdziło wyjścia; wstrzymano trasę bez ponawiania użycia.')
        finally:
            if source:source.close()

    async def transition(self,edge,sample):
        if edge['action']=='clear_web':return await self.clear_web(edge,sample)
        if edge['action']=='use_with_then_step':return await self.dig_transition(edge,sample)
        if edge['action']=='use_with':return await self.rope_transition(edge,sample)
        origin=list(self.state.game_state.position);entry=edge['entry'];dest=edge['exit']
        tile=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==entry),None)
        if not tile or distance(origin,entry)>1:raise ValueError("Brak świeżego odczytu wejścia przejścia.")
        features=tile.get('navigation',[]);opened=any(f.get('door_state_hint')=='open' for f in features)
        if any((f.get('required_level') or 0)>self.state.game_state.level for f in features):raise ValueError("Poziom postaci jest zbyt niski dla tej bramki.")
        ids={i['id'] for i in tile['items']};item=edge.get('item_id')
        matches=item in ids if item is not None else bool(edge.get('entry_items')) and set(edge['entry_items'])<=ids
        if not matches and not (opened and edge['action'] in {'use','use_then_step'} and edge.get('open_item_id') in ids):raise ValueError("Obiekt przejścia zmienił się; potrzebny nowy odczyt i plan.")
        if tile.get('occupants',0)>tile.get('occupant_types',{}).get('self',0):raise ValueError("Wejście przejścia jest zajęte.")
        def same_entry():
            fresh=next((c for c in self.map_now()['cells'] if [c['x'],c['y'],c['z']]==entry),None)
            return bool(fresh and fresh['items']==tile['items'] and fresh.get('occupants',0)<=fresh.get('occupant_types',{}).get('self',0))
        if edge['action']=='step':
            if distance(origin,entry)!=1:raise ValueError("Nieprawidłowe wejście schodów/teleportu.")
            if any(f['kind'] in {'door','level_gate'} for f in features):
                if not opened:raise ValueError('Drzwi są zamknięte; brakuje potwierdzonego użycia w tej trasie.')
                for target in (entry,dest):
                    if list(self.state.game_state.position)==target:continue
                    await self.fresh_map()
                    if not await self.step(target,door=target==entry):raise ValueError('Brak potwierdzenia kroku przez drzwi.')
                return
            await self.send(build_walk_packet(OFFSETS[entry[0]-origin[0],entry[1]-origin[1]]),same_entry)
            if not await self._confirm_walk(dest,origin,4,entry):raise ValueError("Przejście nie potwierdziło wyjścia; nie ponawiam użycia.")
            return
        if not opened:
            obj=next((i for i in tile['items'] if i['id']==item),None)
            if not obj:raise ValueError("Nieznany przedmiot do użycia na przejściu.")
            await self.send(build_use_item_packet(*entry,item,obj['stack'],0),same_entry)
            if edge['action']=='use':
                if not await self.wait_position(dest,origin,4,entry):raise ValueError("Użycie nie potwierdziło wyjścia; wstrzymano trasę.")
                return
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                current=await self.fresh_map()
                door=next((c for c in current['cells'] if [c['x'],c['y'],c['z']]==entry),None)
                if door and door['kind']=='clear' and any(f.get('door_state_hint')=='open' for f in door.get('navigation',[])):break
                await asyncio.sleep(.05)
            else:raise ValueError("Drzwi nie potwierdziły otwarcia.")
        for target in (entry,dest):
            if list(self.state.game_state.position)==target:continue
            await self.fresh_map()
            if not await self.step(target,door=target==entry):raise ValueError("Nie udało się przejść przez otwarte drzwi.")

    def route_boundary(self,sample):
        if self.index>=len(self.path)-1:return True
        pos=list(self.state.game_state.position);target=self.path[self.index+1]
        if any(e['origin']==pos and e['exit']==target for e in self.edges):return True
        # Open doors need no use packet but remain room boundaries. Also look
        # one step ahead so we stop before standing in the doorway itself.
        points=self.path[self.index+1:self.index+3]
        for tile in sample['cells']:
            if [tile['x'],tile['y'],tile['z']] not in points:continue
            if any(f['kind'] in {'door','level_gate'} or
                   f['kind']=='passage' and f.get('behavior_hint') in {'use_enters','open_then_step'} for f in tile.get('navigation',[])):return True
        return False

    async def run(self):
        self.guard()
        if not self.path or (not self.combat_only and distance(self.state.game_state.position,self.path[self.index])!=0):
            raise ValueError("Pozycja zmieniła się podczas pauzy. Uruchom trasę od początku.")
        self.generation+=1;self.status='running';self.reason='Nawigacja po trasie.'
        self.save_session_cursor()
        if self.combat.manual_override:self.combat.sent_target=0
        self.combat.manual_override=False
        proxy=self.state.game_proxy
        def manual(opcode,reader):
            if opcode==ClientOpcode.ATTACK:
                try:cid=reader.read_u32()
                except Exception:return
                if cid:
                    self.combat.accept_manual_target(cid)
                    return
            if self.combat_only and opcode not in {ClientOpcode.ATTACK,ClientOpcode.CANCEL_ATTACK}:return
            if opcode in MANUAL_MOVES:
                self.motion_generation+=1;self.manual_until=time.monotonic()+.3
                self.recovery_next_at=0;self.reason='Ręczny ruch — wznowię trasę z aktualnej pozycji; walka aktywna.'
                return
            world_use=False
            if opcode in {ClientOpcode.USE_ITEM,ClientOpcode.USE_ITEM_EX}:
                try:world_use=reader.read_position()[0]!=65535
                except Exception:pass
            if opcode in MANUAL_MOVES or world_use or opcode in {ClientOpcode.ATTACK,ClientOpcode.CANCEL_ATTACK}:
                self.combat.manual_override=opcode in {ClientOpcode.ATTACK,ClientOpcode.CANCEL_ATTACK}
                if self.combat.manual_override:self.combat.sent_target=0
                self.pause('Ręczny ruch, atak lub użycie obiektu — V2 wstrzymany.')
        proxy.register_client_packet_callback(manual)
        from session_lifetime import SessionLease
        from automation_domains import CavebotDomains
        self.session_lease=SessionLease(self.state)
        self.domains=CavebotDomains(self)
        from potions import SenzuUsage
        usage = SenzuUsage()
        self.state.game_state.senzu_usage = usage
        if not self.combat_only:
            from cavebot_history import RunHistory
            try:
                self.reporter = RunHistory(self, self._resuming_report or self.index > self.loop_start)
                self._observe_report('sample', True)
            except Exception:
                log.exception('Could not start Cavebot history')
        try:
            await self.domains.run()
        except ValueError as exc:self.pause(str(exc))
        except asyncio.CancelledError:
            if self.status=='running':self.pause('Nawigacja zatrzymana.')
            raise
        except Exception:
            self.pause('Błąd wykonania trasy; nawigacja wstrzymana.')
            raise
        finally:
            self._observe_report('finish', False, self.reason)
            self._finish_walk_timing("session_ended")
            usage.finish()
            proxy.unregister_client_packet_callback(manual)
            await self.combat.cancel_attack()

    async def _navigation_loop(self):
        manual_position=list(self.state.game_state.position)
        while self.status=="running":
            if self.domains.movement_state=='complete':
                await asyncio.sleep(.05);continue
            self.reason='Dojście do najbliższego osiągalnego waypointu.' if self.index<self.loop_start else 'Nawigacja po trasie.'
            sample=await self.fresh_map();pos=list(self.state.game_state.position)
            if self.navigation_hold:
                if (self._held_web and sample.get('center')==pos and self.index+1<len(self.path)
                        and self._held_web==(pos,self.path[self.index+1])):
                    tile=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==self._held_web[1]),None)
                    if web_passable(tile):
                        self.retry_navigation()
                        continue
                if (self.index+1 < len(self.path) and self.path[self.index] != pos
                        and self.path[self.index+1] == pos and sample.get('center') == pos
                        and any(edge['origin'] == self.path[self.index] and edge['exit'] == pos
                                for edge in self.edges)):
                    # A late arrival is positive evidence. Accept the completed
                    # transition without using the tool again or retracing it.
                    self.retry_navigation()
                    self.save_session_cursor()
                    continue
                self.domains.movement_state="held"
                if self.status!='running' or not self.navigation_hold:continue
                self.reason='Ruch wstrzymany: '+self.navigation_hold+' Walka aktywna.'
                await asyncio.sleep(.05);continue
            if not self.combat_only and time.monotonic()<self.manual_until:
                if pos!=manual_position:self.manual_until=time.monotonic()+.3
                manual_position=pos
                self.domains.movement_state="waiting"
                await asyncio.sleep(.05);continue
            if not self.combat_only and pos!=self.path[self.index]:
                if self.combat.config['enabled'] and self.combat.anchor is not None:
                    # Chase/retreat owns this temporary excursion; its return
                    # intent uses this same executor, never a competing walker.
                    await self.domains.consume_movement(sample,False)
                    await asyncio.sleep(.01);continue
                if not self.recover_local(sample):
                    self.domains.movement_state="waiting"
                    self.reason='Powrót do trasy: czekam na wolne pole; walka aktywna.'
                    await asyncio.sleep(.05)
                continue
            self.skip_repeated_sweep()
            self.tolerate_waypoint(sample)
            at_transition=self.route_boundary(sample)
            if self.combat_only:self.path=[pos];self.index=0
            try:
                if await self.domains.consume_movement(sample,at_transition):continue
            except MovementDeferred as exc:
                self.reason=str(exc);await asyncio.sleep(.05);continue
            if self.combat_only:
                self.reason=('Ręcznie wybrany cel poza warunkami walki — czekam.' if self.combat.manual_target else
                             'Czekam na pasującego potwora. Ruch pozostaje ręczny.')
                await asyncio.sleep(.05);continue
            pos=list(self.state.game_state.position)
            if pos!=self.path[self.index]:
                self.recover_local(await self.fresh_map());continue
            if self.index>=len(self.path)-1:
                if self.loop and pos != self.path[self.loop_start]:
                    anchor = self.path[self.loop_start]
                    if await self.reconnect_route_step(pos, anchor, closing=True):
                        continue
                    self.reason = f'Brak potwierdzonego domknięcia do {tuple(anchor)}.'
                    await asyncio.sleep(1)
                    continue
                self._observe_report('finish', True, 'okrążenie' if self.loop else 'koniec trasy')
                self.laps+=1
                if not self.loop:
                    journal = getattr(self.state.game_state, "engine_journal", None)
                    if journal is not None:
                        journal.save_route(None)
                    self.domains.movement_state='complete';self.reason='Trasa zakończona. Powtarzanie wyłączone.'
                    if not self.combat.config['enabled']:self.status='complete';break
                    await asyncio.sleep(.05);continue
                self.index=self.loop_start;self.variation_until=self.index;self.mark_waypoint()
                self._observe_report('sample', True)
                continue
            # Combat may have awaited attack/spell pacing; use current terrain.
            sample=await self.fresh_map()
            if not self.combat.placement:self.vary_leg(sample)
            target=self.path[self.index+1]
            edge=next((e for e in self.edges if e['origin']==pos and e['exit']==target),None)
            if edge is None and distance(pos,target)==1:
                # A web can reappear on a route saved while the field was clear.
                next_tile=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==target),None)
                web = cuttable_web(next_tile)
                if web:
                    edge=web_edge(pos,target,web['id'])
            if edge:
                self._route_command=True;self._route_boundary=True
                self.save_session_cursor(pending=target)
                try:await self.transition(edge,sample)
                except MovementDeferred as exc:
                    self.reason=str(exc);await asyncio.sleep(.05);continue
                except ValueError as exc:
                    # Keep authorized combat alive, but never blindly repeat
                    # a tool/door operation whose outcome is uncertain.
                    # Once a transition started, an observation gap is an
                    # uncertain operation, not permission to use the tool again.
                    if edge['action']=='clear_web':
                        history=getattr(self.state.game_state,'movement_history',None)
                        if history:
                            tile=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==target),None)
                            history.web_blocked(pos,target,tile,str(exc))
                    if not self.combat.config['enabled']:raise ValueError(str(exc)) from exc
                    self._held_web=(list(pos),list(target)) if isinstance(exc,WebCutUnconfirmed) else None
                    self.navigation_hold=str(exc);self.logs.append('Ruch: '+str(exc));continue
                finally:self._route_command=False;self._route_boundary=False
            else:
                if distance(pos, target) > 1:
                    if await self.reconnect_route_step(pos, target):
                        continue
                    self.reason = f'Brak potwierdzonego połączenia trasy {tuple(pos)} → {tuple(target)}.'
                    await asyncio.sleep(1)
                    continue
                now=time.monotonic();self.blocked={p:t for p,t in self.blocked.items() if t>now}
                tiles={(c['x'],c['y'],c['z']):c for c in sample['cells']}
                if not step_clear(sample['cells'],pos,target,self.blocked):
                    # Rejoin within the next eight ordinary steps, never skip a transition.
                    candidates=[]
                    limit=min(len(self.path),self.index+9,self.loop_start+1 if self.index<self.loop_start else len(self.path))
                    limit=self.checkpoint_limit(limit)
                    for j in range(self.index+1,limit):
                        p=self.path[j]
                        if p[2]!=pos[2] or any(e['origin']==self.path[j-1] and e['exit']==p for e in self.edges):break
                        # A route can revisit our current tile. It provides no next
                        # step and cannot serve as a detour around the blocked tile.
                        if p != pos and walkable(tiles.get(tuple(p))):candidates.append(j)
                    found=False
                    for j in reversed(candidates):
                        local=detour(sample['cells'],pos,self.path[j],self.blocked)
                        if local:
                            self.replace_path(self.index+1,j,local[1:]);target=self.path[self.index+1];found=True;break
                    if not found:
                        self.describe_blockage(sample,target)
                        if (tuple(target) not in self.blocked and not self.combat.config['enabled']
                                and not any(t.get('occupants',0) for t in sample['cells'])):
                            raise ValueError('Brak widocznego obejścia przeszkody; sprawdź trasę.')
                        await asyncio.sleep(.1);continue
                self._route_command=True;self._route_boundary=at_transition
                self.save_session_cursor(pending=target)
                try:arrived=await self.step(target)
                except MovementDeferred as exc:
                    self.reason=str(exc);await asyncio.sleep(.05);continue
                finally:self._route_command=False;self._route_boundary=False
                if not arrived:
                    self.failures+=1
                    self.blocked[tuple(target)]=time.monotonic()+min(self.failures, 10)
                    cancelled = bool(self._route_attempt and self._route_attempt.get('cancelled'))
                    self.describe_blockage(await self.fresh_map(),target,cancelled=cancelled)
                    if self.failures>=3:self.reason='Brak potwierdzenia kroku — sprawdzam obejście; walka aktywna.'
                    continue
            self.index+=1;self.mark_waypoint();self.failures=0;self.blockage=None;self._pending_body_move=None
            self.save_session_cursor()
