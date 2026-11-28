"use client";
import { t } from "@/lib/i18n";
import {useState} from "react";
import {defaultMonsterRule,defaultCombatSpell,monsterRuleSummary,moveRule,type MonsterRule} from "@/lib/cavebotModel";
import SpellPicker,{SpellCooldown} from './SpellPicker';
import SpellConditions from './SpellConditions';

export default function MonsterRules({rules,onChange,movement,disabled=false}:{rules:MonsterRule[];onChange:(rules:MonsterRule[])=>void;movement:boolean;disabled?:boolean}) {
  const [editing,setEditing]=useState<number|null>(null);
  function update(index:number,patch:Partial<MonsterRule>){onChange(rules.map((rule,i)=>i===index?{...rule,...patch}:rule));}
  function reorder(index:number,delta:number){onChange(moveRule(rules,index,delta));setEditing(index+delta);}
  return <details className="v2-options monster-rules">
    <summary>{t("rules.monsters")} <span>{rules.length}</span></summary>
    <div className="monster-rules-actions"><button disabled={disabled||rules.length>=32} onClick={()=>onChange([...rules,defaultMonsterRule()])}>＋ {t("rules.add")}</button></div>
    {!rules.length&&<p className="v2-inline-note">{t("rules.empty")}</p>}
    {!!rules.length&&<div className="v2-rule-caption"><span>{t("rules.caption")}</span><span title={t("rules.priorityHint")}>{t("rules.priority")} ↑</span></div>}
    {rules.map((rule,i)=>{
      const stance=rule.stance??'inherit',spellMode=rule.spell_mode??'inherit',spell=rule.spell??defaultCombatSpell();
      return <div className={`monster-rule ${editing===i?'is-editing':''}`} key={i}>
        <div className="v2-rule"><b>{i+1}</b>
          <input aria-label={t("rules.monster",{n:i+1})} placeholder={t("rules.placeholder")} value={rule.pattern} onChange={e=>update(i,{pattern:e.target.value})}/>
          <select aria-label={t("rules.action",{n:i+1})} value={rule.action} onChange={e=>update(i,{action:e.target.value as MonsterRule['action']})}><option value="attack">{t("rule.attack")}</option><option value="ignore">{t("rule.ignore")}</option></select>
          <input className="v2-priority" aria-label={t("rules.priorityAria",{n:i+1})} title={t("rules.priorityTitle")} type="number" min={0} max={100} value={rule.priority} onChange={e=>update(i,{priority:Number(e.target.value)})}/>
          <button title={t("rules.strategyTitle",{summary:monsterRuleSummary(rule)})} aria-label={t("rules.strategy",{n:i+1})} aria-expanded={editing===i} onClick={()=>setEditing(editing===i?null:i)}>⚙</button>
          <button aria-label={t("rules.remove",{n:i+1})} onClick={()=>{onChange(rules.filter((_,j)=>j!==i));setEditing(null);}}>×</button>
        </div>
        {rule.action==='attack'&&monsterRuleSummary(rule)!==t('rule.attack')&&<small className="monster-rule-summary">{monsterRuleSummary(rule)}</small>}
        {editing===i&&<div className="monster-rule-options">
          {rule.action==='attack'&&<><div className="monster-rule-fields">
            <label>{t("run.movement")}<select aria-label={t("rules.movementAria",{n:i+1})} value={stance} onChange={e=>{const next=e.target.value as MonsterRule['stance'];update(i,{stance:next,distance:next==='distance'?Math.max(2,rule.distance??3):rule.distance??1});}}>
              <option value="inherit">{t("rules.fromRoute")}</option><option value="stop">{t("rules.stop")}</option><option value="chase">{t("rules.chase")}</option><option value="distance">{t("rules.distance")}</option><option value="lure">{t("rules.lure")}</option>
            </select></label>
            {(stance==='distance'||stance==='chase')&&<label>{t("rules.gap")}<input aria-label={t("rules.distanceAria",{n:i+1})} type="number" min={stance==='distance'?2:1} max={7} value={rule.distance??3} onChange={e=>update(i,{distance:Number(e.target.value)})}/></label>}
            <label>{t("spellPicker.spells")}<select aria-label={t("rules.spellsAria",{n:i+1})} value={spellMode} onChange={e=>update(i,{spell_mode:e.target.value as MonsterRule['spell_mode'],spell:e.target.value==='custom'?spell:null})}>
              <option value="inherit">{t("rules.shared")}</option><option value="none">{t("rule.noSpells")}</option><option value="custom">{t("rules.chosenSpell")}</option>
            </select></label>
          </div>
          {spellMode==='custom'&&<div className="monster-rule-spell"><div className="spell-picker-field"><span>{t("rules.spell")}</span><SpellPicker label={t("rules.spellAria",{n:i+1})} value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/></div>
            <SpellCooldown label={t("rules.spellInterval",{n:i+1})} value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/>
            <SpellConditions value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/>
          </div>}
          {stance==='distance'&&<small>{t(movement?'rules.distanceAuto':'rules.distanceManual')}</small>}
          {!movement&&(stance==='lure'||stance==='chase')&&<small>{t("rules.movementOff")}</small>}
          </>}
          <div className="monster-rule-order"><button aria-label={t("rules.up",{n:i+1})} disabled={!i} onClick={()=>reorder(i,-1)}>↑ {t("rules.higher")}</button><button aria-label={t("rules.down",{n:i+1})} disabled={i===rules.length-1} onClick={()=>reorder(i,1)}>↓ {t("rules.lower")}</button><button onClick={()=>setEditing(null)}>{t("common.done")}</button></div>
        </div>}
      </div>;
    })}
  </details>;
}
