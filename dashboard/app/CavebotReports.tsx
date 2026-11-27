"use client";
import { t, locale, num, type TextKey } from "@/lib/i18n";
import {useEffect, useState} from "react";
import {fetchCavebotReports} from "@/lib/api";

type Decision = {ms:number;kind:string;position:number[]|null;waypoint:number|null;engaged:number|null;
  goal:number|null;target:number[]|null;detail:string|null};
type Report = {id:string;route:string;revision:string|null;profile:string|null;started_at:number;lap:number;complete:boolean;
  duration_ms:number;blocked_ms:number;unobserved_ms:number;xp:number|null;xp_hour:number|null;senzu:number|null;
  senzu_million:number|null;group_median:number|null;observed_deaths:number|null;event_count:number;end_reason:string;events?:Decision[]};
type Index = {active:{route:string;lap:number;events:Decision[]}|null;reports:Report[];more:boolean};
const decisions=new Set(["travel", "waypoint", "blocked", "manual", "group", "corridor", "transition", "no_approach", "timeout", "lure", "fight", "positioning", "return", "chase", "distance", "stationary", "moved", "complete", "end", "pause", "stop", "death", "contact_lost", "unreachable", "first_contact", "left_group", "position_cancelled", "position_interrupted", "position_unconfirmed"]);
const label=(kind:string)=>decisions.has(kind)?t(`reports.kind.${kind}` as TextKey):kind;
const number=(v:number|null)=>v==null?"—":num(v,{maximumFractionDigits:2});
const clock=(ms:number)=>Math.floor(ms/60000)+":"+String(Math.floor(ms/1000)%60).padStart(2,"0");
const date=(n:number)=>new Date(n*1000).toLocaleString(locale(),{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});

function Timeline({events}:{events:Decision[]}){
  const [count,setCount]=useState(40);
  return <div className="lap-timeline">
    {events.slice(0,count).map((event,i)=><div key={i} className="lap-entry">
      <time>{clock(event.ms)}</time><span><strong>{label(event.kind)}</strong>
      {event.detail && <> · {label(event.detail)}</>}
      {event.engaged!=null && <> · {t("reports.group")} {event.engaged}/{event.goal}</>}</span>
      <small>{event.waypoint!=null && <>WP {event.waypoint} · </>}
        {event.position?.join(", ")}{event.target && <> · {t("reports.stepTarget")} {event.target.join(", ")}</>}</small>
    </div>)}
    {count<events.length && <button className="secondary-button" onClick={()=>setCount(count+80)}>{t("reports.more")}</button>}
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
      }catch{if(!abort.signal.aborted)setError(t("reports.readFailed"));}
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
    <summary>{t("reports.title")}</summary>
    {open && <><p role="status">{error || (!index?t("common.loading"):"")}</p>
      {index?.active && <details><summary>{index.active.route} · {t("reports.currentLap",{lap:index.active.lap})}</summary>
        <Timeline events={index.active.events}/></details>}
      {index?.reports.length===0 && <p>{t("reports.empty")}</p>}
      {!!index?.reports.length && <div className="lap-scroll"><table><thead><tr>
        {(["reports.col.start","reports.col.route","reports.col.lap","reports.col.time","reports.col.xp","reports.col.xph","reports.col.senzu","reports.col.blocks"] as const).map(s=><th key={s}>{t(s)}</th>)}
      </tr></thead><tbody>{index.reports.map(r=><tr key={r.id}><td>{date(r.started_at)}</td><td>{r.route} · {r.profile??"—"}</td>
        <td><button className="secondary-button" aria-expanded={id===r.id} onClick={()=>setId(id===r.id?"":r.id)}>
          {r.lap} · {t(r.complete?"reports.full":"reports.part")}</button></td><td>{clock(r.duration_ms)}</td><td>{number(r.xp)}</td>
        <td>{number(r.xp_hour)}</td><td>{number(r.senzu)}</td><td>{number(r.blocked_ms/1000)} s</td></tr>)}</tbody></table></div>}
      {before.length>0 && <button className="secondary-button" onClick={()=>page(before.slice(0,-1))}>{t("reports.newer")}</button>}
      {index?.more && <button className="secondary-button" onClick={()=>page([...before,index.reports[index.reports.length-1].started_at])}>{t("reports.older")}</button>}
      {id && (detail && detail.id===id ? <><h3>{detail.route} · {t("reports.lap",{lap:detail.lap})}</h3>
        <p>{detail.end_reason} · {t("reports.senzuMillion")} {number(detail.senzu_million)} · {t("reports.groupMedian")} {number(detail.group_median)}
        {" · "}{t("reports.deaths")} {number(detail.observed_deaths)}
        {detail.unobserved_ms>0 && <> · {t("reports.gap")} {clock(detail.unobserved_ms)}</>}
        {detail.event_count>(detail.events?.length??0) && <> · {t("reports.visible",{shown:detail.events?.length??0,total:detail.event_count})}</>}</p>
        <Timeline key={id} events={[...(detail.events??[])].reverse()}/></> :
        <p>{t(detailError?"reports.detailFailed":"reports.loadingDecisions")}</p>)}
    </>}
  </details>;
}
