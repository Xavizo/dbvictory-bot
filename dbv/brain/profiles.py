"""Named configuration profiles, separate from atlas drafts and runtime state."""
import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from dbv.paths import ACTIONS_DIR, LOCAL_DATA_DIR
from dbv.brain.cavebot.cavebot_combat import validate_combat
from dbv.brain.state.game_alerts import validate_config as validate_alerts, default_config
from dbv.brain.combat.speed_up import validate_config as validate_speed
from dbv.brain.combat.barrier_spell import validate_config as validate_barrier
from dbv.brain.combat.barrier_band import validate_config as validate_band
from dbv.brain.combat.potions import validate_config as validate_potions

ROOT=LOCAL_DATA_DIR/'profiles.json'
INTERNAL={'dll_bridge','adaptive_cavebot'}


def bot_config(settings):
    return {'actions':{name:{k:copy.deepcopy(v) for k,v in cfg.items() if k!='enabled'}
                       for name,cfg in settings.get('actions',{}).items() if name not in INTERNAL},
            'combat':validate_combat(settings.get('combat_profile')),
            'alerts':validate_alerts(settings.get('alerts',default_config()))}


def validate_bot(value):
    if not isinstance(value,dict) or set(value)!={'actions','combat','alerts'} or not isinstance(value['actions'],dict):
        raise ValueError('Nieprawidłowy profil bota.')
    result={'actions':{},'combat':validate_combat(value['combat']),'alerts':validate_alerts(value['alerts'])}
    for name,cfg in value['actions'].items():
        if (not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',name) or name in INTERNAL
                or not isinstance(cfg,dict) or 'enabled' in cfg):raise ValueError('Profil zawiera stan uruchomienia akcji.')
        if not (ACTIONS_DIR/f'{name}.py').is_file():raise ValueError('Nieznana akcja w profilu.')
        if name=='speed_up':cfg=validate_speed(cfg)
        if name=='barrier_spell':cfg=validate_barrier(cfg)
        if name=='auto_barrier_band':cfg=validate_band(cfg)
        if name=='auto_potions':cfg=validate_potions(cfg,require_cooldowns=False)
        result['actions'][name]=copy.deepcopy(cfg)
    if len(json.dumps(result,allow_nan=False))>65536:raise ValueError('Profil jest zbyt duży.')
    return result


class ProfileStore:
    def __init__(self,path=ROOT):self.path=Path(path)

    def list(self):
        if not self.path.exists():return []
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,list):raise ValueError('Nieprawidłowy plik profili.')
        return data

    def _write(self,rows):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
        os.replace(temp,self.path)

    def save(self,kind,name,config,ident=None):
        if kind not in {'combat','bot'}:raise ValueError('Nieznany typ profilu.')
        if not isinstance(name,str) or not 1<=len(name.strip())<=60 or any(ord(c)<32 for c in name):raise ValueError('Podaj nazwę profilu (1–60 znaków).')
        config=validate_combat(config) if kind=='combat' else validate_bot(config)
        rows=self.list()
        if ident is not None and not any(r['id']==ident and r['kind']==kind for r in rows):raise ValueError('Profil nie istnieje.')
        row={'id':ident or uuid.uuid4().hex,'kind':kind,'name':name.strip(),'config':config,'updated':time.time()}
        rows=[r for r in rows if r['id']!=row['id']];rows.append(row);self._write(rows)
        return row

    def get(self,ident,kind):
        row=next((r for r in self.list() if r['id']==ident and r['kind']==kind),None)
        if row is None:raise ValueError('Profil nie istnieje.')
        return row

    def delete(self,ident,kind):
        self.get(ident,kind);self._write([r for r in self.list() if r['id']!=ident])


def require_stopped(state,kind):
    if getattr(state,'_adaptive_prepare_token',None) is not None:raise ValueError('Poczekaj na zakończenie przygotowania V2.')
    tasks=getattr(state,'_action_tasks',{})
    relevant={'adaptive_cavebot'} if kind=='combat' else set(tasks)-{'dll_bridge'}
    if any(name in relevant and not task.done() for name,task in tasks.items()):
        raise ValueError('Zatrzymaj V2 przed wczytaniem walki.' if kind=='combat' else 'Zatrzymaj wszystkie akcje przed wczytaniem profilu bota.')


def applied_bot_settings(settings,config):
    cfg=validate_bot(config);result=copy.deepcopy(settings)
    actions=result.setdefault('actions',{})
    for name in set(actions)|set(cfg['actions']):
        if name=='dll_bridge':continue
        actions[name]={**cfg['actions'].get(name,{}),'enabled':False}
    result['combat_profile']=cfg['combat'];result['alerts']=cfg['alerts']
    result.pop('paused_actions',None);result.pop('selected_combat_profile',None)
    return result
