"use client";

import { t, locale } from "@/lib/i18n";
import {useEffect, useRef, useState} from "react";
import {acknowledgeAlerts, configureAlerts, setAlertsPaused, type AlertConfig, type AlertKind, type AlertState} from "@/lib/api";

const label=(kind:AlertKind|"vitals")=>t(`alerts.kind.${kind}`);
const kinds:AlertKind[]=["private","default","player","ki_loss"];
type AlertEvent = AlertState["events"][number];

const DISMISSED_PREVIEW_KEY = "dbv.alerts.dismissedPreview";

function AlertPreview({event, onOpen, onDismiss}:{event:AlertEvent;onOpen:()=>void;onDismiss:()=>void}) {
  return <div className="alert-peek">
    <button type="button" className="alert-peek-open" onClick={onOpen}>
      <span role="status"><b>{label(event.kind)}</b> · {event.sender}</span>
      <small>{event.text}</small>
    </button>
    <button type="button" className="alert-peek-dismiss" aria-label={t("alerts.closePreview")}
      title={t("alerts.closePreview")} onClick={onDismiss}>×</button>
  </div>;
}

export default function AlertBell({alerts,connected,onRefresh}:{alerts?:AlertState|null;connected:boolean;onRefresh:()=>void|Promise<void>}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [config,setConfig]=useState<AlertConfig|null>(null);
  const [localPause,setLocalPause]=useState<{paused:boolean;session:string|null;through:number}|null>(null);
  const [dismissedPreview,setDismissedPreview]=useState<string|null>(() => {
    if (typeof window === "undefined") return null;
    try { return window.sessionStorage.getItem(DISMISSED_PREVIEW_KEY); } catch { return null; }
  });
  const lastMuted=useRef<{session:string;through:number}|null>(null);
  const ref=useRef<HTMLDivElement>(null);
  const legacy=!!alerts&&typeof alerts.paused!=="boolean";
  useEffect(()=>{
    let current=true;
    if(legacy)window.dbvAlerts?.panelPause?.().then(s=>{if(current)setLocalPause(s);}).catch(()=>{});
    else setLocalPause(null);
    return()=>{current=false;};
  },[legacy,alerts?.session]);
  useEffect(()=>{
    if(legacy&&localPause?.paused&&alerts)lastMuted.current={session:alerts.session,through:alerts.latest_id};
  },[legacy,localPause?.paused,alerts?.latest_id,alerts?.session]);
  const serial=JSON.stringify(alerts?.config);
  useEffect(()=>{if(serial)setConfig(JSON.parse(serial));},[serial]);
  useEffect(()=>{
    if(!open)return;
    const close=(event:PointerEvent)=>{if(!ref.current?.contains(event.target as Node))setOpen(false);};
    const escape=(event:KeyboardEvent)=>{if(event.key==="Escape")setOpen(false);};
    document.addEventListener("pointerdown",close);document.addEventListener("keydown",escape);
    return()=>{document.removeEventListener("pointerdown",close);document.removeEventListener("keydown",escape);};
  },[open]);
  async function change(kind:AlertKind, patch:Partial<AlertConfig[AlertKind]>) {
    if(!config)return;
    const previous=config,next={...config,[kind]:{...config[kind],...patch}};
    setConfig(next);setBusy(true);setError("");
    try{await configureAlerts(next);}catch(e){setConfig(previous);setError(String(e));}finally{setBusy(false);}
  }
  async function read() {
    if(!alerts)return;
    setBusy(true);setError("");
    try{await acknowledgeAlerts(alerts.latest_id);}catch(e){setError(String(e));}finally{setBusy(false);}
  }
  async function changePause() {
    if(!alerts)return;
    setBusy(true);setError("");
    try {
      if(typeof alerts.paused==="boolean"){
        await setAlertsPaused(!alerts.paused);
        await onRefresh();
      }else if(localPause&&window.dbvAlerts?.panelPause){
        lastMuted.current={session:alerts.session,through:alerts.latest_id};
        setLocalPause(await window.dbvAlerts.panelPause(!localPause.paused));
      }
    } catch(e) {
      setError(String(e));setOpen(true);
    } finally {
      setBusy(false);
    }
  }
  const latest=alerts?.events.at(-1);
  const previewKey=alerts && latest ? `${alerts.session}:${latest.id}` : null;
  function dismissPreview() {
    if (!previewKey) return;
    setDismissedPreview(previewKey);
    try { window.sessionStorage.setItem(DISMISSED_PREVIEW_KEY,previewKey); } catch { /* Keep the in-memory dismissal. */ }
    ref.current?.querySelector<HTMLButtonElement>(".alert-bell")?.focus({preventScroll:true});
  }
  const through=legacy&&alerts?Math.max(localPause?.session===alerts.session?localPause.through:0,
    lastMuted.current?.session===alerts.session?lastMuted.current.through:0):alerts?.silenced_through??0;
  const unread=legacy&&alerts?Math.min(alerts.unread,alerts.events.filter(e=>e.id>through).length):alerts?.unread??0;
  const automationPaused=alerts?.automation_paused===true;
  const paused=automationPaused || (alerts?.paused??localPause?.paused??true);
  const pauseLabel=t(automationPaused?"alerts.pausedByAutomation":paused?"alerts.resume":"alerts.pause");
  return <div className="alert-hub" ref={ref}>
    <button className={`alert-bell ${unread&&!paused?"has-alert":""}`} aria-label={paused?t("alerts.paused"):unread?t("alerts.unread",{n:unread}):t("alerts.title")} title={t(paused?"alerts.paused":"alerts.titleSound")} aria-expanded={open} aria-controls="alert-panel" onClick={()=>setOpen(v=>!v)}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></svg>
      <span>{t("alerts.title")}</span>{!paused&&unread>0&&<span className="alert-count">{unread>99?"99+":unread}</span>}
    </button>
    {(typeof alerts?.paused==="boolean"||localPause)&&<button className={`alert-pause${paused?" is-paused":""}`} aria-label={pauseLabel}
      title={pauseLabel} disabled={busy||!connected||automationPaused} onClick={()=>void changePause()}>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
        <path d={paused?"M4 2.5 13 8l-9 5.5z":"M3 2h3.5v12H3zm6.5 0H13v12H9.5z"}/>
      </svg><span>{t(automationPaused?"common.pause":paused?"common.resume":"alerts.hold")}</span>
    </button>}
    {!open&&!paused&&unread>0&&latest&&latest.id>through&&previewKey!==dismissedPreview&&
      <AlertPreview event={latest} onOpen={()=>setOpen(true)} onDismiss={dismissPreview}/>}

    {open&&<section id="alert-panel" className="alert-panel" aria-label={t("alerts.title")}>
      <header><strong>{t("alerts.title")}</strong><span>{t(automationPaused?"alerts.automationPause":paused?"alerts.held":alerts?.cavebot_active?"alerts.cavebotOn":"alerts.cavebotOff")}</span><button aria-label={t("alerts.close")} className="alert-close" onClick={()=>setOpen(false)}>×</button></header>
      {!alerts||!config?<p>{t("alerts.needsSession")}</p>:<>
        <div className="alert-setting-head"><span>{t("alerts.event")}</span><span>{t("alerts.signal")}</span><span>{t("alerts.when")}</span></div>
        {kinds.filter(kind=>config[kind]).map(kind=><div key={kind} className="alert-setting">
          <label><input type="checkbox" checked={config[kind].enabled} disabled={busy||!connected} onChange={e=>void change(kind,{enabled:e.target.checked})}/>{label(kind)}</label>
          <label className="alert-sound-toggle"><input type="checkbox" aria-label={t("alerts.soundAria",{kind:label(kind)})} checked={config[kind].sound} disabled={busy||!connected||!config[kind].enabled} onChange={e=>void change(kind,{sound:e.target.checked})}/></label>
          <select aria-label={t("alerts.whenAria",{kind:label(kind)})} value={config[kind].scope} disabled={busy||!connected||!config[kind].enabled} onChange={e=>void change(kind,{scope:e.target.value as "always"|"cavebot"})}><option value="always">{t("alerts.always")}</option><option value="cavebot">{t("alerts.withV2")}</option></select>
        </div>)}
        <p className="alert-hint">{t("alerts.hint")}</p>
        {config.ki_loss&&<p className="alert-hint">{t("alerts.kiHint")}</p>}
        <div className="alert-tools"><button disabled={paused} onClick={async()=>{setError("");try{if(!window.dbvAlerts)throw new Error(t("alerts.restartForSound"));await window.dbvAlerts.testSound();}catch(e){setError(String(e));}}}>♪ {t("alerts.test")}</button><button disabled={busy||!connected||!unread} onClick={()=>void read()}>{t("alerts.markRead")}</button></div>
        {error&&<p role="alert" className="alert-error">{error}</p>}
        <div className="alert-history" aria-label={t("alerts.history")}>{[...alerts.events].reverse().map(e=><article key={`${alerts.session}-${e.id}`}><div><b>{label(e.kind)}</b><time>{new Date(e.at*1000).toLocaleTimeString(locale())}</time></div><strong>{e.sender}</strong>{e.position&&<span className="alert-coordinates"> ({e.position.join(", ")})</span>}<p>{e.text}</p></article>)}{!alerts.events.length&&<p className="alert-empty">{t("action.noEvents")}</p>}</div>
        <footer>{t(!connected?"alerts.noBot":!alerts.map_fresh?"alerts.playersWaiting":"alerts.playersActive")} <span title={t("alerts.chatTitle")}>{t("alerts.chat")}: {alerts.chat_received?t("alerts.chatCount",{n:alerts.chat_received}):t("alerts.chatWaiting")}</span></footer>
      </>}
    </section>}
  </div>;
}
