"use client";

import { useBot } from "@/lib/useBot";
import RouteReplayPanel from "./RouteReplayPanel";
import MovementHistoryPanel from "./MovementHistoryPanel";
import CavebotReports from "./CavebotReports";
import SpeedUpPanel from "./SpeedUpPanel";
import HunterKnifePanel from "./HunterKnifePanel";
import ProfilePicker from "./ProfilePicker";
import { useState } from "react";
import {
  toggleAction,
  emergencyStop,
  pauseAutomation,
  resumeAutomation,
  unlockAutomation,
  deleteAction,
} from "@/lib/api";
import type {
  ActionInfo,
  PlayerInfo,
  CreatureInfo,
  ProxySequence,
} from "@/lib/api";
import CavebotWorkspace from "./CavebotWorkspace";
import AutomationStrip, {actionTitles} from "./AutomationStrip";
import PotionPanel from "./PotionPanel";
import TransitionLearningPanel from "./TransitionLearningPanel";
import DashboardChrome, { type WorkspaceView } from "./DashboardChrome";
import BarrierBandPanel from "./BarrierBandPanel";
import BarrierSpellPanel from "./BarrierSpellPanel";

function ActionCard({action,onRefresh}:{action:ActionInfo;onRefresh:()=>void}) {
  const [confirmDelete,setConfirmDelete]=useState(false);
  const [expanded,setExpanded]=useState(false);
  const [actionError,setActionError]=useState("");
  const [busy,setBusy]=useState(false);
  const title=actionTitles[action.name] ?? action.name;
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
      <button role="switch" aria-checked={action.enabled} aria-label={`${title}: włącz lub wyłącz`} disabled={busy} onClick={()=>change(!action.enabled)} className={`action-switch ${action.enabled ? "on" : ""}`}><span/></button>
      <h3>{title}</h3>
      <span className={`action-state ${action.running ? "running" : action.enabled ? "enabled" : ""}`}>
        <i className="status-dot"/>{action.running ? "Pracuje" : action.completed ? "Zakończona" : action.enabled ? "Czeka na grę" : "Wyłączona"}
      </span>
      <button className="action-expand" aria-expanded={expanded} aria-controls={`action-details-${action.name}`} onClick={()=>setExpanded(!expanded)}>{expanded ? "Ukryj ▴" : "Opis / logi ▾"}</button>
    </div>
    {actionError && <p role="alert" className="px-3 pb-2 text-xs text-red-300">{actionError}</p>}
    <div hidden={!expanded} id={`action-details-${action.name}`} className="action-details">
      <p className="action-description">{action.description || "Brak opisu tej akcji."}</p>
      <p className="mb-2 font-mono text-xs text-gray-500">{action.name}</p>
      {action.logs?.length ? <div className="action-log">{action.logs.map((line,i)=><div key={i}>{line}</div>)}</div> : <p className="text-xs text-gray-500">Brak zdarzeń w tej sesji.</p>}
      <button disabled={busy} className="mt-3 text-xs text-red-300" onClick={()=>setConfirmDelete(true)}>Usuń plik akcji…</button>
      {confirmDelete && <div className="mt-3 rounded-lg border border-red-900 bg-red-950/30 p-3 text-xs"><p>Usunąć <strong>{action.name}</strong>? Tej operacji nie można cofnąć.</p><div className="mt-3 flex gap-3"><button onClick={()=>setConfirmDelete(false)} className="secondary-button">Anuluj</button><button disabled={busy} onClick={remove} className="rounded bg-red-700 px-3 py-2">Usuń akcję</button></div></div>}
    </div>
  </article>;
}

function StatBar({
  label,
  current,
  max,
  color,
}: {
  label: string;
  current: number;
  max: number;
  color: string;
}) {
  const pct = max > 0 ? Math.round((current / max) * 100) : 0;
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="tabular-nums">
          {current} / {max}
        </span>
      </div>
      <div className="h-3 rounded-full bg-gray-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-300 ${color}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}


function ConnectionSequence({ seq, onClose }: { seq: ProxySequence; onClose: () => void }) {
  const steps = [
    { key: "proxy_created", label: "Proxy Created" },
    { key: "listening", label: "Listening" },
    { key: "client_connected", label: "Client Connected" },
    { key: "server_connected", label: "Server Connected" },
    { key: "xtea_captured", label: "XTEA Captured" },
    { key: "logged_in", label: "Logged In" },
    { key: "packets_flowing", label: "Packets Flowing" },
  ] as const;

  const ts = seq.timestamps ?? {};
  const firstFail = steps.findIndex((s) => !seq[s.key]);

  return (
    <div className="flex items-center gap-1 flex-wrap mb-4">
      {steps.map((s, i) => {
        const ok = seq[s.key];
        const isBlocker = i === firstFail;
        const t = ts[s.key];
        const timeStr = t != null ? `+${t}s` : "";
        return (
          <div key={s.key} className="flex items-center gap-1">
            {i > 0 && (
              <span className={`text-xs ${ok ? "text-emerald-600" : "text-gray-600"}`}>
                &rarr;
              </span>
            )}
            <div
              className={`rounded px-2 py-1 text-xs font-medium ${
                ok
                  ? "bg-emerald-900/50 text-emerald-300"
                  : isBlocker
                  ? "bg-red-900/50 text-red-300 animate-pulse"
                  : "bg-gray-800 text-gray-500"
              }`}
              title={`${s.label}${timeStr ? ` (${timeStr})` : ""}`}
            >
              {s.label}{timeStr && <span className="ml-1 text-[10px] opacity-70">{timeStr}</span>}
            </div>
          </div>
        );
      })}
      <button
        onClick={onClose}
        className="text-xs text-gray-500 hover:text-gray-300 ml-1"
      >
        &times;
      </button>
    </div>
  );
}

function PlayerStats({ player }: { player: PlayerInfo }) {
  const [x, y, z] = player.position;
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 p-4 mb-8 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-gray-300">
          Level {player.level}
        </span>
        <span className="text-xs text-gray-500 tabular-nums">
          ({x}, {y}, {z})
        </span>
      </div>
      <StatBar label="HP" current={player.hp} max={player.max_hp} color="bg-red-500" />
      <StatBar label="Mana" current={player.mana} max={player.max_mana} color="bg-blue-500" />
    </div>
  );
}

function CreatureList({ creatures }: { creatures: CreatureInfo[] }) {
  return (
    <div className="mb-8">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
          Creatures
        </h2>
        <span className="rounded-full bg-gray-700 px-2 py-0.5 text-xs tabular-nums text-gray-300">
          {creatures.length}
        </span>
      </div>
      {creatures.length > 0 ? (
        <div className="rounded-lg border border-gray-700 bg-gray-800 divide-y divide-gray-700">
          {creatures.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-4 py-2">
              <span className="text-sm text-gray-200 w-28 shrink-0 truncate" title={`#${c.id}`}>
                {c.name || `#${c.id}`}
              </span>
              <span className="text-xs tabular-nums text-gray-500 w-24 shrink-0">
                ({c.x}, {c.y}, {c.z})
              </span>
              <div className="flex-1 h-2.5 rounded-full bg-gray-700 overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-300 bg-gradient-to-r from-emerald-600 to-emerald-400"
                  style={{ width: `${c.health}%` }}
                />
              </div>
              <span className="text-xs tabular-nums text-gray-300 w-10 text-right">
                {c.health}%
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-500">No creatures nearby</p>
      )}
    </div>
  );
}

const observationLabels: Record<string,string> = {
  health:"HP i Ki", position:"Pozycja postaci", map:"Mapa i pola", creatures:"Stworzenia", target:"Wybrany cel",
  walk_cancel:"Anulowanie kroku", messages:"Komunikaty gry", loot:"Loot", inventory:"Ekwipunek",
  effects:"Efekty postaci", death:"Śmierć"
};
const observationStates = {fresh:"Aktualne", stale:"Nieaktualne", waiting:"Brak próbki", gap:"Luka", observed:"Zaobserwowano"};

export default function Dashboard() {
  const { state, mcpConnected, refresh, dashboardError } = useBot();

  const [view,setView]=useState<WorkspaceView>("cavebot");
  const [showProxySeq,setShowProxySeq]=useState(false);

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

  const actions=(state?.actions ?? []).filter(a=>!["auto_potions","auto_barrier_band","barrier_spell","speed_up","auto_hunter_knife","adaptive_cavebot"].includes(a.name));
  const shownActions=actions.filter(a=>(`${actionTitles[a.name] ?? ""} ${a.name} ${a.description}`).toLowerCase().includes(search.toLowerCase()) &&
    (actionFilter==="all" || (actionFilter==="running" ? a.running : actionFilter==="enabled" ? a.enabled : !a.enabled)));

  return <DashboardChrome view={view} onView={setView} state={state} mcpConnected={mcpConnected} busy={controlBusy}
    onStop={()=>setEmergency()} onUnlock={()=>setEmergency(true)} onResume={setPause} onRefresh={refresh} error={controlError || dashboardError || ""}>
    <section hidden={view!=="cavebot"} aria-label="Konfiguracja Cavebota"><CavebotWorkspace state={state} active={view==="cavebot"} onRefresh={refresh}/><CavebotReports visible={view==="cavebot"}/></section>
    <section hidden={view!=="automation"} aria-label="Widok automatyki">
      <AutomationStrip state={state} connected={mcpConnected} onRefresh={refresh} onSettings={()=>setView("automation")} onPause={setPause} busy={controlBusy}/>
      {potion ? <PotionPanel action={potion} diagnostics={state?.potion_diagnostics} paused={state?.automation_paused ?? false} onRefresh={refresh}/> :
        <div className="empty-panel"><h2>Czekam na ustawienia reguł</h2><p>Pojawią się po połączeniu panelu z botem.</p></div>}
      <BarrierBandPanel state={state} onRefresh={refresh}/>
      <BarrierSpellPanel state={state} onRefresh={refresh}/>
      <SpeedUpPanel state={state} onRefresh={refresh}/>
      <HunterKnifePanel state={state} onRefresh={refresh}/>
      <ProfilePicker kind="bot" state={state} onRefresh={refresh}/>
      <details className="additional-actions"><summary>Pozostałe akcje <span>{actions.filter(a=>a.enabled).length} włączonych</span></summary>
      <div className="action-toolbar"><label className="search-field"><span aria-hidden="true">⌕</span><input aria-label="Szukaj akcji" placeholder="Szukaj akcji…" value={search} onChange={e=>setSearch(e.target.value)}/></label>
        <select aria-label="Filtr akcji" value={actionFilter} onChange={e=>setActionFilter(e.target.value)}><option value="all">Wszystkie akcje</option><option value="running">Działające</option><option value="enabled">Włączone</option><option value="off">Wyłączone</option></select>
        <span>{shownActions.length} z {actions.length}</span>
      </div>
      <div className="action-grid">{shownActions.map(a=><ActionCard key={a.name} action={a} onRefresh={refresh}/>)}</div>
      {!shownActions.length && <div className="empty-panel"><h2>{state ? "Brak pasujących akcji" : "Czekam na połączenie z botem"}</h2><p>{state ? "Zmień filtr lub wpisaną nazwę." : "Lista pojawi się po otrzymaniu danych."}</p></div>}
      </details>
    </section>
    <section hidden={view!=="diagnostics"} aria-label="Widok diagnostyki" className="space-y-5">
      {state?.packet_observer && <details className="learned-drawer"><summary>Analiza pakietów · {state.packet_observer.status==="active"?"aktywna":state.packet_observer.status==="degraded"?"luki w odczycie":state.packet_observer.status==="closed"?"zakończona":"niedostępna"}</summary><div className="p-3 text-xs space-y-1">
        <p>Kolejka {state.packet_observer.pending}/{state.packet_observer.capacity} · opóźnienie {state.packet_observer.lag_ms} ms · maksimum {state.packet_observer.max_lag_ms} ms</p>
        <p>Przetworzone {state.packet_observer.applied} · pominięte obserwacje {state.packet_observer.dropped} · błędy {state.packet_observer.failures}</p>
        {state.packet_observer.last_problem && <p>Ostatni problem: {({overload:"przeciążenie kolejki",stale:"nieaktualna obserwacja",decode_failed:"nierozpoznana ramka",analysis_failed:"błąd analizy",invalid_frame:"nieprawidłowa ramka",worker_stopped:"analizator zatrzymany",notification_failed:"błąd powiadomienia"} as Record<string,string>)[state.packet_observer.last_problem] ?? "błąd obserwacji"}. Pominięte obserwacje dotyczą analizy bota; pakiety gry są przekazywane osobno.</p>}
      </div></details>}
      {state?.observation_families && <details className="learned-drawer">
        <summary>Odczyty sesji {state.observation_families.session_id}{mcpConnected ? "" : " · ostatni stan"}</summary>
        <div className="diagnostic-grid">{Object.entries(state.observation_families.families).map(([name, value])=><div className="diagnostic-card" key={name}>
          <span>{observationLabels[name] ?? name}</span><strong>{observationStates[value.state]}</strong>
          {value.age_ms != null && <small>Wiek próbki {(value.age_ms / 1000).toLocaleString("pl-PL",{maximumFractionDigits:1})} s</small>}
        </div>)}</div>
        {state.adaptive_cavebot?.navigation_wait && <div className="p-3 text-xs">
          <strong>Oczekiwanie ruchu</strong><p>{state.adaptive_cavebot.navigation_wait.reason}</p>
          <p>Wznowienie oceny po obserwacji {state.adaptive_cavebot.navigation_wait.observations.map(name=>observationLabels[name] ?? name).join(", ")}
            {" lub po "}{(state.adaptive_cavebot.navigation_wait.review_in_ms / 1000).toLocaleString("pl-PL",{maximumFractionDigits:2})} s.</p>
        </div>}
      </details>}
      <details className="learned-drawer"><summary>Wyuczone przejścia</summary><TransitionLearningPanel map={state?.passive_map}/></details>
      <div className="diagnostic-grid">
        {[{label:"Panel / MCP",ok:mcpConnected},{label:"Sesja gry",ok:state?.connected},{label:"DLL w kliencie",ok:mcpConnected && state?.dll_injected},{label:"Połączenie mostu",ok:mcpConnected && state?.dll_bridge_connected}].map(s=><div key={s.label} className="diagnostic-card"><span>{s.label}</span><strong className={s.ok ? "text-emerald-300" : "text-gray-400"}>{s.ok ? "Aktywne" : "Nieaktywne"}</strong></div>)}
      </div>
      <div className="diagnostic-grid two-columns"><div className="diagnostic-card"><span>Pakiety z serwera</span><strong>{(state?.packets_from_server ?? 0).toLocaleString("pl-PL")}</strong></div><div className="diagnostic-card"><span>Pakiety z klienta</span><strong>{(state?.packets_from_client ?? 0).toLocaleString("pl-PL")}</strong></div></div>
      <RouteReplayPanel state={state} online={mcpConnected}/>
      {view==="diagnostics"&&<MovementHistoryPanel online={mcpConnected}/>}
      {state?.proxy_sequence && <div className="diagnostic-section"><button className="secondary-button" onClick={()=>setShowProxySeq(!showProxySeq)}>{showProxySeq ? "Ukryj etapy połączenia" : "Pokaż etapy połączenia"}</button>{showProxySeq && <ConnectionSequence seq={state.proxy_sequence} onClose={()=>setShowProxySeq(false)}/>}</div>}
      {state?.connected && state.player && <PlayerStats player={state.player}/>}
      <div className="diagnostic-section"><div className="section-caption"><div><h2>Lista stworzeń z DLL</h2><p>To osobny odczyt diagnostyczny. Typy postaci na pasywnej mapie pochodzą z pól klienta.</p></div></div><CreatureList creatures={state?.creatures ?? []}/></div>
    </section>
  </DashboardChrome>;
}
