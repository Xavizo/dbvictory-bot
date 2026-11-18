"use client";
import type {CombatSpell,ConditionOp,SpellCheck} from '@/lib/cavebotModel';

const ops:ConditionOp[]=['>=','>','=','<=','<'];
const symbols:Record<ConditionOp,string>={'>=':'≥','>':'>','=':'=','<=':'≤','<':'<'};
function Compare({label,value,op,onValue,onOp,max,min=0,disabled=false,unit}:{label:string;value:number;op:ConditionOp;onValue:(n:number)=>void;onOp:(op:ConditionOp)=>void;max:number;min?:number;disabled?:boolean;unit?:React.ReactNode}) {
  return <label className="spell-condition"><span>{label}</span><div>
    <select aria-label={`Operator ${label}`} disabled={disabled} value={op} onChange={e=>onOp(e.target.value as ConditionOp)}>{ops.map(o=><option key={o} value={o}>{symbols[o]}</option>)}</select>
    <input aria-label={`Próg ${label}`} type="number" min={min} max={max} step={1} disabled={disabled} value={value} onChange={e=>onValue(Number(e.target.value))}/>{unit}
  </div></label>;
}
export default function SpellConditions({value,onChange,disabled=false}:{value:CombatSpell;onChange:(next:CombatSpell)=>void;disabled?:boolean}) {
  const patch=(p:Partial<CombatSpell>)=>onChange({...value,...p});
  return <div className="spell-conditions">
    <div className="spell-area-row">
      <label title="Wokół mnie: liczba mobów względem postaci. Wybrany cel: dodatkowo cel ataku musi spełnić warunek odległości.">Obszar<select aria-label="Obszar warunku czaru" disabled={disabled} value={value.aim??'target'} onChange={e=>patch({aim:e.target.value as 'target'|'self'})}><option value="self">Wokół mnie</option><option value="target">Wybrany cel</option></select></label>
      <label>Widoczność<select aria-label="Warunek widoczności czaru" disabled={disabled} value={value.trigger} onChange={e=>patch({trigger:e.target.value as CombatSpell['trigger']})}><option value="distance">Według odległości</option><option value="visible">Cały ekran</option></select></label>
    </div>
    <div className="spell-condition-grid">
      <Compare label="Odległość SQM" value={value.range} op={value.range_op??'<='} onValue={range=>patch({range})} onOp={range_op=>patch({range_op})} min={1} max={7} disabled={disabled||value.trigger==='visible'}/>
      <Compare label="Moby w obszarze" value={value.count} op={value.count_op??'>='} onValue={count=>patch({count})} onOp={count_op=>patch({count_op})} max={12} disabled={disabled}/>
      {(['ki','hp'] as const).filter(field=>field==='ki'||value.hp!=null).map(field=>{
        const unit=value[`${field}_unit`]??'percent',max=unit==='percent'?(field==='hp'?100:1000):2147483647;
        return <div className="spell-resource" key={field}><Compare label={field==='ki'?'Ki':'HP'} value={value[field]??0} op={value[`${field}_op`]??(field==='ki'?'>=':'<=')} onValue={n=>patch({[field]:n})} onOp={op=>patch({[`${field}_op`]:op})} max={max} disabled={disabled}
          unit={<select aria-label={`Jednostka ${field.toUpperCase()}`} disabled={disabled} value={unit} onChange={e=>patch({[`${field}_unit`]:e.target.value})}><option value="percent">%</option><option value="value">pkt</option></select>}/>
          {field==='hp'&&<button type="button" className="condition-remove" disabled={disabled} aria-label="Usuń warunek HP" onClick={()=>patch({hp:null})}>×</button>}</div>;
      })}
      {value.hp==null&&<button type="button" className="spell-add-condition" disabled={disabled} onClick={()=>patch({hp:50,hp_op:'<=',hp_unit:'percent'})}>＋ Warunek HP</button>}
    </div>
  </div>;
}

export function SpellDiagnostics({rows,passive=false}:{rows:SpellCheck[];passive?:boolean}) {
  if(!rows.length)return null;
  return <div className="spell-diagnostics" aria-label="Ocena warunków czarów">
    {passive&&<small>Podgląd warunków · bez rzucania czarów i bez odliczania odstępów</small>}
    {rows.map((row,index)=><div key={index} className={row.selected?'is-ready':''}><b>{index+1}</b><span><strong>{row.words}</strong><small>{row.reason}</small>{row.wave_note&&<small>{row.wave_note}</small>}</span><em title="Liczba pasujących mobów w zadanym obszarze">{row.matched_count} mob.</em></div>)}
  </div>;
}
