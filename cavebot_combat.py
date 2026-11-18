"""V2 combat policy. Only fresh, uniquely identified visible monsters qualify."""
import math
import re
import time
from cavebot_lure import PursuitTracker,gap,group_needs_time,route_approaches,lure_stopping_path
from combat_reachability import reachable_tiles
from combat_spell_conditions import validate_conditions,spell_reaches,evaluate_spells,evaluate_spell
from combat_wave import facing


def validate_spell(spell):
    if not isinstance(spell,dict):raise ValueError('Nieprawidłowy czar.')
    words=spell.get('words','')
    if not isinstance(words,str) or not re.fullmatch(r"[A-Za-z][A-Za-z '-]{0,63}",words):raise ValueError('Wpisz słowa czaru (do 64 znaków).')
    cooldown=spell.get('cooldown',1)
    if type(cooldown) not in (int,float) or not math.isfinite(cooldown) or not .2<=cooldown<=120:raise ValueError('Cooldown czaru: 0,2–120 s.')
    trigger=spell.get('trigger','distance')
    if trigger not in {'visible','distance'}:raise ValueError('Nieprawidłowy warunek czaru.')
    row=dict(words=words.strip(),trigger=trigger,cooldown=cooldown,ki=spell.get('ki',0),range=spell.get('range',5),count=spell.get('count',1))
    return validate_conditions(spell,row)


def validate_combat(value=None):
    value={} if value is None else value
    if not isinstance(value,dict):raise ValueError('Nieprawidłowa konfiguracja walki.')
    result=dict(enabled=value.get('enabled',False),mode=value.get('mode','stop'),strategy=value.get('strategy','nearest'),
                engage=value.get('engage','near'),radius=value.get('radius',5),distance=value.get('distance',1),lure_count=value.get('lure_count',4),
                lure_seconds=value.get('lure_seconds',8),lure_distance=value.get('lure_distance',3),rules=value.get('rules',[]),spells=value.get('spells',[]))
    if result['engage'] not in {'visible','near'}:raise ValueError('Nieprawidłowy warunek wyboru celu.')
    if type(result['enabled']) is not bool or result['mode'] not in {'stop','chase','lure'} or result['strategy'] not in {'nearest','lowest_hp','highest_hp','finish'}:
        raise ValueError('Nieprawidłowy sposób walki lub wyboru celu.')
    for key,lo,hi in [('radius',1,7),('distance',1,7),('lure_count',1,12),('lure_seconds',1,30),('lure_distance',1,7)]:
        if type(result[key]) is not int or not lo<=result[key]<=hi:raise ValueError(f'Nieprawidłowe ustawienie: {key}.')
    if not isinstance(result['rules'],list) or len(result['rules'])>32:raise ValueError('Maksymalnie 32 reguły potworów.')
    rules=[]
    for index,rule in enumerate(result['rules']):
        if not isinstance(rule,dict):raise ValueError('Nieprawidłowa reguła potwora.')
        pattern=rule.get('pattern','').strip() if isinstance(rule.get('pattern'),str) else ''
        if not 1<=len(pattern)<=80 or any(ord(c)<32 for c in pattern):raise ValueError('Wpisz nazwę potwora lub wzorzec z *.')
        if rule.get('action','attack') not in {'attack','ignore'}:raise ValueError('Nieprawidłowa reguła celu.')
        priority=rule.get('priority',max(1,100-index))
        if type(priority) is not int or not 0<=priority<=100:raise ValueError('Priorytet potwora: 0–100 (więcej = ważniejszy).')
        stance=rule.get('stance','inherit');spell_mode=rule.get('spell_mode','inherit')
        if stance not in {'inherit','stop','chase','lure','distance'}:raise ValueError('Nieprawidłowa strategia potwora.')
        if spell_mode not in {'inherit','none','custom'}:raise ValueError('Nieprawidłowy wybór czarów potwora.')
        distance=rule.get('distance',3 if stance=='distance' else result['distance'])
        if type(distance) is not int or not (2 if stance=='distance' else 1)<=distance<=7:raise ValueError('Dystans potwora: 2–7 SQM dla walki dystansowej, 1–7 dla pościgu.')
        spell=validate_spell(rule['spell']) if rule.get('spell') is not None else None
        if spell_mode=='custom' and spell is None:raise ValueError('Wpisz czar dla tego potwora.')
        if stance=='distance' and spell_mode=='custom' and spell['aim']=='target' and spell['trigger']=='distance' and spell['range_op']=='<=' and spell['range']<distance:
            raise ValueError('Zasięg czaru jest mniejszy od dystansu do potwora.')
        rules.append(dict(pattern=pattern,action=rule.get('action','attack'),priority=priority,
                          stance=stance,distance=distance,spell_mode=spell_mode,spell=spell))
    if result['enabled'] and not any(r['action']=='attack' for r in rules):raise ValueError('Dodaj co najmniej jedną regułę ataku.')
    if not isinstance(result['spells'],list) or len(result['spells'])>12:raise ValueError('Maksymalnie 12 czarów.')
    spells=[validate_spell(spell) for spell in result['spells']]
    result.update(rules=rules,spells=spells)
    return result


def matches(pattern,name):
    # Only * is special. Regex and shell ?/[...] syntax are literal.
    return re.fullmatch('.*'.join(re.escape(p) for p in pattern.casefold().split('*')),name.casefold()) is not None


def eligible_monsters(gs,sample,config,now=None):
    now=time.time() if now is None else now
    if sample.get('status')!='ready' or sample.get('stale') or sample.get('age_seconds',99)>.6 or tuple(sample.get('center',[]))!=tuple(gs.position):return []
    cells={(c['x'],c['y'],c['z']):c for c in sample.get('cells',[])}
    accessible=reachable_tiles(sample['cells'],gs.position)
    creatures=list(gs.creatures.items());result=[]
    pets=getattr(gs,'pets',None)
    for cid,c in creatures:
        if pets and pets.is_pet(cid):continue
        pos=(c.get('x'),c.get('y'),c.get('z'));tile=cells.get(pos,{})
        if cid==gs.player_id or type(cid)is not int or not 0<cid<2**32 or c.get('source')!='dll' or not 0<=now-c.get('t',0)<=.6 or not 0<c.get('health',0)<=100:continue
        if pos not in accessible:continue
        if pos[2]!=gs.position[2] or tile.get('occupants')!=1 or tile.get('occupant_types',{}).get('monster')!=1:continue
        if pets and pets.confirmed_after_map(pos,sample.get('captured_at')):continue
        if sum((v.get('x'),v.get('y'),v.get('z'))==pos for _,v in creatures)!=1:continue
        distance=max(abs(pos[0]-gs.position[0]),abs(pos[1]-gs.position[1]))
        if config.get('engage','near')!='visible' and distance>config['radius']:continue
        for rank,rule in enumerate(config['rules']):
            if matches(rule['pattern'],c.get('name','')):
                if rule['action']=='attack':result.append(dict(id=cid,name=c.get('name','?'),position=list(pos),health=c['health'],distance=distance,priority=rule.get('priority',max(1,100-rank)),rule_index=rank))
                break
    return result


def target_policy(config,target):
    """An ordered monster match overrides movement and spells independently."""
    rule=config['rules'][target['rule_index']]
    stance=rule.get('stance','inherit');spell_mode=rule.get('spell_mode','inherit')
    mode=config['mode'] if stance=='inherit' else stance
    return dict(mode=mode,distance=config['distance'] if stance=='inherit' else rule.get('distance',config['distance']),
                spells=config['spells'] if spell_mode=='inherit' else [rule['spell']] if spell_mode=='custom' else [])


def attack_distance_ok(config,target):
    policy=target_policy(config,target)
    return policy['mode']!='distance' or target['distance']>=policy['distance']


def choose_target(candidates,config,current=0):
    strategy=config['strategy']
    def order(c):
        value={'nearest':c['distance'],'lowest_hp':c['health'],'highest_hp':-c['health'],'finish':0 if c['id']==current else 1}[strategy]
        return -c['priority'],value,c['distance'],c['id']
    return min(candidates,key=order) if candidates else None


class CombatRuntime:
    """Single movement owner: chase returns to its route anchor; lure follows route."""
    def __init__(self,owner,config):
        self.owner=owner;self.config=config;self.target=None;self.sent_target=0
        self.phase='route';self.lure_started=None;self.battle_started=None;self.progress_at=0;self.last_health=None
        self.pursuit=PursuitTracker();self.lure_waiting=False;self.lure_stop_reason=None
        self.placement_checked=False;self.placement=None;self.placement_group=set()
        self.anchor=None;self.deadlines={};self.cast_after=0;self.manual_override=False;self.policy_mode=None
        self.spell_checks=[];self.last_spell_attempt=None
        self.wave_turn=None;self.manual_target=0;self.manual_target_at=0
        self.blocker_id=0;self.blocker_since=None
        self.engaged_count=0;self.nearby_count=0;self.transition_clear_since=None;self.group_clear_since=None
        self.stalled_targets={};self.defer_movement=False;self.movement_intent=None

    def snapshot(self):
        lure=None
        if self.policy_mode=='lure' and not self.owner.combat_only:
            lure=dict(following=len(self.pursuit.following),goal=self.config['lure_count'],
                      waiting=self.lure_waiting,stop_reason=self.lure_stop_reason,engaged=self.engaged_count,nearby=self.nearby_count)
        return dict(phase=self.phase,target=self.target,enabled=self.config['enabled'],lure=lure,return_anchor=self.anchor,
                    spells=self.spell_checks,last_spell_attempt=self.last_spell_attempt)

    def reset_lure(self):
        self.pursuit.reset();self.lure_started=None;self.lure_waiting=False;self.lure_stop_reason=None;self.engaged_count=0;self.nearby_count=0
        self.blocker_id=0;self.blocker_since=None;self.group_clear_since=None
        self.placement_checked=False;self.placement=None;self.placement_group.clear()

    def positioning_step(self,origin,target):
        if not self.placement:return False
        start,path,expires=self.placement
        index=self.owner.index-start
        return (time.monotonic()<expires and 0<=index<len(path)-1
                and path[index:index+2]==(tuple(origin),tuple(target)))

    @property
    def pacing_limit(self):
        limit = self.config['lure_distance']
        return limit if self.config['engage']=='visible' else min(limit, self.config['radius'])

    def placement_target(self, sample, targets, physical, boundary):
        if not self.placement or boundary or self.manual_target:
            return None
        start, path, expires = self.placement
        index = self.owner.index - start
        origin = tuple(self.owner.state.game_state.position)
        if time.monotonic() >= expires or not 0 <= index < len(path)-1 or path[index] != origin:
            return None
        target = path[index+1]
        planned = next((c for c in sample['cells'] if (c['x'],c['y'],c['z'])==target), {})
        members = {tuple(c['position']) for c in targets if c['id'] in self.placement_group}
        if len(members) != len(self.placement_group):
            return None
        new_contact = any(point not in members and (dist<=1 or gap(target,point)<dist and gap(target,point)<=2)
                          for point,dist in physical)
        if (planned.get('kind')=='clear' and not planned.get('occupants',0)
                and not planned.get('navigation') and not planned.get('stairs')
                and not new_contact and self.engaged_count<=self.config['lure_count']
                and not group_needs_time(sample['cells'], origin, target, members, self.pacing_limit)):
            return target
        return None

    def allow_positioning(self,sample,targets,physical,boundary):
        target = self.placement_target(sample,targets,physical,boundary)
        o = self.owner
        return bool(target and o.index+1 < len(o.path) and tuple(o.path[o.index+1]) == target)

    def gathering_wait(self, sample, targets, followers):
        o = self.owner
        if o.index+1 >= len(o.path):
            return False
        tracked = followers | self.pursuit.recent_contacts(time.monotonic())
        members = {tuple(c['position']) for c in targets if c['id'] in tracked}
        return group_needs_time(sample['cells'], o.state.game_state.position, o.path[o.index+1],
                                members, self.pacing_limit)

    def visible_group(self,sample):
        """Occupancy also covers a transient DLL identity gap. Never authorizes an attack."""
        gs=self.owner.state.game_state;pos=gs.position
        accessible=reachable_tiles(sample['cells'],pos);result=[]
        pets=getattr(gs,'pets',None)
        for tile in sample['cells']:
            point=(tile['x'],tile['y'],tile['z'])
            monsters=tile.get('occupant_types',{}).get('monster',0)
            if point not in accessible or not monsters:continue
            if pets and pets.confirmed_after_map(point,sample.get('captured_at'))>=monsters:continue
            gap=max(abs(point[0]-pos[0]),abs(point[1]-pos[1]))
            if self.config['engage']!='visible' and gap>self.config['radius']:continue
            rows=[(cid,c) for cid,c in gs.creatures.items() if (c.get('x'),c.get('y'),c.get('z'))==point and not (pets and pets.is_pet(cid))]
            # A fresh known ignored/dead monster is not a reason to keep waiting.
            ignored=False
            if len(rows)==1:
                cid,row=rows[0]
                if row.get('source')=='dll' and 0<=time.time()-row.get('t',0)<.6:
                    rule=next((r for r in self.config['rules'] if matches(r['pattern'],row.get('name',''))),None)
                    ignored=row.get('health',0)<=0 or not rule or rule['action']!='attack' or cid in self.stalled_targets
            if not ignored:result.append((point,gap))
        return result

    def latch_lure(self,reason):
        if self.lure_stop_reason is None:self.lure_stop_reason=reason
        if self.battle_started is None:
            self.battle_started=time.monotonic();self.progress_at=self.battle_started
            if self.anchor is None:self.anchor=list(self.owner.state.game_state.position)
        self.phase='fight';self.lure_waiting=False

    def route_step_allowed(self,boundary=False):
        """Recheck at enqueue AND dispatch, after any awaited spell/attack."""
        if not self.config['enabled'] or self.owner.combat_only:return True
        sample=self.owner.map_now();targets=self.candidates(sample)
        mode=self.policy_mode or self.config['mode']
        if mode!='lure':return not targets
        physical=self.visible_group(sample)
        following=self.refresh_lure_limit(targets,physical,boundary)
        if self.lure_stop_reason or self.battle_started:
            return self.allow_positioning(sample,targets,physical,boundary)
        return not self.gathering_wait(sample, targets, following)

    def refresh_lure_limit(self,targets,physical,boundary):
        adjacent={c['id'] for c in targets if c['distance']<=1}
        following={c['id'] for c in targets if c['id'] in self.pursuit.following}
        self.nearby_count=max(len(adjacent),sum(gap<=1 for _,gap in physical))
        known_positions={tuple(c['position']) for c in targets if c['id'] in adjacent|following}
        self.engaged_count=len(adjacent|following)+sum(gap<=1 and point not in known_positions for point,gap in physical)
        if self.engaged_count>=self.config['lure_count']:self.latch_lure('group')
        elif boundary and (targets or physical):self.latch_lure('transition')
        return following

    def candidates(self,sample=None):
        rows=eligible_monsters(self.owner.state.game_state,sample if sample is not None else self.owner.map_now(),self.config)
        now=time.monotonic()
        self.stalled_targets={cid:t for cid,t in self.stalled_targets.items() if t>now}
        return [row for row in rows if row['id'] not in self.stalled_targets]

    def blocking_target(self,candidates,sample):
        o=self.owner
        if o.combat_only or self.manual_target or o.index+1>=len(o.path):return None
        pos=list(o.state.game_state.position);target=o.path[o.index+1]
        if pos!=o.path[o.index]:return None
        edge=next((e for e in o.edges if e['origin']==pos and e['exit']==target),None)
        if edge:target=edge['entry']
        if target[2]!=pos[2] or max(abs(target[i]-pos[i]) for i in (0,1))!=1:return None
        tile=next((c for c in sample['cells'] if [c['x'],c['y'],c['z']]==target),{})
        if tile.get('kind')!='clear' or tile.get('navigation') and not edge:return None
        return next((c for c in candidates if c['position']==target and target_policy(self.config,c)['mode']=='lure'),None)

    def still_target(self,cid):
        candidates=self.candidates()
        if self.blocker_id==cid:
            blocker=self.blocking_target(candidates,self.owner.map_now())
            if not blocker or blocker['id']!=cid:return False
        return (not self.manual_target or self.manual_target==cid) and any(c['id']==cid and attack_distance_ok(self.config,c) for c in candidates)

    def select_target(self,candidates):
        if self.manual_target:
            picked=next((c for c in candidates if c['id']==self.manual_target),None)
            if picked:return picked
            raw=self.owner.state.game_state.creatures.get(self.manual_target)
            # Keep a live manual choice even when a policy excludes it. Do not
            # change to a different monster during a transient map/DLL gap.
            if time.monotonic()-self.manual_target_at<.8 or (raw and raw.get('health',0)>0 and time.time()-raw.get('t',0)<1):return None
            self.manual_target=0;self.sent_target=0;self.manual_override=False
        return choose_target(candidates,self.config,self.sent_target)

    def accept_manual_target(self,cid):
        self.owner.generation+=1  # expire already queued attacks, turns and casts
        self.manual_target=cid;self.manual_target_at=time.monotonic()
        self.sent_target=cid;self.manual_override=True;self.wave_turn=None
        self.last_health=None;self.battle_started=None

    def wave_allowed(self,spell,cid,origin,direction,after=0,require_facing=False):
        gs=self.owner.state.game_state
        if list(gs.position)!=origin or not self.still_target(cid):return False
        sample=self.owner.map_now();targets=self.candidates()
        target=next((c for c in targets if c['id']==cid),None)
        check=evaluate_spell(spell,gs,targets,target,sample=sample)
        return (check['conditions_met'] and check['wave_direction']==direction and
                (not require_facing or facing(gs,after)==direction))

    async def orient_wave(self,spell,cid,check):
        from protocol import Direction,build_turn_packet
        direction=check['wave_direction']
        if direction is None:return True
        o=self.owner;origin=list(o.state.game_state.position)
        key=(spell['words'].casefold(),cid,tuple(origin),direction,o.generation)
        if self.wave_turn and self.wave_turn[0]!=key:self.wave_turn=None
        after=self.wave_turn[1] if self.wave_turn else 0
        if facing(o.state.game_state,after)==direction:return True
        check.update(reason='Wave: czekam na potwierdzenie obrotu',ready=False)
        if self.wave_turn and time.monotonic()-after<1:return False
        stamp=time.monotonic();self.wave_turn=(key,stamp)
        sent=await o.send(build_turn_packet(Direction(direction)),lambda:
            self.wave_turn==(key,stamp) and self.wave_allowed(spell,cid,origin,direction),skip_if_changed=True)
        if not sent:self.wave_turn=None
        return False  # never cast in the same tick as an unacknowledged turn

    async def cancel_attack(self):
        if not self.sent_target or self.manual_override:return
        from protocol import build_attack_packet
        owner=self.owner;generation=owner.generation;stamp=time.monotonic()
        from session_lifetime import SessionLease
        from command_delivery import delivery,attempted
        lease=SessionLease(owner.state)
        if owner.state.connected:
            receipt=await delivery(await owner.state.game_proxy.inject_to_server(build_attack_packet(0),guard=lambda:
                lease.valid() and getattr(owner.state,'adaptive_cavebot',None) is owner and owner.generation==generation and
                time.monotonic()-stamp<.5 and not self.manual_override)
            )
            if attempted(receipt):self.sent_target=0

    async def request_step(self,target,validate=None):
        if not self.defer_movement:
            return await self.owner.step(target,validate=validate)
        self.movement_intent=dict(origin=list(self.owner.state.game_state.position),target=list(target),
                                  validate=validate,expires=time.monotonic()+.5)
        return True

    async def tick(self,sample,at_transition=False,allow_movement=True):
        """True consumes this iteration; False allows one normal route step."""
        import asyncio
        from protocol import build_attack_packet,build_say_packet
        from adaptive_cavebot import detour,distance,walkable
        o=self.owner;cfg=self.config
        if not cfg['enabled']:return False
        candidates=self.candidates(sample)
        blocker=self.blocking_target(candidates,sample)
        current=blocker or self.select_target(candidates)
        now=time.monotonic();pos=list(o.state.game_state.position)
        if not blocker and self.blocker_since is not None:
            if self.lure_started is not None:self.lure_started+=now-self.blocker_since
            self.blocker_since=None;self.blocker_id=0;self.last_health=None
        if current is not None or not at_transition:self.transition_clear_since=None
        if current is None and at_transition and not o.combat_only:
            # Allow the observer to publish creature identities at a floor edge.
            # A single empty identity frame is not proof that the area is empty.
            if self.transition_clear_since is None:self.transition_clear_since=now
            if now-self.transition_clear_since<.4:
                o.reason='Sprawdzam dostępne cele przed przejściem.'
                await asyncio.sleep(.05);return True
        if current is not None:self.group_clear_since=None
        if current is None and not o.combat_only and (self.policy_mode=='lure' or cfg['mode']=='lure'):
            physical=self.visible_group(sample)
            if physical:
                self.group_clear_since=None;o.reason='Sprawdzam odczyt grupy — ruch czeka, walka pozostaje aktywna.'
                await asyncio.sleep(.05);return True
            if self.battle_started is not None or self.lure_stop_reason:
                if self.group_clear_since is None:self.group_clear_since=now
                if now-self.group_clear_since<.4:
                    o.reason='Potwierdzam koniec walki przed dalszą trasą.'
                    await asyncio.sleep(.05);return True
        if current is None:
            self.spell_checks=evaluate_spells(cfg['spells'],o.state.game_state,candidates,None,now,self.deadlines,self.cast_after,sample)
            await self.cancel_attack();self.target=None;self.phase='route';self.reset_lure();self.battle_started=None;self.last_health=None;self.policy_mode=None
            # Local recovery/route progression already placed us on the current
            # leg. An older fight anchor must not pull us back or wait forever
            # on an offscreen/now-blocked tile (e.g. a log or a closed door).
            if not o.combat_only and o.path and pos==o.path[o.index]:
                self.anchor=None
            if allow_movement and self.anchor and pos!=self.anchor:
                self.phase='return';o.reason='Powrót na trasę po walce.'
                path=detour(sample['cells'],pos,self.anchor)
                if not path or len(path)<2:
                    o.describe_blockage(sample,self.anchor)
                    o.reason='Powrót po walce — '+o.reason
                    await asyncio.sleep(.05);return True
                if not await self.request_step(path[1]):o.reason='Powrót po walce: ponownie sprawdzam pozycję.'
                return True
            self.anchor=None
            return False
        self.target=current
        policy=target_policy(cfg,current);mode=policy['mode'];gap_distance=policy['distance']
        if mode!=self.policy_mode:
            self.policy_mode=mode;self.reset_lure();self.battle_started=None
        if blocker:
            self.blocker_id=blocker['id']
            if self.blocker_since is None:self.blocker_since=now
        luring=False
        followers=[]
        if mode=='lure' and not o.combat_only:
            followers=self.pursuit.update(pos,candidates,now)
            adjacent={c['id'] for c in candidates if c['distance']<=1}
            # Capacity is a safety stop, not a claim about aggro ownership.
            # Adjacent eligible monsters count even before two pursuit samples.
            physical=self.visible_group(sample)
            self.nearby_count=max(len(adjacent),sum(gap<=1 for _,gap in physical))
            engaged=adjacent|{c['id'] for c in followers}
            known_positions={tuple(c['position']) for c in candidates if c['id'] in engaged}
            self.engaged_count=len(engaged)+sum(gap<=1 and point not in known_positions for point,gap in physical)
            if self.battle_started is None:
                # Seeing an uncollected monster ahead is an approach, not a
                # gathering interval. Start the limit when a real group forms.
                if self.lure_started is None and self.engaged_count:self.lure_started=now
                if self.engaged_count>=cfg['lure_count']:self.lure_stop_reason='group'
                elif at_transition:self.lure_stop_reason='transition'
                elif self.lure_started is not None and (self.blocker_since or now)-self.lure_started>=cfg['lure_seconds']:self.lure_stop_reason='timeout'
                luring=self.lure_stop_reason is None and allow_movement
            if self.lure_stop_reason and not self.placement_checked:
                self.placement_checked=True
                if (self.lure_stop_reason in {'group','timeout'} and allow_movement and not at_transition
                        and not blocker and not self.manual_target and self.engaged_count==len(engaged)
                        and self.engaged_count<=cfg['lure_count'] and list(o.path[o.index])==pos):
                    members=[c['position'] for c in candidates if c['id'] in engaged]
                    other=[point for point,_ in physical if point not in known_positions]
                    other.extend(c['position'] for c in candidates if c['id'] not in engaged)
                    route=lure_stopping_path(sample['cells'],o.path[o.index:o.index+4],o.edges,members,other,self.pacing_limit)
                    if route:
                        self.placement=(o.index,route,now+2)
                        self.placement_group=set(engaged)
        if blocker and self.lure_stop_reason:
            blocker=None;self.blocker_id=0;self.blocker_since=None
        self.lure_waiting=False
        if not luring and not blocker and self.battle_started is None and (mode!='lure' or self.lure_stop_reason):
            self.battle_started=now;self.progress_at=now
            if self.anchor is None and not o.combat_only:self.anchor=pos
        progress=(current['id'],current['health'])
        if progress!=self.last_health:self.last_health=progress;self.progress_at=now
        if now-self.progress_at>20:
            self.stalled_targets[current['id']]=now+3
            o.logs.append(f'Cel {current["name"]} nie traci HP od 20 s — pomijam na 3 s; pozostała walka aktywna.')
            await self.cancel_attack();self.target=None;self.last_health=None
            # Do not pause the shared route/combat owner for one failed target.
            await asyncio.sleep(.05);return True
        self.phase='fight';o.reason=f'Walka: {current["name"]} · {current["health"]}% HP.'
        if self.lure_stop_reason:
            label={'group':'Grupa zebrana','transition':'Przejście lub koniec odcinka','timeout':'Limit czasu zbierania'}[self.lure_stop_reason]
            o.reason=f'{label} — stoję i walczę: {current["name"]} · {current["health"]}% HP.'
        cid=current['id']
        can_attack=attack_distance_ok(cfg,current)
        if not can_attack:
            await self.cancel_attack()
        elif self.sent_target!=cid:
            if await o.send(build_attack_packet(cid),lambda:self.still_target(cid),skip_if_changed=True):
                if getattr(getattr(o,'last_receipt',None),'status','dispatched')=='dispatched':
                    self.sent_target=cid
            if self.sent_target!=cid:
                o.reason='Wybór celu nie został wysłany jednoznacznie — ponawiam ocenę.'
                return True  # do not cast a targeted spell at an older server target
        gs=o.state.game_state
        self.spell_checks=evaluate_spells(policy['spells'],gs,candidates,current,now,self.deadlines,self.cast_after,sample)
        if not can_attack:
            for row in self.spell_checks:row.update(ready=False,selected=False,reason='Strategia dystansowa: cel za blisko')
        for index,spell in enumerate(policy['spells'] if can_attack else []):
            if not self.spell_checks[index]['selected']:continue
            check=self.spell_checks[index]
            if not await self.orient_wave(spell,cid,check):break
            key=spell['words'].strip().casefold()
            origin=list(gs.position);direction=check['wave_direction']
            after=self.wave_turn[1] if direction is not None and self.wave_turn else 0
            def spell_allowed(spell=spell,origin=origin,direction=direction,after=after):
                if direction is not None:
                    return self.wave_allowed(spell,cid,origin,direction,after,True)
                if not self.still_target(cid):return False
                targets=self.candidates();target=next((c for c in targets if c['id']==cid and attack_distance_ok(cfg,c)),None)
                # Queue guards re-run after our cooldown has been reserved.
                # Revalidate live conditions, not that newly reserved deadline.
                return evaluate_spell(spell,gs,targets,target,sample=self.owner.map_now())['conditions_met']
            try:sent=await o.send(build_say_packet(spell['words']),spell_allowed,skip_if_changed=True)
            except asyncio.CancelledError:
                pending=getattr(o,'last_receipt',None)
                if pending is not None and pending.attempted:
                    stamp=time.monotonic()
                    self.deadlines[key]=stamp+spell['cooldown'];self.cast_after=stamp+spell['cooldown']
                    self.last_spell_attempt=dict(words=spell['words'],at=time.time(),status=pending.status)
                raise
            if sent:
                stamp=time.monotonic()
                self.deadlines[key]=stamp+spell['cooldown'];self.cast_after=stamp+spell['cooldown']
                self.last_spell_attempt=dict(words=spell['words'],at=time.time(),status=getattr(getattr(o,'last_receipt',None),'status','dispatched'))
            else:
                self.spell_checks[index].update(ready=False,selected=False,reason='Warunki zmieniły się — ponowię ocenę')
            break
        if blocker:
            self.phase='clear_path';o.reason=f'Odblokowuję drogę: {current["name"]} · {current["health"]}% HP. Potem wrócę do lurowania.'
            await asyncio.sleep(.05);return True
        if allow_movement and self.placement:
            def next_placement():
                fresh = o.map_now()
                return self.placement_target(fresh, self.candidates(fresh), self.visible_group(fresh), at_transition)
            target = next_placement()
            if target is not None:
                self.phase='positioning';o.reason='Podchodzę do miejsca z lepszym dostępem dla grupy.'
                if o.index+1 < len(o.path) and target == tuple(o.path[o.index+1]):
                    return False
                await self.request_step(list(target), validate=lambda: next_placement() == target)
                return True
            self.placement=None
        # A third monster may arrive while ATTACK/SAY waits in the queue.
        if luring:
            fresh=o.map_now()
            latest=self.candidates(fresh)
            self.refresh_lure_limit(latest,self.visible_group(fresh),at_transition)
            luring = self.lure_stop_reason is None
        if luring:
            self.phase='lure'
            # ATTACK/SAY may have awaited a queue: classify fresh positions and
            # the current route. Distant unconfirmed monsters must not freeze
            # the approach, and a follower now ahead must not hold us back.
            confirmed={c['id'] for c in followers}
            position=list(o.state.game_state.position)
            ahead=lambda c:route_approaches(position,c['position'],o.path,o.index,o.edges)
            waiting=self.gathering_wait(fresh, latest, confirmed)
            self.lure_waiting=waiting
            activity='czekam na grupę z tyłu' if waiting else 'podchodzę trasą do mobów' if any(ahead(c) for c in latest) else 'zbieram grupę'
            o.reason=f'W grupie: {self.engaged_count}/{cfg["lure_count"]} · '+activity+' · atak aktywny.'
            if waiting:await asyncio.sleep(.05)
            return waiting
        needs_distance=mode=='distance' and not gap_distance<=current['distance']<=min(7,gap_distance+1)
        if allow_movement and not o.combat_only and (needs_distance or mode=='chase' and current['distance']>gap_distance):
            paths=[]
            for cell in sample['cells']:
                p=[cell['x'],cell['y'],cell['z']]
                target_gap=distance(p,current['position'])
                in_range=(gap_distance<=target_gap<=min(7,gap_distance+1)) if mode=='distance' else target_gap<=gap_distance
                if not walkable(cell) or not in_range or distance(p,self.anchor)>cfg['radius']:continue
                path=detour(sample['cells'],pos,p)
                # Do not close the gap further while retreating, or cross melee
                # range while approaching a ranged position. Replan every step.
                minimum=min(current['distance'],gap_distance) if mode=='distance' else 0
                if path and len(path)>1 and all(distance(step,self.anchor)<=cfg['radius'] and distance(step,current['position'])>=minimum for step in path):paths.append(path)
            if not paths:
                o.reason='Brak dojścia do celu — czekam na drogę; walka aktywna.'
                await asyncio.sleep(.05);return True
            path=min(paths,key=lambda p:(len(p),distance(p[-1],self.anchor)));self.phase='distance' if mode=='distance' else 'chase'
            o.reason=f'Dystans: {current["name"]} · {current["distance"]} → {gap_distance} SQM.' if mode=='distance' else f'Pościg: {current["name"]}.'
            def movement_allowed():
                return any(c['id']==cid and (mode!='distance' or distance(path[1],c['position'])>=min(c['distance'],gap_distance)) for c in self.candidates())
            if not await self.request_step(path[1],validate=movement_allowed):o.reason='Ruch w walce nie potwierdził kroku — sprawdzam ponownie.'
        elif o.combat_only and not can_attack:
            self.phase='distance';o.reason=f'{current["name"]}: za blisko ({current["distance"]}/{gap_distance} SQM). Ruch pozostaje ręczny.'
            await asyncio.sleep(.05)
        else:await asyncio.sleep(.05)
        return True
