"""User descriptions of individual map objects. Never movement or action settings."""
import json
import os
from pathlib import Path
import threading
import time

from transition_learning import position
from map_navigation import SUPPORTED_KINDS

MAX_BYTES = 1024*1024
MAX_NOTES = 1000
FIELDS = {"position","item_id","kind","required_level","activation_hint","note"}
ACTIVATIONS = {
    "stairs":("step",), "hole":("step",), "teleport":("step",),
    "ladder":("use",), "descent":("step","use"),
    "door":("open_if_closed",), "level_gate":("open_if_closed",),
    "rope_spot":("rope","aio","fly_up","rope_or_fly_up"),
    "web":("aio",),
    "covered_hole":("shovel","aio"),
}


def validate_note(data, *, loading=False):
    if not isinstance(data,dict) or set(data)!=FIELDS or not position(data.get("position")):
        raise ValueError("Nieprawidłowe dane opisu pola.")
    if type(data["item_id"]) is not int or not 100<=data["item_id"]<=65535 or not isinstance(data["kind"],str) or data["kind"] not in SUPPORTED_KINDS:
        raise ValueError("Nieprawidłowy typ lub ID obiektu.")
    level=data["required_level"]
    if level is not None and (type(level) is not int or not 1<=level<=100000 or data["kind"]!="level_gate"):
        raise ValueError("Wymagany poziom można przypisać bramce: 1–100000.")
    if data["activation_hint"] is not None and (not isinstance(data["activation_hint"],str) or data["activation_hint"] not in {"step","use","rope","shovel","fly_up","rope_or_fly_up","aio","open_if_closed"}):
        raise ValueError("Nieprawidłowa czynność.")
    allowed=ACTIVATIONS[data["kind"]]
    activation=data["activation_hint"]
    if activation is None or (data["kind"] in {"door","level_gate"} and activation in {"use","step"}):
        activation=allowed[0]
    elif activation not in allowed:
        if loading: activation=allowed[0]  # Old descriptions remain readable; normalize in memory.
        else: raise ValueError("Ta czynność nie pasuje do wybranego typu obiektu.")
    if not isinstance(data["note"],str) or len(data["note"])>240 or any(ord(c)<32 for c in data["note"]):
        raise ValueError("Notatka może mieć do 240 znaków w jednej linii.")
    return {**data,"position":list(data["position"]),"note":data["note"].strip(),"activation_hint":activation}


def note_key(data):
    return (*data["position"],data["item_id"])


class MapAnnotations:
    def __init__(self,directory,scope):
        self.scope=scope
        self.path=Path(directory)/f"map_notes-{scope[:16]}.json"
        self.records={}
        self.error=None
        self._stamp=None
        self._lock=threading.RLock()
        try:
            if self.path.exists():
                stat=self.path.stat()
                with self.path.open("rb") as f: raw=f.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES: raise ValueError()
                doc=json.loads(raw)
                if doc["version"]!=1 or doc["scope"]!=scope or not isinstance(doc["records"],list) or len(doc["records"])>MAX_NOTES:
                    raise ValueError()
                records={}
                for row in doc["records"]:
                    if set(row)!=FIELDS|{"updated_at"} or type(row["updated_at"]) not in (int,float) or not 0<row["updated_at"]<1e12:
                        raise ValueError()
                    note=validate_note({k:row[k] for k in FIELDS},loading=True)
                    records[note_key(note)]={**note,"updated_at":row["updated_at"]}
                self.records=records
                self._stamp=(stat.st_mtime_ns,stat.st_size)
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            self.error="Plik własnych opisów jest nieprawidłowy lub niedostępny; zapis zablokowany."

    def save(self,data,remove=False):
        note=validate_note(data)
        with self._lock:
            if self.error: raise ValueError(self.error)
            records=dict(self.records)
            key=note_key(note)
            if remove:
                records.pop(key,None)
            else:
                if key not in records and len(records)>=MAX_NOTES:
                    raise ValueError("Osiągnięto limit 1000 własnych opisów.")
                records[key]={**note,"updated_at":time.time()}
            try:
                stat=self.path.stat() if self.path.exists() else None
                if ((stat.st_mtime_ns,stat.st_size) if stat else None)!=self._stamp:
                    self.error="Plik własnych opisów zmienił się poza sesją; zapis wstrzymany."
                    raise ValueError(self.error)
                raw=json.dumps({"version":1,"scope":self.scope,"records":list(records.values())},ensure_ascii=False,indent=2).encode("utf-8")
                if len(raw)>MAX_BYTES: raise ValueError("Osiągnięto limit rozmiaru opisów.")
                self.path.parent.mkdir(parents=True,exist_ok=True)
                temp=self.path.with_suffix(".tmp")
                with temp.open("wb") as f:
                    f.write(raw); f.flush(); os.fsync(f.fileno())
                os.replace(temp,self.path)
                stat=self.path.stat(); self._stamp=(stat.st_mtime_ns,stat.st_size)
                self.records=records
            except OSError:
                raise ValueError("Nie udało się zapisać opisu na dysku.") from None

    def annotate(self,cells):
        with self._lock: records=dict(self.records)
        result=[]
        for cell in cells:
            features=list(cell.get("navigation",[]))
            for item in cell["items"]:
                note=records.get((cell["x"],cell["y"],cell["z"],item["id"]))
                if note is None: continue
                features=[f for f in features if f["item_id"]!=item["id"]]
                feature={k:note[k] for k in ("kind","item_id","note")}
                feature.update(stack=item["stack"],source="user_annotation")
                if note["required_level"] is not None:
                    feature.update(required_level=note["required_level"],requirement_source="user_report")
                if note["activation_hint"]: feature["activation_hint"]=note["activation_hint"]
                if note["kind"] in {"door","level_gate"}:
                    feature.update(door_state_hint="closed" if "not_walkable" in item["flags"] else "open",closing_policy="unknown")
                features.append(feature)
            result.append({**cell,"navigation":features})
        return result
