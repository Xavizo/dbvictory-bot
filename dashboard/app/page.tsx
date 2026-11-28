'use client';

import { t, num, plural, type TextKey } from "@/lib/i18n";
import { useBot } from "../lib/useBot";
import RouteReplayPanel from "./RouteReplayPanel";
import MovementHistoryPanel from "./MovementHistoryPanel";
import CavebotReports from "./CavebotReports";
import SpeedUpPanel from "./SpeedUpPanel";
import HunterKnifePanel from "./HunterKnifePanel";
import ProfilePicker from "./ProfilePicker";
import { useState } from "react";
import { deleteAction, emergencyStop, pauseAutomation, resumeAutomation, toggleAction, unlockAutomation, type ActionInfo } from "@/lib/api";
import { ConnectionSequence, CreatureList, PlayerStats } from "./SessionDiagnostics";
import CavebotWorkspace from "./CavebotWorkspace";
import AutomationStrip, {actionTitle} from "./AutomationStrip";
import PotionPanel from "./PotionPanel";
import TransitionLearningPanel from "./TransitionLearningPanel";
import DashboardChrome, { type WorkspaceView } from "./DashboardChrome";
import BarrierBandPanel from "./BarrierBandPanel";
import BarrierSpellPanel from "./BarrierSpellPanel";
import ItemIdentificationPanel from "./ItemIdentificationPanel";
import StandaloneCombatPanel from "./StandaloneCombatPanel";

function ActionCard({action,onRefresh}:{action:ActionInfo;onRefresh:()=>void}) {
  const [confirmDelete,setConfirmDelete]=useState<boolean>(false);
  const [expanded,setExpanded]=useState(false);
  const [actionError,setActionError]=useState("");
  const [busy,setBusy]=useState(false);
  const title=actionTitle(action.name) ?? action.name;
  async function change(enabled:boolean) {
    setBusy(true);setActionError("");
    try {await toggleAction(action.name,enabled);onRefresh();}
    catch(err) {setActionError(err instanceof Error ? err.message : String(err));}
    finally {setBusy(false);}
  }
  async function remove() {
    setBusy(true);setActionError("");
    try {await deleteAction(action.name);setConfirmDelete(false);onRefresh();}
    catch(err) {setActionError(err instanceof Error ? err.message : String(err));}
    finally {setBusy(false);}
  }
  return <article className={`action-card ${action.running ? "is-running" : ""}`}>
    <div className="action-line">
      <button role="switch" aria-checked={action.enabled} aria-label={t("action.toggle",{title})} disabled={busy} onClick={()=>change(!action.enabled)} className={`action-switch ${action.enabled ? "on" : ""}`}><span/></button>
      <h3>{title}</h3>
      <span className={`action-state ${action.running ? "running" : action.enabled ? "enabled" : ""}`}>
        <i className="status-dot"/>{t(action.running ? "action.working" : action.completed ? "action.completed" : action.enabled ? "action.waitingForGame" : "action.off")}</span>
      <button className="action-expand" aria-expanded={expanded} aria-controls={`action-details-${action.name}`} onClick={()=>setExpanded(!expanded)}>{expanded ? t("action.hide")+" ▴" : t("action.details")+" ▾"}</button></div>
    {actionError && <p role="alert" className="px-3 pb-2 text-xs text-red-300">{actionError}</p>}
    <div hidden={!expanded} id={`action-details-${action.name}`} className="action-details">
      <p className="action-description">{action.description || t("action.noDescription")}</p>
      <p className="mb-2 font-mono text-xs text-gray-500">{action.name}</p>
      {action.logs?.length ? <div className="action-log">{action.logs.map((line,i)=><div key={i}>{line}</div>)}</div> : <p className="text-xs text-gray-500">{t("action.noEvents")}</p>}
      <button disabled={busy} className="mt-3 text-xs text-red-300" onClick={()=>setConfirmDelete(true)}>{t("action.deleteFile")}</button>
      {confirmDelete && <div className="mt-3 rounded-lg border border-red-900 bg-red-950/30 p-3 text-xs"><p>{t("action.deleteAsk")} <strong>{action.name}</strong>? {t("action.cannotUndo")}</p><div className="mt-3 flex gap-3"><button onClick={()=>setConfirmDelete(false)} className="secondary-button">{t("common.cancel")}</button><button disabled={busy} onClick={remove} className="rounded bg-red-700 px-3 py-2">{t("action.delete")}</button></div></div>}</div>
  </article>;
}


const observed=new Set(["health","position","map","creatures","target","walk_cancel","messages","loot","inventory","effects","death"]);
const observationLabel=(name:string)=>observed.has(name)?t(`observation.${name}` as TextKey):name;

export default function DashboardPage() {
  const { state, mcpConnected, refresh, dashboardError } = useBot();

  const [view,setView]=useState<WorkspaceView>("cavebot");
  const [showProxySeq,setShowProxySeq]=useState<boolean>(false);

  const [controlError,setControlError]=useState("");
  const [controlBusy,setControlBusy]=useState(false);
  const [search,setSearch]=useState("");
  const [actionFilter,setActionFilter]=useState("all");
  async function setEmergency(unlock=false) {
    setControlBusy(true);setControlError("");
    try {await (unlock ? unlockAutomation() : emergencyStop());await refresh();}
    catch(err) {setControlError(err instanceof Error ? err.message : String(err));}
    finally {setControlBusy(false);}
  }
  async function setPause() {
    setControlBusy(true);setControlError("");
    try {await (state?.automation_paused ? resumeAutomation() : pauseAutomation());await refresh();}
    catch(e){setControlError(e instanceof Error ? e.message:String(e));}finally{setControlBusy(false);}
  }
  const potion=state?.actions.find(a=>a.name==="auto_potions");

  const actions=(state?.actions ?? []).filter(a=>!["auto_potions","auto_barrier_band","barrier_spell","speed_up","auto_hunter_knife","adaptive_cavebot","auto_combat"].includes(a.name));
  const shownActions=actions.filter(a=>(`${actionTitle(a.name) ?? ""} ${a.name} ${a.description}`).toLowerCase().includes(search.toLowerCase()) &&
    (actionFilter==="all" || (actionFilter==="running" ? a.running : actionFilter==="enabled" ? a.enabled : !a.enabled)));

  return <DashboardChrome view={view} onView={setView} state={state} mcpConnected={mcpConnected} busy={controlBusy}
    onStop={()=>setEmergency()} onUnlock={()=>setEmergency(true)} onResume={setPause} onRefresh={refresh} error={controlError || dashboardError || ""}>
    <section hidden={view!=="cavebot"} aria-label={t("page.cavebot")}><CavebotWorkspace state={state} active={view==="cavebot"} onRefresh={refresh}/><CavebotReports visible={view==="cavebot"}/></section>
    <section hidden={view!=="automation"} aria-label={t("page.automation")}>
      <AutomationStrip state={state} connected={mcpConnected} onRefresh={refresh} onSettings={()=>setView("automation")} onPause={setPause} busy={controlBusy}/>
      {potion ? <PotionPanel action={potion} diagnostics={state?.potion_diagnostics} paused={state?.automation_paused ?? false} onRefresh={refresh}/> :
        <div className="empty-panel"><h2>{t("page.waitingRules")}</h2><p>{t("page.waitingRulesNote")}</p></div>}
      <StandaloneCombatPanel state={state} onRefresh={refresh}/>
      <BarrierBandPanel state={state} onRefresh={refresh}/>
      <BarrierSpellPanel state={state} onRefresh={refresh}/>
      <SpeedUpPanel state={state} onRefresh={refresh}/>
      <HunterKnifePanel state={state} onRefresh={refresh}/>
      <ProfilePicker kind="bot" state={state} onRefresh={refresh}/>
      <details className="additional-actions"><summary>{t("page.otherActions")} <span>{plural("page.enabledCount",actions.filter(a=>a.enabled).length)}</span></summary>
      <div className="action-toolbar"><label className="search-field"><span aria-hidden="true">⌕</span><input aria-label={t("page.searchActions")} placeholder={t("page.searchActions")+"…"} value={search} onChange={e=>setSearch(e.target.value)}/></label>
        <select aria-label={t("page.actionFilter")} value={actionFilter} onChange={e=>setActionFilter(e.target.value)}><option value="all">{t("page.filterAll")}</option><option value="running">{t("page.filterRunning")}</option><option value="enabled">{t("page.filterEnabled")}</option><option value="off">{t("page.filterOff")}</option></select>
        <span>{t("page.shownOf",{shown:shownActions.length,total:actions.length})}</span>
      </div>
      <div className="action-grid">{shownActions.map(a=><ActionCard key={a.name} action={a} onRefresh={refresh}/>)}</div>
      {!shownActions.length && <div className="empty-panel"><h2>{t(state ? "page.noMatch" : "page.waitingBot")}</h2><p>{t(state ? "page.noMatchNote" : "page.waitingBotNote")}</p></div>}
      </details>
    </section>
    <section hidden={view!=="diagnostics"} aria-label={t("page.diagnostics")} className="space-y-5">
      <ItemIdentificationPanel state={state} online={mcpConnected} onRefresh={refresh}/>
      {state?.packet_observer && <details className="learned-drawer"><summary>{t("packets.title")} · {t(state.packet_observer.status==="active"?"packets.active":state.packet_observer.status==="degraded"?"packets.degraded":state.packet_observer.status==="closed"?"packets.closed":"packets.unavailable")}</summary><div className="p-3 text-xs space-y-1">
        <p>{t("packets.queue",{pending:state.packet_observer.pending,capacity:state.packet_observer.capacity,lag:state.packet_observer.lag_ms,max:state.packet_observer.max_lag_ms})}</p>
        <p>{t("packets.counts",{applied:state.packet_observer.applied,dropped:state.packet_observer.dropped,failures:state.packet_observer.failures})}</p>
        {state.packet_observer.last_problem && <p>{t("packets.lastProblem",{problem:["overload","stale","decode_failed","analysis_failed","invalid_frame","worker_stopped","notification_failed"].includes(state.packet_observer.last_problem)?t(`packets.problem.${state.packet_observer.last_problem}` as TextKey):t("packets.problem.other")})}</p>}
      </div></details>}
      {state?.observation_families && <details className="learned-drawer">
        <summary>{t("observation.title",{session:state.observation_families.session_id})}{mcpConnected ? "" : " · "+t("observation.lastState")}</summary>
        <div className="diagnostic-grid">{Object.entries(state.observation_families.families).map(([name, value])=><div className="diagnostic-card" key={name}>
          <span>{observationLabel(name)}</span><strong>{t(`observation.state.${value.state}`)}</strong>
          {value.age_ms != null && <small>{t("observation.age",{seconds:num(value.age_ms / 1000,{maximumFractionDigits:1})})}</small>}
        </div>)}</div>
        {state.adaptive_cavebot?.navigation_wait && <div className="p-3 text-xs">
          <strong>{t("observation.movementWait")}</strong><p>{state.adaptive_cavebot.navigation_wait.reason}</p>
          <p>{t("observation.resume",{what:state.adaptive_cavebot.navigation_wait.observations.map(observationLabel).join(", "),
            seconds:num(state.adaptive_cavebot.navigation_wait.review_in_ms / 1000,{maximumFractionDigits:2})})}</p>
        </div>}
      </details>}
      <details className="learned-drawer"><summary>{t("page.learnedTransitions")}</summary><TransitionLearningPanel map={state?.passive_map}/></details>
      <div className="diagnostic-grid">
        {[{label:t("diagnostics.panel"),ok:mcpConnected},{label:t("diagnostics.game"),ok:state?.connected},{label:t("diagnostics.dll"),ok:mcpConnected && state?.dll_injected},{label:t("diagnostics.bridge"),ok:mcpConnected && state?.dll_bridge_connected}].map(s=><div key={s.label} className="diagnostic-card"><span>{s.label}</span><strong className={s.ok ? "text-emerald-300" : "text-gray-400"}>{t(s.ok ? "diagnostics.active" : "diagnostics.inactive")}</strong></div>)}
      </div>
      <div className="diagnostic-grid two-columns"><div className="diagnostic-card"><span>{t("diagnostics.fromServer")}</span><strong>{num(state?.packets_from_server ?? 0)}</strong></div><div className="diagnostic-card"><span>{t("diagnostics.fromClient")}</span><strong>{num(state?.packets_from_client ?? 0)}</strong></div></div>
      <RouteReplayPanel state={state} online={mcpConnected}/>
      {view==="diagnostics"&&<MovementHistoryPanel online={mcpConnected}/>}
      {state?.proxy_sequence && <div className="diagnostic-section"><button className="secondary-button" onClick={()=>setShowProxySeq(!showProxySeq)}>{t(showProxySeq ? "diagnostics.hideSteps" : "diagnostics.showSteps")}</button>{showProxySeq && <ConnectionSequence seq={state.proxy_sequence} onClose={()=>setShowProxySeq(false)}/>}</div>}
      {state?.connected && state.player && <PlayerStats player={state.player}/>}
      <div className="diagnostic-section"><div className="section-caption"><div><h2>{t("diagnostics.creatures")}</h2><p>{t("diagnostics.creaturesNote")}</p></div></div><CreatureList creatures={state?.creatures ?? []}/></div>
    </section>
  </DashboardChrome>;
}
