"use client";
import {useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import type {BotState} from "@/lib/api";

export default function CavebotRunBar({state,connected,onRefresh}:{state:BotState|null;connected:boolean;onRefresh:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const bot=state?.adaptive_cavebot;
  const running=bot?.status==="running",paused=bot?.status==="paused";
  if(!bot || !bot.total || (!running && !paused && bot.status!=="ready" && !bot.resumable))return null;
  async function control(command:"pause"|"stop"|"resume"|"discard_progress") {
    setBusy(true);setError("");
    try {
      const response=await authenticatedFetch("http://127.0.0.1:8089/api/adaptive/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command})});
      if(!response.ok){const data=await response.json();throw new Error(data.error ?? `HTTP ${response.status}`);}
      onRefresh();
    } catch(e){setError(e instanceof Error?e.message:String(e));} finally{setBusy(false);}
  }
  return <aside className={`cavebot-run-bar ${running?"is-running":"is-paused"}`} aria-label="Sterowanie aktywnym Cavebotem V2">
    <div><strong><i className={`status-dot ${running?"online":""}`}/> {bot.mode==="combat"?"SAMA WALKA":"CAVEBOT V2"} · {running?"DZIAŁA":paused?"PAUZA":bot.status==="idle"?"STOP · POSTĘP ZACHOWANY":"GOTOWY"}</strong>
      <span title={bot.name}>{bot.mode==="combat"?"Automatyczny wybór celu i czary · ruch ręczny":`${bot.name} · waypoint ${bot.waypoint?.current!=null?bot.waypoint.current+1:"–"}/${bot.waypoint?.total??"–"} · okrążenia ${bot.loops??0}`}</span>
      {error?<small role="alert">{error}</small>:<small title={bot.reason}>{bot.reason}</small>}</div>
    <div className="cavebot-run-buttons">
      {running&&bot.navigation_hold&&<button disabled={busy||!connected} onClick={()=>control("resume")}>Sprawdź przejście ponownie</button>}
      {running?<button disabled={busy || !connected} onClick={()=>control("pause")}>Ⅱ Pauza V2</button>:
        <button disabled={busy || !connected || (!paused&&!bot.resumable) || state?.automation_paused} onClick={()=>control("resume")}>▶ Wznów V2</button>}
      {running?<button className="cavebot-stop" disabled={busy || !connected} onClick={()=>control("stop")}>■ Zatrzymaj V2</button>:
        <button disabled={busy || !connected || bot.preparing} title="Odrzuć zapamiętany postęp. Zachowaj zapisany szkic i ustawienia walki." onClick={()=>control("discard_progress")}>Odrzuć postęp</button>}
    </div>
  </aside>;
}
