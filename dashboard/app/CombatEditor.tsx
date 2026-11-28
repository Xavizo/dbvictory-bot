"use client";
import { t } from "@/lib/i18n";
import Help from "@/app/Help";
import {clampInt,defaultCombatSpell,moveRule,SCREEN_RANGE,type CombatConfig} from "@/lib/cavebotModel";
import ActivationCheckbox from "./ActivationCheckbox";
import MonsterRules from "./MonsterRules";
import SpellPicker,{SpellCooldown} from "./SpellPicker";
import SpellConditions from "./SpellConditions";
import {LureSettings} from "./LureStatus";

/** Combat settings shared by the cavebot and Walka: selection, Potwory, Techniki and Dodatkowe ustawienia. */
export default function CombatEditor({combat,onChange,disabled,movement}:{combat:CombatConfig;onChange:(next:CombatConfig)=>void;disabled:boolean;movement:boolean}) {
  const change=onChange;
  const hasLure=movement&&(combat.mode==='lure'||combat.rules.some(r=>r.action==='attack'&&r.stance==='lure'));
  const setSpell=(i:number,next:CombatConfig['spells'][number])=>change({...combat,spells:combat.spells.map((v,j)=>j===i?next:v)});
  return <>
    {movement&&<div className="combat-behavior" role="group" aria-label={t("workspace.behavior")}>{(['stop','chase','lure'] as const).map(mode=><button key={mode} type="button" aria-pressed={combat.mode===mode} title={t(`workspace.mode.${mode}.hint`)} onClick={()=>change({...combat,mode})}>{t(`workspace.mode.${mode}`)}</button>)}</div>}
    <div className="v2-engage"><label>{t("workspace.engage")}<Help text={t("workspace.engageHelp")}/><select aria-label={t("workspace.engageAria")} value={combat.engage} onChange={e=>change({...combat,engage:e.target.value as CombatConfig['engage']})}><option value="visible">{t("workspace.engageVisible")}</option><option value="near">{t("workspace.engageNear")}</option></select></label>
      {combat.engage==="near"&&<label>{t("workspace.upTo")} <input aria-label={t("workspace.attackDistance")} type="number" min={1} max={SCREEN_RANGE} value={combat.radius} onChange={e=>change({...combat,radius:clampInt(e.target.value,1,SCREEN_RANGE,combat.radius)})}/> SQM</label>}</div>
    {hasLure&&<LureSettings value={combat} onChange={change}/>}
    <MonsterRules rules={combat.rules} movement={movement} disabled={disabled} onChange={rules=>change({...combat,rules})}/>
    <details className="v2-options"><summary>{t("workspace.sharedSpells")}<Help text={t("workspace.spellOrder")}/> <span>{combat.spells.length}</span></summary>
      {combat.spells.map((s,i)=><div className="combat-spell-card" key={i}>
        <div className="combat-spell-header"><b className="spell-priority-number">{i+1}</b><SpellPicker value={s} label={t("workspace.spell",{n:i+1})} disabled={disabled} onChange={next=>setSpell(i,next)}/>
          <SpellCooldown value={s} label={t("workspace.spellInterval",{n:i+1})} disabled={disabled} onChange={next=>setSpell(i,next)}/>
          <div className="spell-order"><button type="button" disabled={i===0} aria-label={t("workspace.spellUp",{n:i+1})} onClick={()=>change({...combat,spells:moveRule(combat.spells,i,-1)})}>↑</button><button type="button" disabled={i===combat.spells.length-1} aria-label={t("workspace.spellDown",{n:i+1})} onClick={()=>change({...combat,spells:moveRule(combat.spells,i,1)})}>↓</button></div>
          <button type="button" className="spell-remove" aria-label={t("workspace.spellRemove",{n:i+1})} onClick={()=>change({...combat,spells:combat.spells.filter((_,j)=>j!==i)})}>×</button>
        </div>
        <SpellConditions value={s} disabled={disabled} onChange={next=>setSpell(i,next)}/>
      </div>)}
      <button disabled={combat.spells.length>=12} onClick={()=>change({...combat,spells:[...combat.spells,defaultCombatSpell()]})}>＋ {t("workspace.addSpell")}</button></details>
    <details className="v2-options"><summary>{t("workspace.more")}</summary><div className="v2-select-row">
      <label>{t("workspace.tie")}<select value={combat.strategy} onChange={e=>change({...combat,strategy:e.target.value as CombatConfig['strategy']})}><option value="nearest">{t("workspace.nearest")}</option><option value="lowest_hp">{t("workspace.lowestHp")}</option><option value="highest_hp">{t("workspace.highestHp")}</option><option value="finish">{t("workspace.finish")}</option></select></label>
      {movement&&combat.mode==='chase'&&<label>{t("workspace.chaseDistance")}<input type="number" min={1} max={7} value={combat.distance} onChange={e=>change({...combat,distance:clampInt(e.target.value,1,7,combat.distance)})}/></label>}
      {hasLure&&<label>{t("workspace.lureLimit")}<Help text={t("workspace.lureLimitTitle")}/><input type="number" min={1} max={30} value={combat.lure_seconds} onChange={e=>change({...combat,lure_seconds:clampInt(e.target.value,1,30,combat.lure_seconds)})}/></label>}
      <div className="hold-target"><ActivationCheckbox label={t("workspace.holdTarget")} checked={!!combat.hold_target} disabled={disabled} onChange={hold_target=>change({...combat,hold_target})}/><Help text={t("workspace.holdTargetHelp")}/></div>
    </div></details>
  </>;
}
