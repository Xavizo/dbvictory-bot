"use client";
import {useState} from "react";
import {toggleAction, type BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
export const actionTitles:Record<string,string> = {
  auto_hunter_knife:"Hunter Knife",auto_barrier_band:"Barrier Band",barrier_spell:"Barrier",auto_potions:"Leczenie · HP / Ki",auto_combat:"Automatyczna walka",
  auto_targeting:"Wybór celu",adaptive_cavebot:"Cavebot",eat_food:"Jedzenie",full_light:"Pełne oświetlenie",
  packet_sniffer:"Diagnostyka pakietów",speed_up:"Przyspieszenie",
};
export default function AutomationStrip({state,connected,onRefresh,onSettings,onPause,busy}:{state:BotState|null;connected:boolean;onRefresh:()=>void;onSettings:()=>void;onPause:()=>void;busy:boolean}) {
  const [retained,setRetained]=useState<string[]>([]),[pending,setPending]=useState<string|null>(null),[error,setError]=useState("");
  const paused=!!state?.automation_paused,manual=state?.automation_pause_kind==="manual";
  const actions=(state?.actions ?? []).filter(a=>a.name!=="dll_bridge" && (a.enabled || a.running || retained.includes(a.name) || (a.name==="adaptive_cavebot" && state?.adaptive_cavebot?.status==="paused")));
  async function toggle(name:string,enabled:boolean) {
    setPending(name);setError("");setRetained(old=>old.includes(name)?old:[...old,name]);
    try {if(name==="adaptive_cavebot") {
      const response=await authenticatedFetch("http://127.0.0.1:8089/api/adaptive/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command:enabled?"resume":"pause"})});
      if(!response.ok)throw new Error((await response.json()).error);
    } else await toggleAction(name,enabled);await onRefresh();}
    catch(e){setError(e instanceof Error?e.message:String(e));}finally{setPending(null);}
  }
  return <section className="automation-strip" aria-label="Szybkie sterowanie automatyką">
    <div className="automation-strip-heading"><strong>Automatyka</strong><button onClick={onSettings}>Ustawienia ↗</button>
      <button className="pause-all-button" disabled={!connected || busy || (!!paused && !manual)} onClick={onPause}>{manual && paused ? "▶ Wznów wybrane" : "Ⅱ Pauza wszystkich"}</button></div>
    <div className="automation-chips">{actions.map(a=><article key={a.name} className={`automation-chip ${a.running && !paused?"running":""}`}>
      <span><strong>{actionTitles[a.name] ?? a.name}</strong><small>{a.name==="adaptive_cavebot" && state?.adaptive_cavebot?.status==="paused"?"Pauza · sprawdź powód w Cavebocie":!connected?"Brak łączności z panelem":!a.enabled && !a.running?"Wyłączona":paused?"Wstrzymana":a.running?"Pracuje":a.completed?"Zakończona":state?.connected?"Gotowa / oczekuje":"Czeka na grę"}</small></span>
      <button className={`action-switch ${a.enabled || a.running?"on":""}`} role="switch" aria-checked={a.enabled || a.running} aria-label={`${actionTitles[a.name] ?? a.name}: włącz lub wyłącz`}
        disabled={!connected || busy || pending!==null || (paused && !a.enabled && !a.running) || (a.name==="adaptive_cavebot" && !a.running && state?.adaptive_cavebot?.status!=="paused")} onClick={()=>toggle(a.name,!(a.enabled || a.running))}><span/></button>
    </article>)}{!actions.length && <p>Brak włączonych akcji.</p>}</div>
    {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
  </section>;
}
