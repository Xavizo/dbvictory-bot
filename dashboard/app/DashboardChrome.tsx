"use client";

import { useEffect, useRef, useState, useCallback, type ReactNode, type CSSProperties } from "react";
import type { BotState } from "@/lib/api";
import CavebotRunBar from "./CavebotRunBar";
import AlertBell from "./AlertBell";
import SessionStats from "./SessionStats";
import dynamic from "next/dynamic";
const GlobalAtlas=dynamic(()=>import("./GlobalAtlas"),{ssr:false});

export type WorkspaceView = "automation" | "cavebot" | "diagnostics" | "items";
const workspaces: {id:WorkspaceView;nav:string;path:string}[] = [
  {id:"automation",nav:"Automatyka",path:"M4 6h16M4 12h16M4 18h16M8 3v6m8 0v6m-6 0v6"},
  {id:"cavebot",nav:"Cavebot",path:"M5 6a2 2 0 104 0 2 2 0 00-4 0zm10 12a2 2 0 104 0 2 2 0 00-4 0zM7 8v5a3 3 0 003 3h4a3 3 0 000-6h-2"},
  {id:"items",nav:"Test przedmiotów",path:"M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4m-9 4v10"},
  {id:"diagnostics",nav:"Diagnostyka",path:"M3 12h4l3-8 4 16 3-8h4"},
];
function Icon({path}:{path:string}) {
  return <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={path}/></svg>;
}
function Resource({label,current,max,color,available}:{label:string;current:number;max:number;color:string;available:boolean}) {
  const percentage=max>0 ? Math.round(current/max*100) : 0;
  return <div className="resource-meter" style={{"--meter-color":color} as CSSProperties}>
    <span>{label}</span><div className="resource-track"><i style={{width:`${available ? Math.max(0,Math.min(100,percentage)) : 0}%`}}/>
      <strong>{available ? `${current.toLocaleString("pl-PL")} / ${max.toLocaleString("pl-PL")}` : "brak odczytu"}</strong></div>
    <small>{available ? `${percentage}%` : "—"}</small>
  </div>;
}
export default function DashboardChrome({view,onView,state,mcpConnected,busy,onStop,onUnlock,onResume,onRefresh,error,children}:{
  view:WorkspaceView;onView:(v:WorkspaceView)=>void;state:BotState|null;mcpConnected:boolean;busy:boolean;
  onStop:()=>void;onUnlock:()=>void;onResume:()=>void;onRefresh:()=>void;error:string;children:ReactNode;
}) {
  const [atlasOpen,setAtlasOpen]=useState(false);
  const closeAtlas=useCallback(()=>setAtlasOpen(false),[]);
  const scrollRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{if(scrollRef.current) scrollRef.current.scrollTop=0;},[view]);
  const running=state?.actions.filter(a=>a.running && a.name!=="dll_bridge") ?? [];
  const connected=mcpConnected && !!state?.connected, paused=!!state?.automation_paused, preview=!!state?.passive_map?.preview;
  const player=state?.player, map=state?.passive_map, families=state?.observation_families?.families;
  const healthFresh = families?.health ? families.health.state === "fresh"
    : (player?.stats_age ?? -1) >= 0 && (player?.stats_age ?? Infinity) <= 2;
  const mapFresh = map?.status === "ready" && !map.stale && !!map.center
    && (!families?.map || families.map.state === "fresh");
  const position = mapFresh ? map.center : families?.position?.state === "fresh" ? player?.position : null;
  const worldReady = connected && !preview && healthFresh && mapFresh
    && (!state?.login_startup || state.login_startup.phase === "activated");
  let gameStatus="Oczekiwanie na silnik";
  if (!mcpConnected && state) gameStatus="Brak świeżych danych z panelu";
  else if (preview) gameStatus="Demo";
  else if (worldReady) gameStatus="Sesja gry aktywna";
  else if (connected && state?.login_startup?.phase === "failed") gameStatus="Błąd uruchamiania automatyki";
  else if (connected && !healthFresh) gameStatus="Czekam na HP i Ki";
  else if (connected && !mapFresh) gameStatus="Czekam na pozycję i mapę";
  else if (connected) gameStatus="Przygotowanie sesji";
  else if (mcpConnected && state?.proxy_sequence?.client_connected) gameStatus="Łączenie z grą…";
  else if (mcpConnected && state?.proxy_sequence?.listening) gameStatus="Zaloguj się do gry";
  else if (mcpConnected) gameStatus="Czekam na klienta";
  return <div className="dashboard-shell">
    <header className="console-header">
      <div className="console-brand"><span aria-hidden="true">D<span>V</span></span><strong>DBVictory</strong><small>control panel</small></div>
      {preview && <em className="demo-badge">DEMO · BEZ GRY</em>}
      <div className="connection-cluster" aria-label="Stan połączenia">
        <span title={mcpConnected ? "Panel połączony z botem" : "Panel bez połączenia"}><i className={`status-dot ${mcpConnected ? "online" : ""}`}/>Panel</span>
        <span role="status"><i className={`status-dot ${worldReady ? "online" : ""}`}/>{gameStatus}</span>
        <span title={mcpConnected && state?.dll_bridge_connected ? "Most DLL połączony" : "Brak potwierdzenia mostu DLL"}><i className={`status-dot ${mcpConnected && state?.dll_bridge_connected ? "online" : ""}`}/>DLL</span>
      </div>
      <AlertBell alerts={state?.alerts} connected={mcpConnected} onRefresh={onRefresh}/>
      <span className={`automation-pill ${paused ? "paused" : running.length ? "active" : ""}`}>{paused ? "PAUZA" : `Aktywne: ${running.length}`}</span>
      <button className="emergency-button" disabled={busy || !state} onClick={onStop} title="Awaryjny STOP. Pause/Break: pauza i wznowienie poprzednich akcji."><span aria-hidden="true">■</span> STOP <small>wszystkie akcje</small></button>
    </header>
    {state?.alerts?.vitals_alarm_id != null && <div role="alert" className="error-banner">
      Brak aktualnego HP postaci. Automatyczne leczenie czeka na odczyt.
    </div>}
    {state?.pause_hotkey?.error&&<div role="alert" className="error-banner">Pause/Break: {state.pause_hotkey.error}</div>}
    <div className="player-ribbon">
      <div className="level-display"><span>LVL</span><strong>{connected && healthFresh && player ? player.level : "—"}</strong></div>
      <Resource label="HP" current={player?.hp ?? 0} max={player?.max_hp ?? 0} color="#e77983" available={connected && healthFresh && !!player?.max_hp}/>
      <Resource label="KI" current={player?.mana ?? 0} max={player?.max_mana ?? 0} color="#70b5f3" available={connected && healthFresh && !!player?.max_mana}/>
      <div className="position-display" title="Współrzędne postaci"><span>XYZ</span><strong>{connected && position ? position.join(" · ") : "—"}</strong></div>
    </div>
    <nav className="console-tabs" aria-label="Widoki panelu">{workspaces.map(w=><button key={w.id} aria-current={view===w.id ? "page" : undefined} className={view===w.id ? "is-active" : ""} onClick={()=>onView(w.id)}>
      <Icon path={w.path}/><span>{w.nav}</span>
      {w.id==="automation" && state?.actions.find(a=>a.name==="auto_potions")?.running && <i className="status-dot online"/>}
      {w.id==="cavebot" && state?.actions.some(a=>a.name==="adaptive_cavebot" && a.running) && <i className="status-dot online"/>}
    </button>)}<button className="atlas-launcher" aria-label="Otwórz atlas i mapę" aria-haspopup="dialog" onClick={()=>setAtlasOpen(true)}><Icon path="M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2V5zm6-2v16m6-14v16"/><span>Atlas</span></button></nav>
    <SessionStats stats={state?.session_statistics} senzu={state?.senzu_usage}/>
    {atlasOpen&&<GlobalAtlas state={state} onRefresh={onRefresh} onClose={closeAtlas}/>}
    <CavebotRunBar state={state} connected={mcpConnected} onRefresh={onRefresh}/>
    <div className="workspace-scroll" ref={scrollRef}>
      {paused && <div className="pause-banner"><span><strong>Automatyka zatrzymana.</strong> {state?.automation_pause_kind==="manual" ? "Wybór akcji został zachowany." : "Odblokowanie nie uruchamia akcji."}</span><button disabled={busy || !mcpConnected} onClick={state?.automation_pause_kind==="manual" ? onResume : onUnlock}>{state?.automation_pause_kind==="manual" ? "Wznów wybrane" : "Odblokuj"}</button></div>}
      {error && <div role="alert" className="error-banner">{error}</div>}
      <main id="workspace-content">{children}</main>
    </div>
  </div>;
}
