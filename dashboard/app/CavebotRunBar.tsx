"use client";
import { t, type TextKey } from "@/lib/i18n";
import {useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import type {BotState} from "@/lib/api";
import LureStatus from "./LureStatus";
const phases=new Set(["fight","chase","distance","lure","return","route"]);
const domains=new Set(["running","combat_movement","waiting","waiting_for_combat","held","fault","complete","disabled","stopped","idle"]);

export default function CavebotRunBar({state,connected,onRefresh}:{state:BotState|null;connected:boolean;onRefresh:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const bot=state?.adaptive_cavebot;
  const running=bot?.status==="running",paused=bot?.status==="paused";
  const target=(running||paused)?bot?.combat?.target:null;
  const targetStatus=target ? [target.name,`${target.health}% HP`,phases.has(bot?.combat?.phase??"")?t(`run.phase.${bot!.combat!.phase}` as TextKey):undefined].filter(Boolean).join(" · ") : "";
  const domainLabel=(status:string)=>domains.has(status)?t(`run.domain.${status}` as TextKey):status;
  if(!bot || (!bot.total && bot.mode!=="combat") || (!running && !paused && bot.status!=="ready" && !bot.resumable))return null;
  async function control(command:"pause"|"stop"|"resume"|"discard_progress") {
    setBusy(true);setError("");
    try {
      const response=await authenticatedFetch("http://127.0.0.1:8089/api/adaptive/control",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({command})});
      if(!response.ok){const data=await response.json();throw new Error(data.error ?? `HTTP ${response.status}`);}
      onRefresh();
    } catch(e){setError(e instanceof Error?e.message:String(e));} finally{setBusy(false);}
  }
  return <aside className={`cavebot-run-bar ${running?"is-running":"is-paused"}`} aria-label={t("run.aria")}>
    <div><div className="cavebot-status-head"><strong><i className={`status-dot ${running?"online":""}`}/> {t(bot.mode==="combat"?"run.combatOnly":"run.cavebot")} · {t(running?"run.running":paused?"run.paused":bot.status==="idle"?"run.stopped":"run.ready")}</strong>
      {target&&<span className="combat-target" title={targetStatus}>{t("run.target")} · {targetStatus}</span>}
      {(running||paused)&&bot.combat?.lure&&<LureStatus progress={bot.combat.lure} paused={paused}/>}
    </div>
      <span title={bot.name}>{bot.mode==="combat"?t("run.combatNote"):t("run.routeNote",{name:bot.name,current:bot.waypoint?.current!=null?bot.waypoint.current+1:"–",total:bot.waypoint?.total??"–",loops:bot.loops??0})}</span>
      {bot.domains&&<small title={[bot.domains.movement.reason,bot.domains.combat.reason].filter(Boolean).join(" · ")}>
        {t("run.movement")}: {bot.mode==="combat"?t("run.manual"):domainLabel(bot.domains.movement.status)} · {t("run.combat")}: {domainLabel(bot.domains.combat.status)} · {t("run.potions")}: {t(state?.actions.some(a=>a.name==="auto_potions"&&a.running)&&!state?.automation_paused?"run.domain.running":"run.potionsOff")}
      </small>}
      {error?<small role="alert">{error}</small>:<small title={bot.reason}>{bot.reason}</small>}</div>
    <div className="cavebot-run-buttons">
      {running&&bot.navigation_hold&&<button disabled={busy||!connected} onClick={()=>control("resume")}>{t("run.recheckTransition")}</button>}
      {running?<button disabled={busy || !connected} onClick={()=>control("pause")}>Ⅱ {t("common.pause")}</button>:
        <button disabled={busy || !connected || (!paused&&!bot.resumable) || state?.automation_paused} onClick={()=>control("resume")}>▶ {t("common.resume")}</button>}
      {running?<button className="cavebot-stop" disabled={busy || !connected} onClick={()=>control("stop")}>■ {t("common.stop")}</button>:
        <button disabled={busy || !connected || bot.preparing} title={t("run.discardTitle")} onClick={()=>control("discard_progress")}>{t("run.discard")}</button>}
    </div>
  </aside>;
}
