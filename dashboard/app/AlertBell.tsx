"use client";

import {useEffect, useRef, useState} from "react";
import {acknowledgeAlerts, configureAlerts, setAlertsPaused, type AlertConfig, type AlertKind, type AlertState} from "@/lib/api";

const labels:Record<AlertKind,string>={private:"Wiadomość prywatna",default:"Czat Default",player:"Pojawienie się gracza"};
const kinds:AlertKind[]=["private","default","player"];
export default function AlertBell({alerts,connected,onRefresh}:{alerts?:AlertState|null;connected:boolean;onRefresh:()=>void|Promise<void>}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [config,setConfig]=useState<AlertConfig|null>(null);
  const [localPause,setLocalPause]=useState<{paused:boolean;session:string|null;through:number}|null>(null);
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
  const through=legacy&&alerts?Math.max(localPause?.session===alerts.session?localPause.through:0,
    lastMuted.current?.session===alerts.session?lastMuted.current.through:0):alerts?.silenced_through??0;
  const unread=legacy&&alerts?Math.min(alerts.unread,alerts.events.filter(e=>e.id>through).length):alerts?.unread??0;
  const paused=alerts?.paused??localPause?.paused??false;
  const pauseLabel=paused?"Wznów alerty":"Wstrzymaj alerty";
  return <div className="alert-hub" ref={ref}>
    <button className={`alert-bell ${unread&&!paused?"has-alert":""}`} aria-label={`Alerty${paused?" wstrzymane":unread?`: ${unread} nieprzeczytanych`:""}`} title={paused?"Alerty wstrzymane":"Alerty i dźwięk"} aria-expanded={open} aria-controls="alert-panel" onClick={()=>setOpen(v=>!v)}>
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M9 21h6"/></svg>
      <span>Alerty</span>{!paused&&unread>0&&<span className="alert-count">{unread>99?"99+":unread}</span>}
    </button>
    {(typeof alerts?.paused==="boolean"||localPause)&&<button className={`alert-pause${paused?" is-paused":""}`} aria-label={pauseLabel}
      title={pauseLabel} disabled={busy||!connected} onClick={()=>void changePause()}>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
        <path d={paused?"M4 2.5 13 8l-9 5.5z":"M3 2h3.5v12H3zm6.5 0H13v12H9.5z"}/>
      </svg><span>{paused?"Wznów":"Wstrzymaj"}</span>
    </button>}
    {!open&&!paused&&unread>0&&latest&&latest.id>through&&<button className="alert-peek" onClick={()=>setOpen(true)}><span role="status"><b>{labels[latest.kind]}</b> · {latest.sender}</span><small>{latest.text}</small></button>}
    {open&&<section id="alert-panel" className="alert-panel" aria-label="Alerty">
      <header><strong>Alerty</strong><span>{paused?"Wstrzymane":alerts?.cavebot_active?"Cavebot działa":"Cavebot zatrzymany"}</span><button aria-label="Zamknij alerty" className="alert-close" onClick={()=>setOpen(false)}>×</button></header>
      {!alerts||!config?<p>Alerty wymagają nowej sesji silnika.</p>:<>
        <div className="alert-setting-head"><span>Zdarzenie</span><span>Dźwięk</span><span>Kiedy</span></div>
        {kinds.map(kind=><div key={kind} className="alert-setting">
          <label><input type="checkbox" checked={config[kind].enabled} disabled={busy||!connected} onChange={e=>void change(kind,{enabled:e.target.checked})}/>{labels[kind]}</label>
          <label className="alert-sound-toggle"><input type="checkbox" aria-label={`Dźwięk: ${labels[kind]}`} checked={config[kind].sound} disabled={busy||!connected||!config[kind].enabled} onChange={e=>void change(kind,{sound:e.target.checked})}/></label>
          <select aria-label={`Kiedy: ${labels[kind]}`} value={config[kind].scope} disabled={busy||!connected||!config[kind].enabled} onChange={e=>void change(kind,{scope:e.target.value as "always"|"cavebot"})}><option value="always">Zawsze</option><option value="cavebot">Przy V2</option></select>
        </div>)}
        <p className="alert-hint">Gracz: sygnał co 5 s do zniknięcia z mapy. Czat: osobny sygnał przy każdej wiadomości. Dźwięki działają też w zasobniku.</p>
        <div className="alert-tools"><button disabled={paused} onClick={async()=>{setError("");try{if(!window.dbvAlerts)throw new Error("Uruchom ponownie panel, aby wczytać obsługę dźwięku.");await window.dbvAlerts.testSound();}catch(e){setError(String(e));}}}>♪ Test dźwięku</button><button disabled={busy||!connected||!unread} onClick={()=>void read()}>Oznacz jako przeczytane</button></div>
        {error&&<p role="alert" className="alert-error">{error}</p>}
        <div className="alert-history" aria-label="Historia alertów">{[...alerts.events].reverse().map(e=><article key={`${alerts.session}-${e.id}`}><div><b>{labels[e.kind]}</b><time>{new Date(e.at*1000).toLocaleTimeString("pl-PL")}</time></div><strong>{e.sender}</strong>{e.position&&<span className="alert-coordinates"> ({e.position.join(", ")})</span>}<p>{e.text}</p></article>)}{!alerts.events.length&&<p className="alert-empty">Brak zdarzeń w tej sesji.</p>}</div>
        <footer>{!connected?"Brak połączenia z botem.":!alerts.map_fresh?"Gracze: czekam na świeżą mapę.":"Gracze: pasywny odczyt aktywny."} <span title="Czytamy rozpoznane komunikaty czatu. Nie skanujemy zawartości mapy w poszukiwaniu tekstu.">Czat: {alerts.chat_received?`${alerts.chat_received} rozpoznanych komunikatów.`:"oczekiwanie na komunikat."}</span></footer>
      </>}
    </section>}
  </div>;
}
