"use client";
import { t, type TextKey } from "@/lib/i18n";
import {useState} from "react";
import {toggleAction, type BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
const titled=new Set(["auto_hunter_knife","auto_barrier_band","barrier_spell","auto_potions","auto_combat","auto_targeting","adaptive_cavebot","eat_food","packet_sniffer","speed_up"]);
/** The player-facing name of a built-in action; undefined for the player's own actions. */
export const actionTitle=(name:string)=>titled.has(name)?t(`action.title.${name}` as TextKey):undefined;
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
  return <section className="automation-strip" aria-label={t("automation.quick")}>
    <div className="automation-strip-heading"><strong>{t("automation.title")}</strong><button onClick={onSettings}>{t("automation.settings")} ↗</button>
      <button className="pause-all-button" disabled={!connected || busy || (!!paused && !manual)} onClick={onPause}>{manual && paused ? "▶ "+t("automation.resumeSelected") : "Ⅱ "+t("automation.pauseAll")}</button></div>
    <div className="automation-chips">{actions.map(a=><article key={a.name} className={`automation-chip ${a.running && !paused?"running":""}`}>
      <span><strong>{actionTitle(a.name) ?? a.name}</strong><small>{a.name==="adaptive_cavebot" && state?.adaptive_cavebot?.status==="paused"?t("automation.cavebotPaused"):!connected?t("automation.noLink"):!a.enabled && !a.running?t("action.off"):paused?t("automation.paused"):a.running?t("action.working"):a.completed?t("action.completed"):state?.connected?t("automation.ready"):t("action.waitingForGame")}</small></span>
      <button className={`action-switch ${a.enabled || a.running?"on":""}`} role="switch" aria-checked={a.enabled || a.running} aria-label={t("action.toggle",{title:actionTitle(a.name) ?? a.name})}
        disabled={!connected || busy || pending!==null || (paused && !a.enabled && !a.running) || (a.name==="adaptive_cavebot" && !a.running && state?.adaptive_cavebot?.status!=="paused")} onClick={()=>toggle(a.name,!(a.enabled || a.running))}><span/></button>
    </article>)}{!actions.length && <p>{t("automation.none")}</p>}</div>
    {error && <p role="alert" className="text-xs text-red-300">{error}</p>}
  </section>;
}
