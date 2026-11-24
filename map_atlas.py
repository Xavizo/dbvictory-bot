"""Local passive atlas and bounded preview-only route planning. Never sends actions."""
from collections import OrderedDict
from route_identity import navigation_revision
from hunt_areas import sector
from contextlib import closing
import heapq
import hashlib
from itertools import groupby, islice
from functools import lru_cache
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from transition_learning import position,gate_entry_record,ordinary_door_variants,ordinary_door_item
from cavebot_combat import validate_combat
from navigation_tools import WEB_ITEM_IDS, cuttable_web, has_cut_web, tool_kind, web_edge
from map_navigation import is_step_floor_tile, movable_container_cover
from recorded_rope import recorded_rope_edges
from world_minimap import tile_color, save_colors

CACHE_TILES=4096
PAGE_SIZE=16
CACHE_PAGES=128
MAX_EXPANSIONS=40000
MAX_ANCHORS=2048
KINDS={"clear","blocked","avoid","no_ground"}
log = logging.getLogger(__name__)


def database_busy(exc):
    return (getattr(exc, "sqlite_errorcode", 0) & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def validate_labels(labels,count):
    if labels is None:return ['']*count
    if not isinstance(labels,list) or len(labels)!=count or any(not isinstance(v,str) or len(v)>60 or any(ord(c)<32 for c in v) for v in labels):
        raise ValueError('Etykiety waypointów: do 60 znaków na punkt.')
    return [v.strip() for v in labels]


def validate_tags(tags):
    colors = ('mint', 'blue', 'amber', 'rose', 'violet')
    if not isinstance(tags, list) or len(tags) > 16:
        raise ValueError("Szkic może mieć do 16 etykiet.")
    result = []
    for tag in tags:
        if not isinstance(tag, dict) or tag.get('color') not in colors:
            raise ValueError("Wybierz kolor etykiety.")
        text = tag.get('text')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 80 or any(ord(c) < 32 for c in text):
            raise ValueError("Treść etykiety musi mieć od 1 do 80 znaków.")
        result.append({'text': text.strip(), 'color': tag['color']})
    return result


def validate_links(links,points):
    if links is None:return []
    if not isinstance(links,list) or len(links)>len(points):raise ValueError('Nieprawidłowa lista przejść.')
    seen=set();result=[]
    for link in links:
        if not isinstance(link,dict) or set(link)!={'from_index','id'}:raise ValueError('Nieprawidłowe przejście.')
        index,identity=link['from_index'],link['id']
        if type(index)is not int or not 0<=index<len(points)-1 or index in seen or not isinstance(identity,str) or not 1<=len(identity)<=64:
            raise ValueError('Nieprawidłowe przypisanie przejścia do waypointów.')
        seen.add(index);result.append(dict(link))
    return sorted(result,key=lambda r:r["from_index"])


def transition_from_origin(row,origin,observations):
    """A learned entry/exit has a stable ID; its latest approach is only a hint.

    Bind explicitly selected/recorded uses and steps to an adjacent approach.
    Known rope/dig functions and use-enters gates may keep an adjacent recorded
    approach; unknown sequences and conflicting exit mechanisms are rejected.
    """
    if row.get('origin')==origin:return row
    entry=row.get('entry');action=row.get('action')
    kind=tool_kind(row);gate=gate_entry_record(row) if row.get('object_change') else None
    if (row.get('evidence')!='observed' or not position(entry) or
            (action not in {'step','use'} and not kind) or
            (row.get('object_change') and gate is None) or row.get('reason')=='door_crossing'):return None
    if origin[2]!=entry[2]:return None
    delta=max(abs(origin[0]-entry[0]),abs(origin[1]-entry[1]))
    if delta>1 or action=='step' and delta!=1 or action=='use' and row.get('item_id') is None:return None
    if gate and delta!=1:return None
    rebound={**row,'origin':list(origin)}
    if kind and tool_kind(rebound)!=kind:return None
    keys=('entry','action','item_id','prepared_item_id')+(() if kind else ('tool_id',))
    exits={tuple(r['exit']) for r in observations if r.get('evidence')=='observed' and
           all(r.get(k)==row.get(k) for k in keys) and (not kind or tool_kind(r)==kind)}
    if exits!={tuple(row['exit'])}:return None
    return rebound


def trace_links(points,observations,trace):
    """Authorize only transitions actually crossed in a recorded position trace."""
    pairs={(tuple(a),tuple(b)) for a,b in zip(trace,trace[1:])}
    triples={(tuple(a),tuple(b),tuple(c)) for a,b,c in zip(trace,trace[1:],trace[2:])}
    result=[]
    for index,(a,b) in enumerate(zip(points,points[1:])):
        matches=[]
        for source in observations:
            row=gate_entry_record(source) if source.get('object_change') else source
            if not row or not row.get('id') or row.get('evidence')!='observed' or row.get('exit')!=b:continue
            row=transition_from_origin(row,a,observations)
            if row is None:continue
            direct=(tuple(a),tuple(b)) in pairs
            door_step=row.get('action')=='step' and row.get('reason')=='door_crossing'
            via=(row.get('action')=='use_then_step' or door_step) and (tuple(a),tuple(row['entry']),tuple(b)) in triples
            if direct or via:matches.append(row)
        identities={r['id'] for r in matches}
        if len(identities)==1:result.append({'from_index':index,'id':matches[0]['id']})
        elif matches:
            # The same hole may have been recorded both already open and
            # after digging. Prefer the verified conditional dig edge: its
            # executor skips the tool when the prepared item is already there.
            digs=[r for r in matches if r['action']=='use_with_then_step']
            dig_signatures={(tuple(r['entry']),r.get('item_id'),r.get('prepared_item_id')) for r in digs}
            if digs and len(dig_signatures)==1 and all(r.get('tool_id') in {3457,8899,8900} and r.get('prepared_item_id') for r in digs):
                prepared=digs[0]['prepared_item_id']
                if all(r in digs or r['action']=='step' and r['entry']==digs[0]['entry'] and
                       (r.get('item_id')==prepared or r.get('item_id') is None and r.get('entry_items')==[prepared]) for r in matches):
                    result.append({'from_index':index,'id':min(r['id'] for r in digs)});continue
            # Loot and decorations can change entry_items and therefore the
            # learner's record ID. They do not change a specifically used
            # ladder. Preserve action, object, tool and exact endpoints.
            signatures={(r.get('action'),tuple(r.get('entry') or []),r.get('item_id'),r.get('prepared_item_id'),
                         None if r.get('action')=='use_with_then_step' or tool_kind(r)=='rope' else r.get('tool_id')) for r in matches}
            action=matches[0]['action']
            equivalent=(action in {'use','use_then_step'} and matches[0].get('item_id') is not None
                        or action=='step' and bool(matches[0].get('entry_items')) and len({tuple(sorted(r.get('entry_items',[]))) for r in matches})==1
                        or action=='use_with_then_step' and all(r.get('tool_id') in {3457,8899,8900} for r in matches)
                        or action=='use_with' and all(tool_kind(r)=='rope' for r in matches))
            if len(signatures)==1 and equivalent:result.append({'from_index':index,'id':sorted(identities)[0]})
    return result


def review_recorded_actions(recording,transitions):
    """Match recorded world uses to confirmed, ordered transition occurrences.

    A cast/use request alone is not success. Require the acknowledged position
    trace after that request and a matching planned object/action. Nothing here
    changes the recording, atlas or game state.
    """
    trace=recording.get('observed_path') or []
    interactions=[];used=set();last_transition=-1
    keys=('type','item_id','target_item_id','x','y','z','to_x','to_y','to_z','player_pos','t')
    for wp in recording.get('waypoints',[]):
        if wp.get('type') not in {'use_item','use_item_ex'} or (wp.get('x',65535)==65535 and wp.get('to_x',65535)==65535):continue
        action={k:wp.get(k) for k in keys};action['matched_transition']=None
        target=[wp.get(k) for k in (('x','y','z') if wp['type']=='use_item' else ('to_x','to_y','to_z'))]
        for index,row in enumerate(transitions):
            if index in used or index<last_transition or target!=row['entry'] or wp.get('player_pos')!=row['origin']:continue
            if wp['type']=='use_item':
                if row['action'] not in {'use','use_then_step'} or wp.get('item_id')!=row.get('item_id'):continue
            else:
                if row['action'] not in {'use_with','use_with_then_step','clear_web'} or wp.get('item_id') not in (row.get('tool_ids') or [row.get('tool_id')]):continue
                if wp.get('target_item_id') is not None and wp['target_item_id']!=row.get('item_id'):continue
            confirmed=False
            for start in range(len(trace)-1):
                for length in (2,3):
                    part=trace[start:start+length]
                    if len(part)!=length or any(p.get('break_before') for p in part[1:]):continue
                    positions=[p.get('pos') for p in part]
                    expected=[row['origin'],row['exit']] if length==2 else [row['origin'],row['entry'],row['exit']]
                    if positions!=expected or length==3 and row['action'] not in {'use_then_step','use_with_then_step'}:continue
                    if all(type(t) in (int,float) for t in (part[0].get('t'),part[-1].get('t'),wp.get('t'))) and part[0]['t']<=wp['t']<=part[-1]['t']:
                        confirmed=True;break
                if confirmed:break
            if confirmed:
                action['matched_transition']=index;used.add(index);last_transition=index;break
        interactions.append(action)
    return interactions


def near_loop_start(points):
    """Only a local same-floor closing leg may be planned automatically."""
    a,b=points[0],points[-1]
    return a[2]==b[2] and max(abs(a[0]-b[0]),abs(a[1]-b[1]))<=2


def remap_links(links,before,after):
    """Keep authorizations only for the same consecutive anchors, in order."""
    result=[];cursor=0
    for link in validate_links(links,before):
        i=link['from_index']
        for j in range(cursor,len(after)-1):
            if before[i:i+2]==after[j:j+2]:
                result.append({'from_index':j,'id':link['id']});cursor=j+1;break
    return result


def navigation_edges(tiles,observations):
    """Shared validated directed edges for preview, execution and the editor."""
    edges={};ambiguous={};door_pairs={};changes_by_entry={};tool_exits={}
    for r in observations:
        kind=tool_kind(r)
        if kind and r.get('evidence')=='observed':
            key=(tuple(r['entry']),kind,r.get('item_id'),r.get('prepared_item_id'))
            tool_exits.setdefault(key,set()).add(tuple(r['exit']))
        change=r.get('object_change')
        if change and r.get('evidence')=='observed' and r.get('entry'):
            door_pairs.setdefault(tuple(r['entry']),set()).add((change['closed_id'],change['open_id']))
            changes_by_entry.setdefault(tuple(r['entry']), []).append(change)
    normal_doors = {entry: model for entry, changes in changes_by_entry.items()
                    if (model := ordinary_door_variants(changes))}
    if isinstance(tiles,PagedTiles):
        # Learned entrances are sparse across many floors. Loading a whole tile
        # page for each one dominates the cost of editing an existing route.
        entries={tuple(r['entry']) for r in observations if r.get('entry') and r.get('evidence')=='observed'}
        entries.update((x+dx,y+dy,z) for x,y,z in normal_doors for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)))
        tiles.prefetch(entries)
    for r in observations:
        if r.get('object_change'):
            r=gate_entry_record(r)
            if r is None:continue
        if r.get("evidence")!="observed" or not r.get("entry"):continue
        kind=tool_kind(r)
        if kind and len(tool_exits.get((tuple(r['entry']),kind,r.get('item_id'),r.get('prepared_item_id')),()))!=1:continue
        origin,entry,dest=tuple(r["origin"]),tuple(r["entry"]),tuple(r["exit"])
        if origin==dest:continue
        tile=tiles.get(entry,{})
        ids={i["id"] for i in tile.get("items",[])}
        pairs=door_pairs.get(entry,set())
        # A recorded walk through an open normal door remains valid when the
        # door is later closed, but only with independently observed opening.
        if r.get('object_change') and len(pairs)!=1:continue
        opened_id=next(iter(pairs))[1] if len(pairs)==1 and next(iter(pairs))[0]==r.get('item_id') else None
        model = normal_doors.get(entry)
        if model and origin[2] == entry[2] == dest[2]:
            closed_ids, door_open = model
            recorded_walk = (r['action'] == 'step' and r.get('reason') == 'door_crossing'
                             and door_open in r.get('entry_items', ()))
            recorded_use = r['action'] == 'use_then_step' and r.get('item_id') in closed_ids
            if recorded_walk or recorded_use:
                obj = ordinary_door_item(tile, closed_ids, door_open)
                if obj is None:
                    continue
                closed = obj['id'] if obj['id'] != door_open else closed_ids[0]
                opened_id = door_open
                r = {**r, 'action':'use_then_step', 'item_id':closed, 'door_closed_ids':closed_ids,
                     'automatic_door':recorded_walk or r.get('automatic_door', False)}
        if r.get("item_id") is not None:
            if r["item_id"] not in ids and r.get("prepared_item_id") not in ids and not (r['action'] in {'use','use_then_step'} and opened_id in ids):continue
        elif not r.get("entry_items") or not set(r["entry_items"])<=ids:continue
        signature=(origin,entry,r["action"],tool_kind(r) or r.get("tool_id"),r.get("item_id"),r.get('prepared_item_id'))
        ambiguous.setdefault(signature,set()).add(dest)
        edges.setdefault(origin,[]).append((dest,{**r,'open_item_id':opened_id},signature))
    edges={origin:[(dest,row) for dest,row,sig in values if len(ambiguous[sig])==1] for origin,values in edges.items()}
    for values in edges.values():
        for dest,row in values:
            if row['action']=='use_with_then_step':
                row['tool_ids']=sorted({r['tool_id'] for d,r in values if d==dest and r['action']==row['action']
                    and r['entry']==row['entry'] and r.get('item_id')==row.get('item_id')
                    and r.get('prepared_item_id')==row.get('prepared_item_id') and r.get('tool_id') in {3457,8899,8900}})
            elif tool_kind(row)=='rope':
                row['tool_ids']=sorted({r['tool_id'] for r in observations if r.get('evidence')=='observed'
                    and tool_kind(r)=='rope' and r['entry']==row['entry'] and r['exit']==row['exit']
                    and r.get('item_id')==row.get('item_id')})
    # A normal door does not choose a destination or change floors. Once its
    # opening mechanism is observed, known adjacent floor tiles define the
    # available exits. This does not generalize to level gates or holes.
    for entry, (closed_ids, opened) in normal_doors.items():
        obj = ordinary_door_item(tiles.get(entry, {}), closed_ids, opened)
        if obj is None:
            continue
        closed = obj['id'] if obj['id'] != opened else closed_ids[0]
        x,y,z=entry;neighbors=[]
        for dx,dy in ((1,0),(-1,0),(0,1),(0,-1)):
            p=(x+dx,y+dy,z);cell=tiles.get(p,{})
            if cell.get('kind') in {'clear','avoid'} and all(f['kind'] in {'rope_spot','ladder'} for f in cell.get('navigation',[])):neighbors.append(p)
        for origin in neighbors:
            for dest in neighbors:
                if origin==dest:continue
                # Each closed appearance may already be part of a saved sketch's link ID.
                identities = ['door-' + hashlib.sha256(repr((origin,entry,dest,i,opened)).encode()).hexdigest()[:20]
                              for i in closed_ids]
                row=dict(id=identities[0],link_aliases=identities[1:],origin=list(origin),entry=list(entry),
                         exit=list(dest),action='use_then_step',door_closed_ids=closed_ids,
                         item_id=closed,open_item_id=opened,entry_items=[closed,opened],automatic_door=True,
                         evidence='derived',direction='same_floor',count=1)
                edges.setdefault(origin,[]).append((dest,row))
    return edges


def decode_tile(row, web_definitions=None):
    x,y,z,raw,seen=row;cell=json.loads(raw)
    if (not isinstance(cell,dict) or not position([x,y,z]) or
        [cell.get(k) for k in ("x","y","z")]!=[x,y,z] or cell.get("kind") not in KINDS
        or not isinstance(cell.get("items"),list) or len(cell["items"])>16):
        raise ValueError("Nieprawidłowe pole w atlasie.")
    if cell["kind"] == "avoid" and movable_container_cover(cell["items"]):
        cell["kind"] = "clear"
    if web_definitions:
        if has_cut_web(cell['items'],web_definitions):
            cell['web_cut'] = True
        features = cell.get('navigation', [])
        for item in cell['items']:
            iid = item['id']
            if iid in web_definitions and not any(f.get('item_id') == iid and f.get('source') != 'client_hint' for f in features):
                features = [dict(web_definitions[iid], item_id=iid, stack=item['stack'], source='confirmed_id'),
                            *(f for f in features if f.get('item_id') != iid)]
                cell['navigation'] = features
    return (x,y,z),{**cell,"seen_at":seen}


class PagedTiles:
    """Small read cache; the entire persistent atlas is addressable on demand."""
    def __init__(self,path,recent=(),web_definitions=None):
        self.path=path;self.recent=dict(recent);self.pages=OrderedDict()
        self.web_definitions=web_definitions
    def prefetch(self,positions):
        missing=list(positions-self.recent.keys())
        if not missing:return
        with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=.5)) as db:
            for start in range(0,len(missing),250):
                batch=missing[start:start+250]
                values=','.join(['(?,?,?)']*len(batch))
                rows=db.execute(f"""WITH wanted(x,y,z) AS (VALUES {values})
                    SELECT t.x,t.y,t.z,t.data,t.seen FROM wanted JOIN tiles t USING(x,y,z)""",
                    [v for pos in batch for v in pos])
                self.recent.update(decode_tile(row,self.web_definitions) for row in rows)
    def get(self,key,default=None):
        key=tuple(key)
        if key in self.recent:return self.recent[key]
        x,y,z=key;page=(x//PAGE_SIZE,y//PAGE_SIZE,z)
        if page not in self.pages:
            left,top=page[0]*PAGE_SIZE,page[1]*PAGE_SIZE
            with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=.5)) as db:
                rows=db.execute("SELECT x,y,z,data,seen FROM tiles WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
                                (z,left,left+PAGE_SIZE-1,top,top+PAGE_SIZE-1)).fetchall()
            self.pages[page]=dict(decode_tile(row,self.web_definitions) for row in rows)
            if len(self.pages)>CACHE_PAGES:self.pages.popitem(last=False)
        self.pages.move_to_end(page)
        return self.pages[page].get(key,default)
    def __contains__(self,key):return self.get(key) is not None
    def __getitem__(self,key):
        value=self.get(key)
        if value is None:raise KeyError(key)
        return value


class MapAtlas:
    def __init__(self,directory,scope,*,web_definitions=None,web_definition=None):
        # Older callers supplied the definition for item 183 alone.
        self.web_definitions = web_definitions if web_definitions is not None else ({183: web_definition} if web_definition else {})
        self.scope=scope
        self.path=Path(directory).absolute()/f"map_atlas-{scope[:16]}.sqlite3"
        self.tiles=OrderedDict();self.total=0;self.floors=set();self.error=None;self.revision=0
        self.lock=threading.RLock()
        self.recorded_ropes={}
        self._pending = {}
        self._visited_sectors = set()
        self._pending_sectors = set()
        self._write_warning = None
        self._retry_at = 0.
        self._retry_open = False
        self._open()

    def _open(self):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=.2)) as db, db:
                db.execute("CREATE TABLE IF NOT EXISTS meta (scope TEXT NOT NULL)")
                row=db.execute("SELECT scope FROM meta").fetchone()
                if row is None:db.execute("INSERT INTO meta VALUES (?)",(self.scope,))
                elif row[0]!=self.scope:raise ValueError("scope")
                db.execute("CREATE TABLE IF NOT EXISTS recorded_ropes (id TEXT PRIMARY KEY, recording TEXT NOT NULL, edge TEXT NOT NULL)")
                self.recorded_ropes={key:json.loads(raw) for key,raw in db.execute("SELECT id,edge FROM recorded_ropes")}
                db.execute("CREATE TABLE IF NOT EXISTS tiles (x INT,y INT,z INT,data TEXT,seen REAL,PRIMARY KEY(x,y,z))")
                db.execute("CREATE INDEX IF NOT EXISTS tiles_floor_xy ON tiles(z,x,y)")
                db.execute("CREATE INDEX IF NOT EXISTS tiles_seen ON tiles(seen DESC)")
                db.execute("""CREATE TABLE IF NOT EXISTS world_pixels (
                    z INTEGER, x INTEGER, y INTEGER, color INTEGER NOT NULL, observed REAL NOT NULL,
                    revision INTEGER NOT NULL, PRIMARY KEY(z,x,y)) WITHOUT ROWID""")
                db.execute("CREATE INDEX IF NOT EXISTS world_pixel_revision ON world_pixels(z,revision)")
                db.execute("CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY,name TEXT,points TEXT,updated REAL)")
                if 'loop' not in {r[1] for r in db.execute('PRAGMA table_info(drafts)')}:
                    db.execute('ALTER TABLE drafts ADD COLUMN loop INTEGER NOT NULL DEFAULT 1')
                columns={r[1] for r in db.execute('PRAGMA table_info(drafts)')}
                if 'combat' not in columns:db.execute("ALTER TABLE drafts ADD COLUMN combat TEXT NOT NULL DEFAULT '{}'")
                if 'deleted' not in columns:db.execute('ALTER TABLE drafts ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
                if 'labels' not in columns:db.execute("ALTER TABLE drafts ADD COLUMN labels TEXT")
                if 'tags' not in columns:db.execute("ALTER TABLE drafts ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'")
                if 'links' not in columns:db.execute("ALTER TABLE drafts ADD COLUMN links TEXT NOT NULL DEFAULT '[]'")
                if "terrain" not in columns:db.execute("ALTER TABLE drafts ADD COLUMN terrain TEXT")
                db.execute("CREATE TABLE IF NOT EXISTS visited_sectors(x INT,y INT,z INT,PRIMARY KEY(x,y,z))")
                self._visited_sectors = set(db.execute("SELECT x,y,z FROM visited_sectors"))
                self.total=db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
                self.floors={r[0] for r in db.execute("SELECT DISTINCT z FROM tiles")}
                rows=db.execute("SELECT x,y,z,data,seen FROM tiles ORDER BY seen DESC LIMIT ?",(CACHE_TILES,)).fetchall()
                self.tiles=OrderedDict(decode_tile(row,self.web_definitions) for row in rows)
            self.error = None
            self._retry_open = False
            self._retry_at = 0.
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError) as exc:
            if database_busy(exc):
                self._retry_open = True
                self._retry_at = time.monotonic() + 1
                self.error = "Baza atlasu jest zajęta. Ponawiam odczyt."
                return
            self._retry_open = False
            self.tiles=OrderedDict();self.error="Atlas niedostępny lub nieprawidłowy. Plik nie zostanie nadpisany."

    def observe(self,sample):
        if sample.get("status")!="ready":return
        now=time.time();changed=False
        with self.lock:
            if self._retry_open and time.monotonic() >= self._retry_at:
                self._open()
            if self.error:return
            center = sample.get("center")
            player = (tuple(center) if position(center) and any(center[:2])
                      and not sample.get("preview") and not sample.get("stale") else None)
            for cell in sample["cells"]:
                if cell["kind"] not in KINDS:continue
                key=(cell["x"],cell["y"],cell["z"])
                if key == player and cell["kind"] != "no_ground":
                    visited = sector(player)
                    if visited not in self._visited_sectors:
                        self._pending_sectors.add(visited)
                safe={k:cell[k] for k in ("x","y","z","kind")}
                color = tile_color(cell["items"])
                if color is not None and not sample.get("stale") and not sample.get("preview"):
                    safe["minimap_color"] = color
                safe["items"]=[{"id":i["id"],"flags":list(i.get("flags",[])),"stack":i["stack"]} for i in cell["items"][:16]]
                if cell.get('web_cut') or has_cut_web(safe['items'],self.web_definitions):
                    safe['web_cut']=True
                safe["navigation"]=[{k:f[k] for k in ("kind","item_id","stack","source","activation_hint","door_state_hint","behavior_hint",
                    "direction_hint","observation_count","required_level","requirement_source","closing_policy","lock_hint","note") if k in f} for f in cell.get("navigation",[])[:16]]
                safe["transitions"]=[{k:r[k] for k in ("id","exit","action","direction","count") if k in r} for r in cell.get("transitions",[])[:32]]
                previous=self.tiles.get(key) or self._pending.get(key)
                if previous and {k:v for k,v in previous.items() if k!="seen_at"}==safe and now-previous["seen_at"]<30:continue
                self.tiles[key]={**safe,"seen_at":now};self.tiles.move_to_end(key)
                if len(self.tiles)>CACHE_TILES:self.tiles.popitem(last=False)
                self._pending[key]=self.tiles[key]
                changed=True
            if changed:self.revision+=1
            if (self._pending or self._pending_sectors) and time.monotonic() >= self._retry_at:
                self._flush_pending()

    def _flush_pending(self):
        # Keep the newest observation for every tile until COMMIT succeeds.
        batch = list(islice(self._pending, 1024)) if self._write_warning else list(self._pending)
        rows = []
        for key in batch:
            cell = self._pending[key]
            raw = json.dumps({k:v for k,v in cell.items() if k != "seen_at"}, separators=(",",":"))
            rows.append((*key, raw, cell["seen_at"]))
        try:
            with closing(sqlite3.connect(self.path.as_uri()+"?mode=rw", uri=True, timeout=.05)) as db, db:
                db.execute("PRAGMA max_page_count=2147483646")
                db.executemany("INSERT OR IGNORE INTO visited_sectors VALUES (?,?,?)", self._pending_sectors)
                added = db.executemany("INSERT OR IGNORE INTO tiles VALUES (?,?,?,?,?)",rows).rowcount
                db.executemany("UPDATE tiles SET data=?,seen=? WHERE x=? AND y=? AND z=?",
                               [(raw,seen,x,y,z) for x,y,z,raw,seen in rows])
                save_colors(db, (self._pending[key] for key in batch))
        except (OSError, sqlite3.Error) as exc:
            reason = getattr(exc, "sqlite_errorname", type(exc).__name__)
            if (getattr(exc,"sqlite_errorcode",0) & 0xff) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                log.error("Atlas storage unavailable (%s): %s", reason, exc)
                self.error = "Baza atlasu jest nieprawidłowa. Zapis został wstrzymany."
                return
            warning = f"Atlas ponowi zapis ({reason}). Nowe pola czekają w pamięci."
            if warning != self._write_warning:
                log.warning("Atlas write deferred (%s): %s", reason, exc)
            self._write_warning = warning
            self._retry_at = time.monotonic() + (1 if database_busy(exc) else 5)
            return
        for key in batch:
            del self._pending[key]
        self._visited_sectors.update(self._pending_sectors)
        self._pending_sectors.clear()
        self.total += added
        self.floors.update(key[2] for key in batch)
        if self._write_warning and not self._pending:
            log.info("Atlas pending tiles committed")
            self._write_warning = None
        self._retry_at = 0.

    def _paged_tiles(self):
        with self.lock:
            tiles = PagedTiles(self.path, self.tiles, self.web_definitions)
            tiles.recent.update(self._pending)
            return tiles

    def view(self,x,y,z,radius=20):
        if not position([x,y,z]) or type(radius)is not int or not 5<=radius<=32:raise ValueError("Nieprawidłowy obszar atlasu.")
        with self.lock:
            warning = self.error or self._write_warning
            try:
                if self.error:raise ValueError(self.error)
                with closing(sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=.5)) as db:
                    rows=db.execute("SELECT x,y,z,data,seen FROM tiles WHERE z=? AND x BETWEEN ? AND ? AND y BETWEEN ? AND ?",
                                    (z,x-radius,x+radius,y-radius,y+radius)).fetchall()
                cells=dict(decode_tile(row,self.web_definitions) for row in rows)
            except (sqlite3.Error,ValueError,TypeError,KeyError):
                cells={k:c for k,c in self.tiles.items() if k[2]==z and abs(k[0]-x)<=radius and abs(k[1]-y)<=radius}
                warning=self.error or "Nie udało się odczytać tego fragmentu atlasu. Pokazuję pola z pamięci."
            cells.update((k,c) for k,c in self._pending.items() if k[2]==z and abs(k[0]-x)<=radius and abs(k[1]-y)<=radius)
            return {"scope":self.scope,"center":[x,y,z],"radius":radius,"cells":list(cells.values()),"floors":sorted(self.floors),
                    "total":self.total,"limit":None,"revision":self.revision,"error":warning,"passive":True}

    def drafts(self,deleted=False):
        with self.lock, closing(sqlite3.connect(self.path.as_uri()+"?mode=ro",uri=True,timeout=.2)) as db:
            if self._retry_open:raise BlockingIOError(self.error)
            if self.error:raise ValueError(self.error)
            rows=[{"id":i,"name":name,**({"terrain":json.loads(terrain)} if terrain else {}),"points":json.loads(points),"tags":json.loads(tags),"labels":validate_labels(json.loads(labels) if labels else None,len(json.loads(points))),"updated":updated,"loop":bool(loop),"links":validate_links(json.loads(links),json.loads(points)),"combat":validate_combat(json.loads(combat))}
                    for i,name,points,updated,loop,combat,labels,links,tags,terrain in db.execute("SELECT id,name,points,updated,loop,combat,labels,links,tags,terrain FROM drafts WHERE deleted=? ORDER BY updated DESC LIMIT 100",(int(deleted),))]
            return [{**row,'navigation_revision':navigation_revision(row)} for row in rows]

    def save_draft(self,name,points,draft_id=None,loop=True,expected_updated=None,labels=None,links=None,tags=None,terrain=None):
        if self.error:raise ValueError(self.error)
        if type(loop)is not bool:raise ValueError("Nieprawidłowa opcja pętli.")
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError("Nazwa szkicu: od 1 do 80 znaków.")
        if not isinstance(points,list) or not 2<=len(points)<=MAX_ANCHORS or not all(position(p) for p in points):
            raise ValueError("Szkic wymaga od 2 do 2048 punktów.")
        labels=validate_labels(labels,len(points));links=validate_links(links,points)
        with self.lock,sqlite3.connect(self.path,timeout=.2) as db:
            if draft_id is not None and not db.execute("SELECT 1 FROM drafts WHERE id=? AND deleted=0",(draft_id,)).fetchone():
                raise ValueError("Nie ma takiego szkicu w tym profilu.")
            if draft_id is not None and expected_updated is not None:
                if db.execute('SELECT updated FROM drafts WHERE id=?',(draft_id,)).fetchone()[0]!=expected_updated:
                    raise ValueError('Szkic zmienił się w innym oknie. Wczytaj go ponownie przed zapisem; Twoje bieżące zmiany pozostają w edytorze.')
            if draft_id is None and db.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]>=100:
                raise ValueError("Osiągnięto limit 100 szkiców; zapisz zmiany w istniejącym szkicu.")
            if tags is not None and draft_id is not None:
                raise ValueError("Zapisz etykiety osobno od zmian waypointów.")
            draft_id=draft_id or uuid.uuid4().hex
            db.execute("INSERT INTO drafts (id,name,points,updated,loop) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,points=excluded.points,updated=excluded.updated,loop=excluded.loop",(draft_id,name.strip(),json.dumps(points),time.time(),int(loop)))
            db.execute('UPDATE drafts SET labels=?,links=? WHERE id=?',(json.dumps(labels),json.dumps(links),draft_id))
            if terrain is not None:
                from hunt_areas import checked_connector, checked_sectors
                if not isinstance(terrain,dict) or not isinstance(terrain.get('ground'),str) or not 1<=len(terrain['ground'])<=160:
                    raise ValueError('Brak źródła wyboru terenu.')
                cells = checked_sectors(terrain.get('sectors'))
                selection = dict(ground=terrain['ground'], sectors=sorted(cells))
                via = checked_connector(terrain.get('via', []), cells)
                if via:
                    selection['via'] = sorted(via)
                db.execute('UPDATE drafts SET terrain=? WHERE id=?',(json.dumps(selection),draft_id))
            if tags is not None:
                db.execute('UPDATE drafts SET tags=? WHERE id=?',(json.dumps(validate_tags(tags)),draft_id))
        return draft_id

    def set_tags(self, draft_id, tags, previous):
        if self.error:raise ValueError(self.error)
        if not isinstance(draft_id, str) or not 1 <= len(draft_id) <= 64:
            raise ValueError("Wybierz zapisany szkic.")
        updated = json.dumps(validate_tags(tags))
        expected = json.dumps(validate_tags(previous))
        with self.lock, sqlite3.connect(self.path, timeout=.2) as db:
            changed = db.execute("UPDATE drafts SET tags=? WHERE id=? AND deleted=0 AND tags=?",
                                 (updated, draft_id, expected))
            if not changed.rowcount:
                raise ValueError("Etykiety szkicu zmieniły się w innym oknie lub szkic został usunięty.")

    def change_draft(self,draft_id,operation,combat=None,loop=None):
        if self.error:raise ValueError(self.error)
        if not isinstance(draft_id,str) or not draft_id or len(draft_id)>64:raise ValueError("Nieprawidłowy identyfikator szkicu.")
        if operation not in {'delete','restore','configure','set_loop'}:raise ValueError('Nieznana operacja szkicu.')
        with self.lock,sqlite3.connect(self.path,timeout=.2) as db:
            row=db.execute('SELECT deleted FROM drafts WHERE id=?',(draft_id,)).fetchone()
            if not row or bool(row[0])!=(operation=='restore'):raise ValueError('Nie ma takiego szkicu w tym profilu.')
            if operation=='set_loop':
                if type(loop)is not bool:raise ValueError('Nieprawidłowa opcja pętli.')
                db.execute('UPDATE drafts SET loop=?,updated=? WHERE id=?',(int(loop),time.time(),draft_id))
            elif operation=='configure':
                config=validate_combat(combat)
                if type(loop)is not bool:raise ValueError('Nieprawidłowa opcja pętli.')
                db.execute('UPDATE drafts SET combat=?,loop=?,updated=? WHERE id=?',(json.dumps(config),int(loop),time.time(),draft_id))
            else:db.execute('UPDATE drafts SET deleted=?,updated=? WHERE id=?',(int(operation=='delete'),time.time(),draft_id))

    def with_recorded_ropes(self,observations):
        rows=list(observations)
        known={r.get('id') for r in rows}
        with self.lock:
            return rows+[row for key,row in self.recorded_ropes.items() if key not in known]

    def infer_recorded_ropes(self,recording,observations):
        tiles=self._paged_tiles()
        return recorded_rope_edges(recording,observations,self.scope,tiles)

    def remember_recorded_ropes(self,recording,observations):
        edges=self.infer_recorded_ropes(recording,observations)
        with self.lock:
            fresh=[r for r in edges if r['id'] not in self.recorded_ropes]
            if fresh:
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.executemany("INSERT OR IGNORE INTO recorded_ropes VALUES (?,?,?)", [
                        (r['id'],recording['name'],json.dumps(r)) for r in fresh])
                self.recorded_ropes.update((r['id'],r) for r in fresh)
        return self.with_recorded_ropes(observations)

    def route_transitions(self,observations,cells):
        if self.error:return []
        visible={(c['x'],c['y'],c['z']) for c in cells}
        tiles=self._paged_tiles()
        edges=navigation_edges(tiles,[r for r in self.with_recorded_ropes(observations) if tuple(r.get('entry') or ()) in visible])
        return [{k:r.get(k) for k in ('id','origin','entry','exit','action','item_id','tool_id','prepared_item_id','tool_ids','count')}
                for values in edges.values() for dest,r in values
                if tuple(r['entry']) in visible and tuple(r['origin']) in tiles and dest in tiles and r['action']!='unknown']

    def recording_step_observations(self, observations):
        """Resolve a recorded floor step when the same entrance had several tile stacks."""
        tiles = self._paged_tiles()
        entries = {}
        for row in observations:
            if (row.get("evidence")=="observed" and row.get("entry") and row.get("exit")
                    and row["entry"][2]!=row["exit"][2]):
                entries.setdefault(tuple(row["entry"]),[]).append(row)
        discard = set()
        for entry, rows in entries.items():
            if (len(rows)<2 or len({tuple(r["exit"]) for r in rows})!=1 or
                    not all(r.get("reason")=="matched_command" and r.get("action")=="step"
                            and r.get("item_id") is None and r.get("entry_items") for r in rows)):
                continue
            stacks = {tuple(sorted(r["entry_items"])) for r in rows}
            if len(stacks)<2:
                continue
            ids = {item["id"] for item in tiles.get(entry,{}).get("items",[])}
            matching = [stack for stack in stacks if set(stack)<=ids]
            if len(matching)==1:
                discard.update(r["id"] for r in rows if tuple(sorted(r["entry_items"]))!=matching[0])
        return [r for r in observations if r.get("id") not in discard]

    def ordinary_recorded_tiles(self, recording, observations):
        tiles = self._paged_tiles()
        entries = {tuple(row["entry"]) for row in observations if row.get("entry")}
        points = {tuple(s["pos"]) for s in recording["observed_path"] if position(s.get("pos"))}
        return {p for p in points-entries if (cell := tiles.get(p,{})).get("kind") == "clear"
                and not cell.get("navigation") and not cell.get("stairs")}

    def unavailable_recorded_crossings(self, recording, observations):
        tiles = self._paged_tiles()
        trace = recording["observed_path"]
        pairs = {(tuple(a["pos"]),tuple(b["pos"])) for a,b in zip(trace,trace[1:])
                 if a["pos"][2]!=b["pos"][2] and not b.get("break_before")}
        permitted = set()
        bound = list(observations)
        wanted = {}
        for a,b in pairs:
            pair = [list(a),list(b)]
            links = trace_links(pair,observations,pair)
            if links:
                identity = links[0]["id"]
                wanted[a,b] = identity
                for source in observations:
                    if source.get("id")==identity and source["origin"]!=pair[0]:
                        row = transition_from_origin(source,pair[0],observations)
                        if row is not None:
                            bound.append(row)
        edges = navigation_edges(tiles,bound)
        for (a,b),identity in wanted.items():
            if any(dest==b and row["id"]==identity for dest,row in edges.get(a,())):
                permitted.add((a,b))
        return pairs-permitted

    def recording_route(self,recording,observations,optimize):
        if recording.get("atlas_scope") not in (None,self.scope):
            raise ValueError("To nagranie pochodzi z innego atlasu.")
        rows=self.remember_recorded_ropes(recording,observations)
        points,original,links=recording_points(recording,rows,optimize=optimize,include_links=True,
                                              keep_positions=recording.get("coverage_anchors", ()))
        # A recorded ordinary waypoint can lie on terrain the live executor
        # avoids. Prefer an adjacent tile actually visited on this run, only
        # if both adjoining legs can be planned entirely on clear terrain.
        adjusted=[]
        tiles=self._paged_tiles()
        ends={i for link in links for i in (link['from_index'],link['from_index']+1)}
        trace=recording.get('observed_path') or []
        def ordinary_avoid(i):
            return (i not in ends and points[i-1][2]==points[i][2]==points[i+1][2]
                    and tiles.get(tuple(points[i]),{}).get('kind')=='avoid'
                    and not tiles[tuple(points[i])].get('navigation'))
        def safe_path(anchors):
            proposal=self.plan(anchors,rows)
            return proposal['path'] if proposal['complete'] and not proposal['avoid_tiles'] and not proposal['transitions'] else []
        candidates={tuple(points[i]):[] for i in range(1,len(points)-1) if ordinary_avoid(i)}
        for n,sample in enumerate(trace):
            p=sample.get('pos')
            if not position(p) or tuple(p) not in candidates:continue
            for j in (n+1,n-1):
                if not 0<=j<len(trace) or trace[max(n,j)].get('break_before'):continue
                q=trace[j].get('pos')
                if not position(q) or q[2]!=p[2] or max(abs(q[k]-p[k]) for k in (0,1))!=1:continue
                cell=tiles.get(tuple(q),{})
                if cell.get('kind')=='clear' and not cell.get('navigation') and q not in candidates[tuple(p)]:
                    candidates[tuple(p)].append(q)
        for avoid,run in groupby(range(1,len(points)-1),key=ordinary_avoid):
            if not avoid:continue
            indices=list(run)
            start,end=points[indices[0]-1],points[indices[-1]+1]
            replacement=[candidates[tuple(points[i])][0] for i in indices if candidates[tuple(points[i])]]
            if len(replacement)!=len(indices) or not safe_path([start,*replacement,end]):
                detour=safe_path([start,end])
                if not detour:continue
                replacement=[];cursor=0
                for i in indices:
                    p=points[i]
                    distance=lambda n:max(abs(p[k]-detour[n][k]) for k in (0,1))
                    cursor=min(range(cursor,len(detour)),key=distance)
                    if distance(cursor)>2 or tiles[tuple(detour[cursor])].get('navigation'):break
                    replacement.append(detour[cursor])
                if len(replacement)!=len(indices) or not safe_path([start,*replacement,end]):continue
            for i,q in zip(indices,replacement):
                adjusted.append({'waypoint':i+1,'from':points[i],'to':q})
                points[i]=q
        return points,original,links,adjusted

    def plan(self,points,observations,original=None,*,loop=False,links=None):
        if self.error:raise ValueError(self.error)
        if not isinstance(points,list) or not 2<=len(points)<=MAX_ANCHORS or not all(position(p) for p in points):
            raise ValueError("Wskaż od 2 do 2048 punktów trasy.")
        anchors=points
        observations=self.with_recorded_ropes(observations)
        links=validate_links(links,points);selected={r['from_index']:r['id'] for r in links}
        # Keep drawn anchors intact. A nearby endpoint may close via verified
        # ordinary terrain; distant returns still require explicit waypoints.
        loop_closed=points[-1]==points[0]
        tiles=self._paged_tiles()
        # IDs identify an entry/exit mechanism, not its most recently observed
        # approach. Preserve saved links after learning a different approach.
        bound=list(observations)
        bound_approaches=set()
        step_exits = {}
        if links:
            for row in observations:
                if row.get('evidence') == 'observed' and row.get('action') == 'step' and row.get('entry'):
                    step_exits.setdefault(tuple(row['entry']), set()).add(tuple(row['exit']))
        floor_links = {}
        for link in links:
            a,b=points[link['from_index']:link['from_index']+2]
            for source in observations:
                if source.get('id')!=link['id'] or source.get('exit')!=b:continue
                entry = source.get('entry')
                if (source.get('action') == 'step' and source.get('evidence') == 'observed' and entry
                        and a[2] == entry[2] and abs(a[2] - b[2]) == 1
                        and max(abs(a[n] - entry[n]) for n in (0, 1)) == 1
                        and is_step_floor_tile(tiles.get(tuple(entry), {}))):
                    if step_exits.get(tuple(entry)) == {tuple(b)}:
                        floor_links.setdefault(link['from_index'], []).append(source)
                    # A directional entrance with conflicting exits keeps its exact recorded side.
                    continue
                row=transition_from_origin(source,a,observations)
                key=(link['id'],tuple(a),tuple(b))
                if row is not None and row['origin']!=source['origin'] and key not in bound_approaches:
                    bound.append(row)
                    bound_approaches.add(key)
        edges=navigation_edges(tiles,bound)
        automatic_entries={tuple(r["entry"]) for values in edges.values() for _,r in values if r["action"] in {"step","use_with_then_step"}}
        @lru_cache(maxsize=4096)
        def terrain(p):
            """Return a walkable terrain kind or the item ID of a removable web."""
            if p in automatic_entries:
                return None
            tile = tiles.get(p)
            if not tile:
                return None
            if tile['kind'] in {'clear','avoid'} and all(f['kind'] in {'rope_spot','ladder'} for f in tile.get('navigation', ())):
                return tile['kind']
            web = cuttable_web(tile)
            return web['id'] if web else None
        # Only verified physical entrances can replace an authored approach. Keep
        # the waypoint number attached to the side actually reached on this leg.
        crossings = {}
        for index, sources in floor_links.items():
            valid = [edge for source in sources for dest, edge in edges.get(tuple(source['origin']), ())
                     if edge.get('id') == selected[index] and edge['entry'] == source['entry']
                     and dest == tuple(points[index + 1]) and edge['action'] == 'step']
            if valid and len({tuple(edge['entry']) for edge in valid}) == 1:
                edge = next((r for r in valid if r['origin'] == points[index]), valid[0])
                x, y, z = edge['entry']
                sides = {(x+dx, y+dy, z) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy}
                crossings[index] = (edge, sides)
        path=[list(points[0])];actions=[];gaps=[];legs=[];expanded=0
        pairs=list(zip(points,points[1:]))
        for index,(a,b) in enumerate(pairs):
            closing=index==len(points)-1
            a,b=tuple(path[-1]),tuple(b)
            entered_gate=index>0 and any(tuple(t['exit'])==a and tuple(t['entry'])==a and t['action']=='use' for t in actions)
            open_door=tiles.get(a,{}).get('kind')=='clear' and any(f.get('door_state_hint')=='open' for f in tiles.get(a,{}).get('navigation',[]))
            if a not in tiles or (not terrain(a) and a not in edges and not entered_gate and not open_door):
                gaps.append({"segment":index,"from":list(a),"to":list(b),"reason":"Punkt początkowy jest nieznany lub zablokowany"})
                break
            walk_goal=b
            transition=None
            goals={b}
            preferred=b
            if index in crossings:
                edge, sides = crossings[index]
                preferred = tuple(edge['origin'])
                goals = {p for p in sides if p == a or terrain(p) == 'clear'}
                walk_goal = preferred
                transition = {**edge, 'recorded_origin': edge['origin']}
            elif index in selected:
                choices=[r for dest,r in edges.get(a,[])
                         if dest==b and (r.get('id')==selected[index] or selected[index] in r.get('link_aliases',()))]
                exact=[r for r in choices if tuple(r['origin'])==a]
                choices=exact or choices
                if len(choices)!=1:
                    gaps.append({'segment':index,'from':list(a),'to':list(b),'reason':'Wybrane przejście nie pasuje do aktualnych danych; wybierz je ponownie.'});break
                transition={**choices[0], 'id':selected[index]}
                walk_goal=tuple(transition['origin'])
                goals={walk_goal}
            elif a[2]!=b[2]:
                gaps.append({'segment':index,'from':list(a),'to':list(b),'reason':'Dodaj jawne przejście między piętrami z menu mapy.'});break
            elif index + 1 in crossings:
                edge, sides = crossings[index + 1]
                preferred = tuple(edge['origin'])
                goals = {p for p in sides if p == a or terrain(p) == 'clear'}
            found=a in goals
            if found:
                walk_goal=a
            queue=[] if found or not goals else [(0, False, a)]
            cost={a:0};parent={}
            while queue and expanded<MAX_EXPANSIONS:
                distance,_,p=heapq.heappop(queue)
                if distance!=cost[p]:continue
                expanded+=1
                if p in goals:
                    walk_goal=p;found=True;break
                neighbors=[]
                for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)):
                    q=(p[0]+dx,p[1]+dy,p[2])
                    # DBVictory acknowledges diagonal steps even between two
                    # blocked side tiles. Only the destination is entered.
                    kind = terrain(q)
                    if kind is None:
                        continue
                    step_cost = 14 if dx and dy else 10
                    # Prefer a short clear bypass to using a tool.
                    if kind in WEB_ITEM_IDS:
                        neighbors.append((q, step_cost + 35, web_edge(p,q,kind)))
                    else:
                        neighbors.append((q, step_cost + (50 if kind == 'avoid' else 0), None))
                # Known normal doors can open on the route. Floor changes,
                # holes and teleports still require explicit selected edges.
                for q,row in edges.get(p,[]):
                    if row.get('automatic_door') and q[2]==a[2] and terrain(q) in {'clear','avoid'}:
                        neighbors.append((q,20,row))
                for q,step,row in neighbors:
                    new=distance+step
                    if new<cost.get(q,float("inf")):
                        cost[q]=new;parent[q]=(p,row);heapq.heappush(queue,(new,q != preferred,q))
            if not found:
                target=tiles.get(walk_goal)
                reason=('Osiągnięto limit wyszukiwania drogi.' if expanded>=MAX_EXPANSIONS else
                        'Brak dojścia do wolnego pola obok wybranego przejścia.' if index in crossings or index + 1 in crossings else
                        'Pole docelowe nie zostało zapisane w atlasie.' if target is None else
                        'Pole docelowe jest przejściem; brakuje przypisanego wejścia i wyjścia.' if any(f.get('kind') != 'web' for f in target.get('navigation', ())) and not terrain(b) else
                        'Pole docelowe jest zapisane jako zablokowane.' if not terrain(b) else
                        'Znane pola atlasu nie tworzą dostępnej drogi między tymi punktami.')
                gaps.append({"segment":index,"from":list(a),"to":list(b),"reason":reason})
                break
            segment=[];used=[];p=walk_goal
            while p!=a:
                segment.append(list(p));p,row=parent[p]
                if row:used.append(row)
            start=len(path)-1
            path.extend(reversed(segment))
            leg_actions=list(reversed(used))
            if transition:
                path.append(list(b))
                leg_actions.append({**transition, 'origin':list(walk_goal)})
            for row in leg_actions:
                action = {k:row.get(k) for k in ("origin","entry","exit","action","item_id","tool_id",
                          "prepared_item_id","tool_ids","entry_items","id","open_item_id","door_closed_ids")}
                if 'recorded_origin' in row:
                    action['recorded_origin'] = row['recorded_origin']
                actions.append(action)
            legs.append({"from_index":index,"to_index":0 if closing else index+1,"start":start,"end":len(path)-1,**({'closing':True} if closing else {})})
            if closing:loop_closed=True
        return_start=len(path)-1;return_mode='closed' if loop_closed else None;return_error=None
        if loop and not gaps and not loop_closed:
            back=self.plan([anchors[-1],anchors[0]],observations)
            if back['complete'] and not back['avoid_tiles']:
                return_mode='direct';return_parts=[(len(anchors)-1,0,back)]
            else:
                # Retrace waypoint order only with independently learned inverse
                # transitions. A downward hole never implies a rope/ladder back.
                return_mode='reverse';return_parts=[]
                for i in range(len(anchors)-2,-1,-1):
                    start,end=anchors[i+1],anchors[i]
                    part=self.plan([start,end],observations)
                    if not part['complete'] or part['avoid_tiles']:
                        part=None
                        if i in selected:
                            options=[]
                            for source in observations:
                                row=gate_entry_record(source) if source.get('object_change') else source
                                if not row or row.get('evidence')!='observed' or not row.get('id'):continue
                                origin,dest=row['origin'],row['exit']
                                if origin[2]!=start[2] or dest[2]!=end[2]:continue
                                if max(abs(origin[n]-start[n]) for n in (0,1))>2 or max(abs(dest[n]-end[n]) for n in (0,1))>2:continue
                                route=[start]
                                if origin!=start:route.append(origin)
                                link={'from_index':len(route)-1,'id':row['id']};route.append(dest)
                                if dest!=end:route.append(end)
                                candidate=self.plan(route,observations,links=[link])
                                if candidate['complete'] and not candidate['avoid_tiles']:options.append(candidate)
                            if options:part=min(options,key=lambda p:len(p['path']))
                        if part is None:
                            return_error=f'Brak znanego powrotu z {tuple(start)} do {tuple(end)}. Powrót przez przejście musi być osobno zaobserwowany.'
                            break
                    return_parts.append((i+1,i,part))
            if return_error is None:
                for a_index,b_index,part in return_parts:
                    offset=len(path)-1;path.extend(part['path'][1:]);actions.extend(part['transitions'])
                    legs.append(dict(from_index=a_index,to_index=b_index,start=offset,end=len(path)-1,returning=True,closing=b_index==0))
                loop_closed=True
            else:
                return_mode='unavailable'
                gaps.append({'segment':len(anchors)-1,'from':anchors[-1],'to':anchors[0],'reason':return_error})
        avoided=[p for p in path if tiles.get(tuple(p),{}).get("kind")=="avoid" and terrain(tuple(p)) not in WEB_ITEM_IDS]
        return {"points":anchors,"original":original or anchors,"path":path,"transitions":actions,"gaps":gaps,"loop":loop,"loop_closed":loop_closed,
                "legs":legs,"avoid_tiles":avoided,"requires_action_review":bool(avoided),
                "complete":not gaps,"links":links,"expanded":expanded,"preview_only":True,"scope":self.scope,
                "return_mode":return_mode,"return_start":return_start,"return_error":return_error,
                "warning":"Plan po historycznie znanych polach. Stan drzwi i przeszkód wymaga ponownego odczytu przed ruchem."}


def recording_points(recording, observations=(), *, optimize=True, keep_positions=(), include_links=False):
    """Keep floor changes, interactions and shape; simplify only small walk wiggles."""
    original=[];mandatory=set();samples=[]
    def add(p,required=False,sample=None):
        if not position(p):return
        if not original or original[-1]!=list(p):
            original.append(list(p))
            samples.append([sample,sample])
        else:samples[-1][1]=sample
        if required:mandatory.add(len(original)-1)
    waypoints=recording.get("waypoints",[])
    trace=recording.get("observed_path")
    if len(waypoints)>20000 or (isinstance(trace,list) and len(trace)>20000):
        raise ValueError("Nagranie jest za długie do jednego planu; podziel je na odcinki.")
    def interaction(wp):
        return wp.get("type") in {"use_item","use_item_ex"} and (wp.get("x",65535)!=65535 or wp.get("to_x",65535)!=65535)
    if isinstance(trace,list):
        # Version 2: only observed positions establish the route. Requested
        # destinations and lagging floor packets must not invent visited tiles.
        times=sorted(wp.get("t",0) for wp in waypoints if interaction(wp))
        next_action=0
        for index,snapshot in enumerate(trace):
            until=trace[index+1].get("t",float("inf")) if index+1<len(trace) else float("inf")
            required=bool(snapshot.get("break_before"))
            while next_action<len(times) and times[next_action]<until:
                required=True;next_action+=1
            add(snapshot.get("pos"),required,index)
    else:
        add(recording.get("start_position"),True)
        # Older recordings contain requested steps. Replace an entrance with
        # its approach only when an observed edge independently matches all
        # three positions. Otherwise retain the gap for manual review.
        previous=None
        for wp in waypoints:
            kind=wp.get("type")
            if interaction(wp):add(wp.get("player_pos",wp.get("pos")),True)
            if kind=="floor_change" and previous and previous.get("type")=="walk":
                matches=any(r.get("evidence")=="observed" and not r.get("object_change")
                    and r.get("origin")==previous.get("player_pos") and r.get("entry")==previous.get("pos")
                    and r.get("exit")==wp.get("pos") for r in observations)
                if matches and original and original[-1]==previous.get("pos"):
                    mandatory.discard(len(original)-1);original.pop()
                    add(previous.get("player_pos"),True)
            if kind in {"walk","position","floor_change"}:add(wp.get("pos"),kind=="floor_change")
            if kind not in {"tile_transform_item"}:previous=wp
    # A normal door can be sampled while the player stands inside it. Its
    # confirmed origin->exit edge owns that intermediate door tile.
    for r in observations:
        if r.get('object_change'):
            r=gate_entry_record(r)
            if r is None:continue
        if r.get('evidence')!='observed' or not r.get('entry'):continue
        if r.get('action')=='use_then_step' or (r.get('action')=='step' and r.get('reason')=='door_crossing'):
            for i in range(len(original)-3,-1,-1):
                if original[i:i+3]==[r['origin'],r['entry'],r['exit']]:
                    original.pop(i+1)
                    samples.pop(i+1)
                    mandatory={n if n<=i else n-1 for n in mandatory if n!=i+1}
        for i in range(len(original)-1):
            if original[i+1]==r['exit'] and transition_from_origin(r,original[i],observations) is not None:mandatory.update((i,i+1))
    if len(original)<2:raise ValueError("Nagranie nie zawiera wystarczającej drogi.")
    mandatory.update((0,len(original)-1))
    pinned={tuple(p) for p in keep_positions}
    mandatory.update(i for i,p in enumerate(original) if tuple(p) in pinned)
    for i in range(1,len(original)):
        if original[i][2]!=original[i-1][2]:mandatory.update((i-1,i))
    keep=set(mandatory)
    pending=[]
    def simplify(a,b):
        if b-a<=1:return
        ax,ay,_=original[a];bx,by,_=original[b];length=(bx-ax)**2+(by-ay)**2
        far=-1;distance=-1
        for i in range(a+1,b):
            x,y,_=original[i];t=max(0,min(1,((x-ax)*(bx-ax)+(y-ay)*(by-ay))/length)) if length else 0
            delta=(x-ax-t*(bx-ax))**2+(y-ay-t*(by-ay))**2
            if delta>distance:far,distance=i,delta
        if distance>(2.25 if optimize else 0):
            keep.add(far);pending.extend(((a,far),(far,b)))
    ordered=sorted(mandatory)
    pending.extend(zip(ordered,ordered[1:]))
    while pending:
        simplify(*pending.pop())
        if len(keep)>MAX_ANCHORS:raise ValueError("Nagranie jest za długie do jednego planu; podziel je na odcinki.")
    points=[original[i] for i in sorted(keep)]
    if len(points)>MAX_ANCHORS:raise ValueError("Nagranie jest za długie do jednego planu; podziel je na odcinki.")
    if include_links:
        links=[]
        if isinstance(trace,list):
            indices=sorted(keep)
            for index,(a,b) in enumerate(zip(indices,indices[1:])):
                start,end=samples[a][1],samples[b][0]
                # Only an observed direct crossing or a three-position
                # open/enter/exit can bind this exact occurrence of a leg.
                if end-start not in (1,2):continue
                window=trace[start:end+1]
                if any(s.get('break_before') for s in window[1:]):continue
                matched=trace_links(points[index:index+2],observations,[s['pos'] for s in window])
                if matched:links.append({'from_index':index,'id':matched[0]['id']})
        return points,original,links
    return points,original
