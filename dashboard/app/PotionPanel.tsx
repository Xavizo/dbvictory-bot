"use client";

import { useEffect, useState } from "react";
import { toggleAction, updateActionConfig, setPotionDiagnostics, type ActionInfo, type PotionDiagnosticState } from "@/lib/api";

const items = [{id:10161,name:"Special Health Potion"},{id:10160,name:"Special Ki Potion"},{id:8465,name:"Senzu"}];
type Numeric = number | "";
type Rule = {id:string;enabled:boolean;resource:"hp"|"ki";item_id:number;threshold:Numeric;jitter:Numeric;cooldown:Numeric|null;interval:Numeric;mode:"use"|"self"};
type Config = {shared_cooldown:Numeric;rules:Rule[]};
function readConfig(config:ActionInfo["config"]):Config {
  const c=config ?? {};
  return {shared_cooldown:c.shared_cooldown ?? 0,rules:Array.isArray(c.rules) ? c.rules.map((r:Rule)=>({...r})) :
    (["health","ki"] as const).map(kind=>({id:kind,enabled:c[`${kind}_enabled`] ?? true,resource:kind==="health" ? "hp" : "ki",
      item_id:kind==="health" ? 10161 : 10160,threshold:c[`${kind}_threshold`] ?? (kind==="health" ? 60 : 40),
      jitter:c[`${kind}_jitter`] ?? 5,cooldown:c[`${kind}_cooldown`] ?? 0,interval:c[`${kind}_interval`] ?? 1,mode:c[`${kind}_mode`] ?? "use"}))};
}
export default function PotionPanel({action,diagnostics,paused,onRefresh,legacyActions=[]}:{
  action:ActionInfo;diagnostics?:PotionDiagnosticState;paused:boolean;onRefresh:()=>void;legacyActions?:ActionInfo[];
}) {
  const [rowLimit,setRowLimit]=useState(100);
  const [config,setConfig]=useState<Config>(()=>readConfig(action.config));
  const [error,setError]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
  const saved=JSON.stringify(action.config ?? {});
  useEffect(()=>{setConfig(readConfig(JSON.parse(saved)));},[saved]);
  const dirty=JSON.stringify(config)!==JSON.stringify(readConfig(action.config));
  const change=(id:string,patch:Partial<Rule>)=>{setConfig(c=>({...c,rules:c.rules.map(r=>r.id===id ? {...r,...patch} : r)}));setNotice("");};
  function add(item=10161) {
    setConfig(c=>({...c,rules:[...c.rules,{id:crypto.randomUUID(),enabled:false,resource:"hp",item_id:item,
      threshold:item===8465 ? 30 : 60,jitter:0,cooldown:null,interval:1,mode:"use"}]}));setNotice("");
  }
  function move(index:number,delta:number) {
    setConfig(c=>{const rules=[...c.rules];[rules[index],rules[index+delta]]=[rules[index+delta],rules[index]];return {...c,rules};});setNotice("");
  }
  async function perform(fn:()=>Promise<unknown>,message:string) {
    setBusy(true);setError("");setNotice("");
    try {await fn();setNotice(message);onRefresh();} catch(err) {setError(err instanceof Error ? err.message : String(err));}
    finally {setBusy(false);}
  }
  const unknown=config.rules.some(r=>r.enabled && Number(r.cooldown ?? config.shared_cooldown)<=0);
  const conflicts=legacyActions.filter(a=>a.enabled || a.running);
  return <section className="potion-panel healing-panel">
    <div className="potion-panel-header"><div><h2>Reguły leczenia</h2><p>{action.running ? "Działają według priorytetu" : action.enabled ? "Włączone · oczekiwanie na grę" : "Wyłączone"}{dirty && " · niezapisane zmiany"}</p></div>
      <button className={`potion-toggle ${action.enabled ? "is-enabled" : ""}`}
        disabled={busy || (!action.enabled && (paused || unknown || !!conflicts.length || !config.rules.some(r=>r.enabled)))}
        onClick={()=>perform(async()=>{if(!action.enabled) await updateActionConfig("auto_potions",config);await toggleAction("auto_potions",!action.enabled);},action.enabled ? "Reguły wyłączone." : "Reguły włączone.")}>
        {action.enabled ? "Wyłącz reguły" : "Włącz reguły"}</button>
    </div>
    <p className="rule-explanation">Od góry: pierwsza spełniona reguła z zakończonym cooldownem. Cooldown pomija regułę i pozwala sprawdzić następną. Bot nie sprawdza zapasu przedmiotów.</p>
    {!!conflicts.length && <p className="potion-notice">Wyłącz najpierw starsze akcje leczenia w sekcji poniżej: {conflicts.map(a=>a.name).join(", ")}. Dwa mechanizmy nie mogą jednocześnie używać Senzu.</p>}
    <fieldset disabled={busy} className="rule-list">
      {config.rules.map((r,i)=><article key={r.id} className={`healing-rule ${r.enabled ? "" : "rule-disabled"}`}>
        <div className="rule-main">
          <span className="rule-order" title="Priorytet">{i+1}</span>
          <input type="checkbox" aria-label={`Reguła ${i+1}: aktywna`} checked={r.enabled} onChange={e=>change(r.id,{enabled:e.target.checked})}/>
          <span className="rule-word">Jeśli</span>
          <select aria-label={`Reguła ${i+1}: zasób`} value={r.resource} onChange={e=>change(r.id,{resource:e.target.value as Rule["resource"]})}><option value="hp">HP</option><option value="ki">Ki</option></select>
          <span>&lt;</span><input className="rule-number" aria-label={`Reguła ${i+1}: próg procentowy`} type="number" min={1} max={100} step={.1} value={r.threshold} onChange={e=>change(r.id,{threshold:e.target.value==="" ? "" : Number(e.target.value)})}/><span>%</span>
          <label className="flex items-center gap-1" title="Losowanie progu procentowego HP/Ki; nie dotyczy czasu"><span>±</span><input className="rule-number" aria-label={`Reguła ${i+1}: zakres progu HP lub Ki w punktach procentowych`} type="number" min={0} max={30} step={.1} value={r.jitter} onChange={e=>change(r.id,{jitter:e.target.value==="" ? "" : Number(e.target.value)})}/><span>p.p.</span></label>
          <span className="rule-arrow">→</span><span className="rule-word">użyj</span>
          <select className="rule-item" aria-label={`Reguła ${i+1}: przedmiot`} value={r.item_id} onChange={e=>change(r.id,{item_id:Number(e.target.value)})}>{items.map(item=><option key={item.id} value={item.id}>{item.name} · {item.id}</option>)}</select>
          <div className="rule-row-tools">
            <button title="Wyższy priorytet" aria-label={`Reguła ${i+1}: przesuń w górę`} disabled={i===0} onClick={()=>move(i,-1)}>↑</button>
            <button title="Niższy priorytet" aria-label={`Reguła ${i+1}: przesuń w dół`} disabled={i===config.rules.length-1} onClick={()=>move(i,1)}>↓</button>
            <button title="Usuń regułę z edytora" aria-label={`Reguła ${i+1}: usuń`} onClick={()=>{setConfig(c=>({...c,rules:c.rules.filter(x=>x.id!==r.id)}));setNotice("");}}>×</button>
          </div>
        </div>
        <div className="rule-options">
          <span className="text-xs text-gray-400">Próg {r.resource==="hp" ? "HP" : "Ki"}: {Math.max(1,Number(r.threshold)-Number(r.jitter))}–{Math.min(100,Number(r.threshold)+Number(r.jitter))}%</span>
          {([ ["interval","Min. odstęp prób",.1,3600,"s"] ] as const).map(([key,label,min,max,unit])=><label key={key}>{label}
            <input className="rule-number" aria-label={`Reguła ${i+1}: ${label}`} type="number" min={min} max={max} step={.1} value={r[key]} onChange={e=>change(r.id,{[key]:e.target.value==="" ? "" : Number(e.target.value)})}/><span>{unit}</span></label>)}
          <label><input type="checkbox" aria-label={`Reguła ${i+1}: osobny cooldown`} checked={r.cooldown!==null} onChange={e=>change(r.id,{cooldown:e.target.checked ? Number(config.shared_cooldown) : null})}/> Osobny cooldown
            {r.cooldown!==null ? <><input className="rule-number" aria-label={`Reguła ${i+1}: cooldown`} type="number" min={.1} max={3600} step={.1} value={r.cooldown} onChange={e=>change(r.id,{cooldown:e.target.value==="" ? "" : Number(e.target.value)})}/> s</> : <span>Wspólny: {config.shared_cooldown || "—"} s</span>}</label>
          <select aria-label={`Reguła ${i+1}: sposób użycia`} value={r.mode} onChange={e=>change(r.id,{mode:e.target.value as Rule["mode"]})}><option value="use">Użyj przedmiotu</option><option value="self">Użyj na sobie</option></select>
        </div>
      </article>)}
      {!config.rules.length && <p className="rule-empty">Brak reguł. Dodaj warunek HP lub Ki i wybierz przedmiot.</p>}
    </fieldset>
    <div className="rule-footer"><div className="flex gap-2"><button className="secondary-button" disabled={busy || config.rules.length>=32} onClick={()=>add()}>+ Reguła</button><button className="secondary-button" disabled={busy || config.rules.length>=32} onClick={()=>add(8465)}>+ Senzu</button></div>
      <label>Wspólny cooldown <input aria-label="Wspólny cooldown" type="number" min={0} max={3600} step={.1} disabled={busy} value={config.shared_cooldown} onChange={e=>{setConfig(c=>({...c,shared_cooldown:e.target.value==="" ? "" : Number(e.target.value)}));setNotice("");}}/> s</label>
      <button disabled={busy || !dirty} className="primary-button" onClick={()=>perform(()=>updateActionConfig("auto_potions",config),"Zapisano kolejność i warunki reguł.")}>Zapisz reguły</button>
    </div>
    <p className="rule-explanation">Domyślnie reguły korzystają ze wspólnego cooldownu. Osobny cooldown może wydłużyć przerwę dla konkretnego przedmiotu. Zmiana przedmiotu zachowuje ustawienie; bieżący czas oczekiwania nie zeruje się. Próg losuje się po próbie w zakresie ±.</p>
    {unknown && <p className="potion-notice">Aktywna reguła ma nieustalony cooldown. Ustaw go przed zapisaniem działających reguł lub włączeniem automatyki. Nowe reguły są domyślnie wyłączone.</p>}
    {error && <p role="alert" className="text-sm text-red-300">{error}</p>}{notice && <p role="status" className="text-xs text-emerald-300">{notice}</p>}
    <details className="border-t border-gray-700 pt-3" open={diagnostics?.active || undefined}>
      <summary>Pomiar użycia · mikstury i Senzu {diagnostics?.active ? `· ${diagnostics.remaining_seconds ?? 0} s` : ""}</summary>
      <p className="my-2 text-xs text-gray-400">Pasywna obserwacja na 5 minut. Działa obok reguł i odróżnia próby ręczne od automatycznych. Nie wysyła poleceń ani nie wstrzymuje leczenia.</p>
      <div className="flex gap-3 items-center flex-wrap"><button className="secondary-button" disabled={busy} onClick={()=>perform(()=>setPotionDiagnostics(!diagnostics?.active),diagnostics?.active ? "Pomiar zatrzymany." : "Pomiar aktywny przez 5 minut; reguły działają dalej.")}>{diagnostics?.active ? "Zatrzymaj pomiar" : "Rozpocznij pomiar"}</button>
        <label className="text-xs">Wiersze <select aria-label="Liczba wierszy pomiaru" value={rowLimit} onChange={e=>setRowLimit(Number(e.target.value))}>{[50,100,250,500].map(n=><option key={n} value={n}>{n}</option>)}</select></label>
        <span className="text-xs text-gray-400">{diagnostics?.rows.length ?? 0} / {diagnostics?.capacity ?? 500} prób</span></div>
      <div className="overflow-auto mt-3 max-h-96"><table className="w-full text-xs text-left"><thead className="sticky top-0 bg-[#25232d]"><tr><th>Czas</th><th>Przedmiot / źródło</th><th>Wynik</th><th>Odstęp</th><th>Liczba</th><th>Wiek HP/Ki</th><th>Δ HP / Ki</th></tr></thead>
        <tbody>{diagnostics?.rows.slice(-rowLimit).reverse().map((row,i)=><tr key={row.id ?? i} className="border-t border-gray-700 align-top"><td className="py-2 whitespace-nowrap">{row.time}</td><td className="py-2">{items.find(x=>x.id===row.item_id)?.name ?? row.item_id}<small className="block text-gray-400">{row.source==="auto" ? "Reguła" : "Ręcznie"} · {row.mode}</small></td>
          <td className="py-2 max-w-72"><span className={row.status==="rejected" ? "text-red-300" : row.status==="consumption_observed" ? "text-emerald-300" : "text-amber-200"}>{({pending:"Czekam na odpowiedź",unconfirmed:"Brak potwierdzenia",ambiguous:"Niejednoznaczne",server_reported:"Serwer zgłosił próbę",consumption_observed:"Potwierdzony ubytek",rejected:"Odrzucono"} as Record<string,string>)[row.status ?? "unconfirmed"]}</span>{row.response && <small className="block text-gray-400">{row.response}</small>}</td>
          <td className="py-2">{row.gap_seconds ?? "—"} s</td><td className="py-2">{row.reported_count ?? "—"}{row.stock_delta!=null && <small className="block">Δ {row.stock_delta}</small>}</td><td className="py-2">{row.stats_age_ms==null ? "—" : `${row.stats_age_ms} ms`}</td><td className="py-2">{row.hp_delta} / {row.ki_delta}</td></tr>)}</tbody></table></div>
      <p className="mt-2 text-xs text-gray-500">„Using…” może poprzedzać odmowę. Ubytek potwierdza dopiero mniejsza liczba w następnej odpowiedzi dla tego samego przedmiotu; ostatnia próba może czekać na kolejny raport. Przesuwanie lub handel przedmiotami przerywa porównanie. Przy nakładających się próbach wynik pozostaje niejednoznaczny. Δ HP/Ki nie potwierdza zużycia ani cooldownu.</p>
    </details>
  </section>;
}
