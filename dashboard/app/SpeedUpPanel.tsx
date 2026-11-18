"use client";
import {useEffect,useState} from "react";
import {toggleAction,updateActionConfig,type BotState} from "@/lib/api";

export default function SpeedUpPanel({state,onRefresh}:{state:BotState|null;onRefresh:()=>void}) {
  const action=state?.actions.find(a=>a.name==="speed_up"),cfg=action?.config;
  const saved={duration:Number(cfg?.duration??33.3),before:Number(cfg?.before??0),after:Number(cfg?.after??5)};
  const serial=JSON.stringify(saved);
  const [draft,setDraft]=useState(saved),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>setDraft(JSON.parse(serial)),[serial]);
  if(!action)return null;
  const dirty=JSON.stringify(draft)!==serial;
  async function change(save:boolean){setBusy(true);setError("");try{
    if(save)await updateActionConfig("speed_up",draft);else await toggleAction("speed_up",!action?.enabled);
    await onRefresh();
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="speed-up-panel" aria-label="Automatyczny Speed Up">
    <div className="speed-heading"><img src="/local-spells/icons/11713.png" width="28" height="28" alt=""/><strong>Speed Up</strong><span>{action.enabled?(state?.speed_up?.status??"Oczekiwanie"):"Wyłączony"}</span><button className={`action-switch ${action.enabled?"on":""}`} role="switch" aria-checked={action.enabled} aria-label="Automatyczny Speed Up" disabled={busy||dirty||!!state?.automation_paused} onClick={()=>void change(false)}><span/></button></div>
    <div className="speed-times">{([{key:"duration",label:"Czas efektu (s)"},{key:"before",label:"Maks. przed końcem (s)"},{key:"after",label:"Maks. po końcu (s)"}] as const).map(f=><label key={f.key}>{f.label}<input aria-label={f.label} type="number" min={f.key==="duration"?2:0} max="3600" step="0.1" value={draft[f.key]} disabled={busy||action.enabled} onChange={e=>setDraft({...draft,[f.key]:Number(e.target.value)})}/></label>)}{dirty&&<button className="secondary-button" disabled={busy||action.enabled} onClick={()=>void change(true)}>Zapisz</button>}</div>
    <small>Losowanie raz na cykl: od −{draft.before} s do +{draft.after} s względem końca efektu. Czas startowy 33,3 s pochodzi z jednego pomiaru.</small>
    {error&&<p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
