"use client";
import {useEffect,useState} from "react";
import {movementHistory,type MovementHistoryStatus} from "@/lib/api";

export default function MovementHistoryPanel({online}:{online:boolean}) {
  const [report,setReport]=useState<MovementHistoryStatus|null>(null);
  const [error,setError]=useState(""),[readError,setReadError]=useState(""),[saving,setSaving]=useState(false);
  useEffect(()=>{
    if(!online || saving)return;
    const controller=new AbortController();
    let timer:ReturnType<typeof setTimeout>;
    async function refresh(){
      try{
        const data=await movementHistory(false,controller.signal);
        if(!controller.signal.aborted){setReport(data);setReadError("");}
      }catch(e){if(!controller.signal.aborted)setReadError((e as Error).message);}
      if(!controller.signal.aborted)timer=setTimeout(refresh,5000);
    }
    void refresh();
    return ()=>{controller.abort();clearTimeout(timer);};
  },[online,saving]);
  async function save(){
    setSaving(true);setError("");
    try{setReport(await movementHistory(true));}catch(e){setError((e as Error).message);}
    finally{setSaving(false);}
  }
  return <div className="diagnostic-section">
    <div className="section-caption"><h2>Blokady ruchu</h2>
      <span>{report?.capturing?"Trwa zapis blokady":online&&report?.active?"Historia 5 min":"Brak sesji"}</span></div>
    <button className="secondary-button" disabled={!online||!report?.available||saving} onClick={save}>
      {saving?"Zapisywanie…":"Zapisz ostatnią blokadę"}</button>
    {report?.latest&&<p className="break-all" role="status">Ostatni raport · {report.latest}</p>}
    {(error||readError||report?.error)&&<p role="alert">{error||readError||report?.error}</p>}
    {!!report?.unwritten_reports&&<p role="alert">Niezapisane raporty po przepełnieniu kolejki · {report.unwritten_reports}</p>}
  </div>;
}
