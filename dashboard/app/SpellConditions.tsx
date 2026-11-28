"use client";
import { t } from "@/lib/i18n";
import {clampInt,SCREEN_RANGE,type CombatSpell,type ConditionOp,type SpellCheck} from '@/lib/cavebotModel';
import Help from "@/app/Help";

const ops:ConditionOp[]=['>=','>','=','<=','<'];
const symbols:Record<ConditionOp,string>={'>=':'≥','>':'>','=':'=','<=':'≤','<':'<'};
function Compare({label,value,op,onValue,onOp,max,min=0,disabled=false,unit}:{label:string;value:number;op:ConditionOp;onValue:(n:number)=>void;onOp:(op:ConditionOp)=>void;max:number;min?:number;disabled?:boolean;unit?:React.ReactNode}) {
  return <label className="spell-condition"><span>{label}</span><div>
    <select aria-label={t("spellCond.operator",{label})} disabled={disabled} value={op} onChange={e=>onOp(e.target.value as ConditionOp)}>{ops.map(o=><option key={o} value={o}>{symbols[o]}</option>)}</select>
    <input aria-label={t("spellCond.threshold",{label})} type="number" min={min} max={max} step={1} disabled={disabled} value={value} onChange={e=>onValue(clampInt(e.target.value,min,max,value))}/>{unit}
  </div></label>;
}
export default function SpellConditions({value,onChange,disabled=false}:{value:CombatSpell;onChange:(next:CombatSpell)=>void;disabled?:boolean}) {
  const patch=(p:Partial<CombatSpell>)=>onChange({...value,...p});
  return <div className="spell-conditions">
    <div className="spell-area-row">
      <label>{t("spellCond.area")}<Help text={t("spellCond.areaTitle")}/><select aria-label={t("spellCond.areaAria")} disabled={disabled} value={value.aim??'target'} onChange={e=>patch({aim:e.target.value as 'target'|'self'})}><option value="self">{t("spellCond.self")}</option><option value="target">{t("spellCond.target")}</option></select></label>
      <label>{t("spellCond.visibility")}<select aria-label={t("spellCond.visibilityAria")} disabled={disabled} value={value.trigger} onChange={e=>patch({trigger:e.target.value as CombatSpell['trigger']})}><option value="distance">{t("spellCond.byDistance")}</option><option value="visible">{t("spellCond.wholeScreen")}</option></select></label>
    </div>
    <div className="spell-condition-grid">
      <Compare label={t("spellCond.distance")} value={value.range} op={value.range_op??'<='} onValue={range=>patch({range})} onOp={range_op=>patch({range_op})} min={1} max={SCREEN_RANGE} disabled={disabled||value.trigger==='visible'}/>
      <Compare label={t("spellCond.mobs")} value={value.count} op={value.count_op??'>='} onValue={count=>patch({count})} onOp={count_op=>patch({count_op})} max={12} disabled={disabled}/>
      {(['ki','hp'] as const).filter(field=>field==='ki'||value.hp!=null).map(field=>{
        const unit=value[`${field}_unit`]??'percent',max=unit==='percent'?(field==='hp'?100:1000):2147483647;
        return <div className="spell-resource" key={field}><Compare label={field==='ki'?'Ki':'HP'} value={value[field]??0} op={value[`${field}_op`]??(field==='ki'?'>=':'<=')} onValue={n=>patch({[field]:n})} onOp={op=>patch({[`${field}_op`]:op})} max={max} disabled={disabled}
          unit={<select aria-label={t("spellCond.unit",{field:field.toUpperCase()})} disabled={disabled} value={unit} onChange={e=>patch({[`${field}_unit`]:e.target.value})}><option value="percent">%</option><option value="value">{t("spellCond.points")}</option></select>}/>
          {field==='hp'&&<button type="button" className="condition-remove" disabled={disabled} aria-label={t("spellCond.removeHp")} onClick={()=>patch({hp:null})}>×</button>}</div>;
      })}
      {value.hp==null&&<button type="button" className="spell-add-condition" disabled={disabled} onClick={()=>patch({hp:50,hp_op:'<=',hp_unit:'percent'})}>＋ {t("spellCond.addHp")}</button>}
    </div>
  </div>;
}

export function SpellDiagnostics({rows,passive=false}:{rows:SpellCheck[];passive?:boolean}) {
  if(!rows.length)return null;
  return <div className="spell-diagnostics" aria-label={t("spellCond.diagnostics")}>
    {passive&&<small>{t("spellCond.passive")}</small>}
    {rows.map((row,index)=><div key={index} className={row.selected?'is-ready':''}><b>{index+1}</b><span><strong>{row.words}</strong><small>{row.reason}</small>{row.wave_note&&<small>{row.wave_note}</small>}</span><em title={t("spellCond.matchedTitle")}>{t("spellCond.matched",{n:row.matched_count})}</em></div>)}
  </div>;
}
