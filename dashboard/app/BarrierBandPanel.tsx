"use client";
import {useState} from "react";
import {toggleAction,updateActionConfig,type BotState} from "@/lib/api";

export default function BarrierBandPanel({state,onRefresh}:{state:BotState|null;onRefresh:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const action=state?.actions.find(a=>a.name==="auto_barrier_band");
  if(!action)return null;
  const enabled=action.enabled,slot=String(action.config?.slot??"band");
  async function change(next?:string){setBusy(true);setError("");try {
    if(next)await updateActionConfig("auto_barrier_band",{slot:next});
    else await toggleAction("auto_barrier_band",!enabled);
    await onRefresh();
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="barrier-band-panel" aria-label="Automatyczne wyposażenie Barrier Band">
    <div><strong>Barrier Band</strong><small>3051 → 3088 · uzupełnij po zniknięciu</small></div>
    <label>Slot <select aria-label="Docelowy slot Barrier Band" value={slot} disabled={busy||enabled} onChange={e=>change(e.target.value)}>
      <option value="band">Bandów</option><option value="right_hand">Prawa ręka</option><option value="left_hand">Lewa ręka</option>
    </select></label>
    <span className="barrier-band-status">{enabled?(state?.barrier_band?.status??"Oczekiwanie"):"Wyłączony"}</span>
    <button className={`action-switch ${enabled?"on":""}`} role="switch" aria-checked={enabled} aria-label="Automatyczne zakładanie Barrier Band" disabled={busy||!!state?.automation_paused} onClick={()=>change()}><span/></button>
    <small className="barrier-band-help">Zapas w otwartym plecaku. Zajęty slot pozostaje bez zmian.</small>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
