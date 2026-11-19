"use client";
import {useEffect, useState} from "react";
import {fetchCavebotReports} from "@/lib/api";

type Decision = {ms:number;kind:string;position:number[]|null;waypoint:number|null;engaged:number|null;
  goal:number|null;target:number[]|null;detail:string|null};
type Report = {id:string;route:string;revision:string|null;profile:string|null;started_at:number;lap:number;complete:boolean;
  duration_ms:number;blocked_ms:number;unobserved_ms:number;xp:number|null;xp_hour:number|null;senzu:number|null;
  senzu_million:number|null;group_median:number|null;observed_deaths:number|null;event_count:number;end_reason:string;events?:Decision[]};
type Index = {active:{route:string;lap:number;events:Decision[]}|null;reports:Report[];more:boolean};
const labels:Record<string,string> = {travel:"Marsz",waypoint:"Następny waypoint",blocked:"Blokada ruchu",
  manual:"Ręczny ruch",group:"Osiągnięty limit grupy",corridor:"Walka przed przewężeniem",transition:"Walka przed przejściem",
  timeout:"Koniec czasu lurowania",lure:"Lurowanie",fight:"Walka",positioning:"Wybór pola walki",return:"Powrót na trasę",
  chase:"Pościg",distance:"Dystans",stationary:"Postój ≥ 4 s",moved:"Ruch po postoju",complete:"Trasa zakończona",
  end:"Koniec zapisu",pause:"Pauza",stop:"Zatrzymanie trasy",death:"Zaobserwowana śmierć celu",contact_lost:"Utrata kontaktu",unreachable:"Cel poza dostępnym obszarem",
  left_group:"Cel opuścił grupę",position_cancelled:"Serwer anulował krok",position_interrupted:"Przerwano oczekiwanie na pozycję",
  position_unconfirmed:"Brak potwierdzenia pozycji"};
const number=(v:number|null)=>v==null?"—":v.toLocaleString("pl-PL",{maximumFractionDigits:2});
const clock=(ms:number)=>Math.floor(ms/60000)+":"+String(Math.floor(ms/1000)%60).padStart(2,"0");
const date=(n:number)=>new Date(n*1000).toLocaleString("pl-PL",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});

function Timeline({events}:{events:Decision[]}){
  const [count,setCount]=useState(40);
  return <div className="lap-timeline">
    {events.slice(0,count).map((event,i)=><div key={i} className="lap-entry">
      <time>{clock(event.ms)}</time><span><strong>{labels[event.kind]??event.kind}</strong>
      {event.detail && <> · {labels[event.detail]??event.detail}</>}
      {event.engaged!=null && <> · grupa {event.engaged}/{event.goal}</>}</span>
      <small>{event.waypoint!=null && <>WP {event.waypoint} · </>}
        {event.position?.join(", ")}{event.target && <> · cel kroku {event.target.join(", ")}</>}</small>
    </div>)}
    {count<events.length && <button className="secondary-button" onClick={()=>setCount(count+80)}>Więcej decyzji</button>}
  </div>;
}

export default function CavebotReports({visible}:{visible:boolean}){
  const [open,setOpen]=useState(false),[index,setIndex]=useState<Index|null>(null);
  const [before,setBefore]=useState<number[]>([]),[id,setId]=useState(""),[detail,setDetail]=useState<Report|null>(null);
  const [error,setError]=useState(""),[detailError,setDetailError]=useState(false);
  const cursor=before.at(-1);
  useEffect(()=>{
    if(!visible || !open)return;
    const abort=new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    const refresh=async()=>{
      try{
        const result=await fetchCavebotReports<Index>(cursor==null?"":"?before="+cursor,abort.signal);
        if(!abort.signal.aborted){setIndex(result);setError("");}
      }catch{if(!abort.signal.aborted)setError("Nie udało się odczytać raportów.");}
      if(!abort.signal.aborted)timer=setTimeout(refresh,2500);
    };
    void refresh();
    return ()=>{abort.abort();clearTimeout(timer);};
  },[visible,open,cursor]);
  useEffect(()=>{
    setDetail(null);setDetailError(false);
    if(!id || !visible || !open)return;
    const abort=new AbortController();
    fetchCavebotReports<Report>("?id="+encodeURIComponent(id),abort.signal)
      .then(r=>{if(!abort.signal.aborted)setDetail(r);})
      .catch(()=>{if(!abort.signal.aborted)setDetailError(true);});
    return ()=>abort.abort();
  },[id,visible,open]);
  function page(next:number[]){setBefore(next);setId("");setIndex(null);}
  return <details className="diagnostic-section lap-reports" onToggle={e=>setOpen(e.currentTarget.open)}>
    <summary>Historia decyzji i raporty okrążeń</summary>
    {open && <><p role="status">{error || (!index?"Wczytywanie…":"")}</p>
      {index?.active && <details><summary>{index.active.route} · bieżące okrążenie {index.active.lap}</summary>
        <Timeline events={index.active.events}/></details>}
      {index?.reports.length===0 && <p>Raport pojawi się po ukończeniu lub przerwaniu trasy.</p>}
      {!!index?.reports.length && <div className="lap-scroll"><table><thead><tr>
        {["Początek","Trasa / profil","Okrążenie","Czas","XP","XP/h","Senzu","Blokady"].map(s=><th key={s}>{s}</th>)}
      </tr></thead><tbody>{index.reports.map(r=><tr key={r.id}><td>{date(r.started_at)}</td><td>{r.route} · {r.profile??"—"}</td>
        <td><button className="secondary-button" aria-expanded={id===r.id} onClick={()=>setId(id===r.id?"":r.id)}>
          {r.lap} · {r.complete?"pełne":"fragment"}</button></td><td>{clock(r.duration_ms)}</td><td>{number(r.xp)}</td>
        <td>{number(r.xp_hour)}</td><td>{number(r.senzu)}</td><td>{number(r.blocked_ms/1000)} s</td></tr>)}</tbody></table></div>}
      {before.length>0 && <button className="secondary-button" onClick={()=>page(before.slice(0,-1))}>Nowsze</button>}
      {index?.more && <button className="secondary-button" onClick={()=>page([...before,index.reports[index.reports.length-1].started_at])}>Starsze</button>}
      {id && (detail && detail.id===id ? <><h3>{detail.route} · okrążenie {detail.lap}</h3>
        <p>{detail.end_reason} · Senzu / 1 mln XP {number(detail.senzu_million)} · Mediana grupy na początku walki {number(detail.group_median)}
        {" · "}Zaobserwowane zgony {number(detail.observed_deaths)}
        {detail.unobserved_ms>0 && <> · Luka w obserwacji {clock(detail.unobserved_ms)}</>}
        {detail.event_count>(detail.events?.length??0) && <> · Widoczne ostatnie {detail.events?.length} z {detail.event_count} decyzji</>}</p>
        <Timeline key={id} events={[...(detail.events??[])].reverse()}/></> :
        <p>{detailError?"Nie udało się odczytać szczegółów.":"Wczytywanie decyzji…"}</p>)}
    </>}
  </details>;
}
