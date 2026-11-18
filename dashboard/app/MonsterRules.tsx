"use client";
import {useState} from "react";
import {defaultMonsterRule,defaultCombatSpell,monsterRuleSummary,moveRule,type MonsterRule} from "@/lib/cavebotModel";
import SpellPicker,{SpellCooldown} from './SpellPicker';
import SpellConditions from './SpellConditions';

export default function MonsterRules({rules,onChange,movement,disabled=false}:{rules:MonsterRule[];onChange:(rules:MonsterRule[])=>void;movement:boolean;disabled?:boolean}) {
  const [editing,setEditing]=useState<number|null>(null);
  function update(index:number,patch:Partial<MonsterRule>){onChange(rules.map((rule,i)=>i===index?{...rule,...patch}:rule));}
  function reorder(index:number,delta:number){onChange(moveRule(rules,index,delta));setEditing(index+delta);}
  return <details className="v2-options monster-rules">
    <summary>Potwory <span>{rules.length}</span></summary>
    <div className="monster-rules-actions"><button disabled={disabled||rules.length>=32} onClick={()=>onChange([...rules,defaultMonsterRule()])}>＋ Dodaj potwora</button></div>
    {!rules.length&&<p className="v2-inline-note">Dodaj nazwę potwora lub wzorzec, np. Wolf*.</p>}
    {!!rules.length&&<div className="v2-rule-caption"><span>Potwór · pierwsza pasująca reguła</span><span title="0–100, większy = ważniejszy">Priorytet ↑</span></div>}
    {rules.map((rule,i)=>{
      const stance=rule.stance??'inherit',spellMode=rule.spell_mode??'inherit',spell=rule.spell??defaultCombatSpell();
      return <div className={`monster-rule ${editing===i?'is-editing':''}`} key={i}>
        <div className="v2-rule"><b>{i+1}</b>
          <input aria-label={`Potwór ${i+1}`} placeholder="np. Wolf*" value={rule.pattern} onChange={e=>update(i,{pattern:e.target.value})}/>
          <select aria-label={`Działanie reguły ${i+1}`} value={rule.action} onChange={e=>update(i,{action:e.target.value as MonsterRule['action']})}><option value="attack">Zaznacz i atakuj</option><option value="ignore">Ignoruj</option></select>
          <input className="v2-priority" aria-label={`Priorytet potwora ${i+1}`} title="Priorytet 0–100: większy = ważniejszy" type="number" min={0} max={100} value={rule.priority} onChange={e=>update(i,{priority:Number(e.target.value)})}/>
          <button title={`Strategia: ${monsterRuleSummary(rule)}`} aria-label={`Strategia potwora ${i+1}`} aria-expanded={editing===i} onClick={()=>setEditing(editing===i?null:i)}>⚙</button>
          <button aria-label={`Usuń regułę ${i+1}`} onClick={()=>{onChange(rules.filter((_,j)=>j!==i));setEditing(null);}}>×</button>
        </div>
        {rule.action==='attack'&&monsterRuleSummary(rule)!=='Zaznacz i atakuj'&&<small className="monster-rule-summary">{monsterRuleSummary(rule)}</small>}
        {editing===i&&<div className="monster-rule-options">
          {rule.action==='attack'&&<><div className="monster-rule-fields">
            <label>Ruch<select aria-label={`Strategia ruchu ${i+1}`} value={stance} onChange={e=>{const next=e.target.value as MonsterRule['stance'];update(i,{stance:next,distance:next==='distance'?Math.max(2,rule.distance??3):rule.distance??1});}}>
              <option value="inherit">Z ustawień trasy</option><option value="stop">Stój i walcz</option><option value="chase">Podejdź / goń</option><option value="distance">Tylko z dystansu</option><option value="lure">Luruj po trasie</option>
            </select></label>
            {(stance==='distance'||stance==='chase')&&<label>Odstęp SQM<input aria-label={`Dystans potwora ${i+1}`} type="number" min={stance==='distance'?2:1} max={7} value={rule.distance??3} onChange={e=>update(i,{distance:Number(e.target.value)})}/></label>}
            <label>Czary<select aria-label={`Wybór czarów ${i+1}`} value={spellMode} onChange={e=>update(i,{spell_mode:e.target.value as MonsterRule['spell_mode'],spell:e.target.value==='custom'?spell:null})}>
              <option value="inherit">Wspólne</option><option value="none">Bez czarów</option><option value="custom">Wybrany czar</option>
            </select></label>
          </div>
          {spellMode==='custom'&&<div className="monster-rule-spell"><div className="spell-picker-field"><span>Czar</span><SpellPicker label={`Czar potwora ${i+1}`} value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/></div>
            <SpellCooldown label={`Odstęp czaru potwora ${i+1}`} value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/>
            <SpellConditions value={spell} disabled={disabled} onChange={next=>update(i,{spell:next})}/>
          </div>}
          {stance==='distance'&&<small>{movement?'Utrzymuje odstęp na dostępnych polach; poniżej tego dystansu wstrzymuje atak.':'Ruch ręczny — poniżej tego dystansu bot wstrzyma atak i czary.'}</small>}
          {!movement&&(stance==='lure'||stance==='chase')&&<small>Ruch wyłączony — bot zaznacza i atakuje, a Ty sterujesz postacią.</small>}
          </>}
          <div className="monster-rule-order"><button aria-label={`Reguła ${i+1} w górę`} disabled={!i} onClick={()=>reorder(i,-1)}>↑ Wyżej</button><button aria-label={`Reguła ${i+1} w dół`} disabled={i===rules.length-1} onClick={()=>reorder(i,1)}>↓ Niżej</button><button onClick={()=>setEditing(null)}>Gotowe</button></div>
        </div>}
      </div>;
    })}
  </details>;
}
