export type Position=[number,number,number];
export type LureProgress={engaged?:number;nearby?:number;following:number;goal:number;waiting:boolean;stop_reason:'group'|'timeout'|'transition'|'corridor'|'origin_limit'|'clear_pack'|null};
export type ConditionOp='>'|'>='|'='|'<='|'<';
export type CombatSpell={words:string;trigger:"visible"|"distance";cooldown:number;ki:number;range:number;count:number;
  aim?:'target'|'self';ki_op?:ConditionOp;ki_unit?:'percent'|'value';hp?:number|null;hp_op?:ConditionOp;hp_unit?:'percent'|'value';range_op?:ConditionOp;count_op?:ConditionOp};
export type SpellCheck={words:string;aim:'target'|'self';matched_count:number;ready:boolean;conditions_met:boolean;remaining:number;reason:string;selected:boolean;wave_note?:string|null;wave_direction?:number|null;turn_required?:boolean};
export type MonsterRule={pattern:string;action:"attack"|"ignore";priority:number;
  stance?:"inherit"|"stop"|"chase"|"lure"|"distance";distance?:number;spell_mode?:"inherit"|"none"|"custom";spell?:CombatSpell|null};
export type CombatConfig={enabled:boolean;mode:"stop"|"chase"|"lure";strategy:"nearest"|"lowest_hp"|"highest_hp"|"finish";engage:"visible"|"near";radius:number;distance:number;lure_count:number;lure_seconds:number;lure_distance:number;lure_origin_limit?:number;
  rules:MonsterRule[];spells:CombatSpell[]};
export const defaultMonsterRule=():MonsterRule=>({pattern:"",action:"attack",priority:50,stance:"inherit",spell_mode:"inherit"});
export const defaultCombatSpell=():CombatSpell=>({words:"",trigger:"distance",cooldown:1,ki:10,range:5,count:1});
export function monsterRuleSummary(rule:MonsterRule):string {
  if(rule.action==='ignore')return 'Ignoruj';
  const stance={inherit:'',stop:'Stój',chase:'Podejdź',lure:'Luruj',distance:'Dystans'}[rule.stance??'inherit'];
  const movement=stance+(['chase','distance'].includes(rule.stance??'')?` ${rule.distance??3} SQM`:'');
  const spell=rule.spell_mode==='none'?'Bez czarów':rule.spell_mode==='custom'?(rule.spell?.words||'Wpisz czar'):'';
  return [movement,spell].filter(Boolean).join(' · ')||'Zaznacz i atakuj';
}
export type DraftTag={text:string;color:"mint"|"blue"|"amber"|"rose"|"violet"};
export type RouteDraft={navigation_revision?:string;id:string;name:string;tags?:DraftTag[];points:Position[];labels:string[];links?:{from_index:number;id:string}[];loop:boolean;updated:number;combat:CombatConfig};
export const defaultCombat=():CombatConfig=>({enabled:false,mode:"stop",strategy:"nearest",engage:"near",radius:5,distance:1,lure_count:4,lure_seconds:8,lure_distance:3,lure_origin_limit:12,rules:[],spells:[]});
export type TargetPreview={message:string;targets:{id:number;name:string;health:number;priority:number;distance:number}[];selected:{id:number}|null;spells?:SpellCheck[]};
export function parseTargetPreview(response:unknown):TargetPreview {
  if(!response || typeof response!=="object")throw Error("Nieprawidłowa odpowiedź podglądu celów.");
  const envelope=response as Record<string,unknown>;
  const result=(envelope.message && typeof envelope.message==="object"?envelope.message:envelope) as Partial<TargetPreview>;
  if(typeof result.message!=="string" || !Array.isArray(result.targets) || result.targets.some(t=>!t || typeof t.name!=="string" || ![t.id,t.health,t.priority,t.distance].every(Number.isFinite)) || (result.selected!=null && !Number.isFinite(result.selected.id)))throw Error("Nieprawidłowa odpowiedź podglądu celów — spróbuj ponownie.");
  if(result.spells!==undefined&&(!Array.isArray(result.spells)||result.spells.some(s=>!s||typeof s.words!=='string'||typeof s.reason!=='string'||!Number.isFinite(s.matched_count))))throw Error('Nieprawidłowy podgląd warunków czarów.');
  return {message:result.message,targets:result.targets,selected:result.selected??null,...(result.spells?{spells:result.spells}:{})};
}
export function waypointKind(points:Position[],index:number){if(index===0)return "Start";const d=points[index][2]-points[index-1][2];return d<0?`↑ ${-d}`:d>0?`↓ ${d}`:index===points.length-1?"Koniec":"Idź";}
export function moveRule<T>(rows:T[],index:number,delta:number):T[]{const next=[...rows],to=index+delta;if(to>=0&&to<rows.length)[next[index],next[to]]=[next[to],next[index]];return next;}

export function usesRoute(movement:boolean,route?:Pick<RouteDraft,'points'>):boolean {
  return movement && !!route && route.points.length>=2;
}
export function v2StartRequest(movement:boolean,route:RouteDraft|undefined,combat:CombatConfig,loop:boolean,scope:string) {
  if(usesRoute(movement,route)) {
    return {command:'start',scope,draft_id:route!.id,loop};
  }
  if(!combat.enabled || !combat.rules.some(r=>r.action==='attack'&&r.pattern.trim()))throw Error('Włącz walkę i dodaj potwory.');
  return {command:'start_combat',combat:{...combat,mode:'stop' as const}};
}


export type WaypointProgress={navigation_revision?:string|null;current:number|null;last_reached:number|null;total:number;draft_updated:number|null;tolerated:number};
export function visibleWaypointProgress(run:{draft_id?:string;waypoint?:WaypointProgress}|null|undefined,draftId:string,updated:number|undefined,dirty=false,navigationRevision?:string):WaypointProgress|null {
  const p=run?.waypoint;
  if(dirty||!draftId||run?.draft_id!==draftId||!p||!p.total)return null;
  if(navigationRevision&&p.navigation_revision ? navigationRevision!==p.navigation_revision : p.draft_updated!==updated)return null;
  if(p.current!==null&&(!Number.isInteger(p.current)||p.current<0||p.current>=p.total))return null;
  return p;
}
