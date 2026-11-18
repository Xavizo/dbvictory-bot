"use client";

import {useEffect,useRef,useState} from "react";
import type {BotState} from "@/lib/api";
import {RouteReplay} from "@/lib/routeReplay";

export default function RouteReplayPanel({state,online}:{state:BotState|null;online:boolean}) {
  const replay=useRef<RouteReplay|null>(null);
  const [mode,setMode]=useState<"idle"|"recording"|"finished">("idle");
  const [count,setCount]=useState(0);
  useEffect(()=>{
    const run=replay.current;
    if(mode!=="recording"||!run)return;
    if(run.observe(state,online)){
      setCount(run.frames.length);
      if(run.full)setMode("finished");
    }
  },[state,online,mode]);
  function start(){
    replay.current=new RouteReplay();
    setCount(0);
    setMode("recording");
  }
  function finish(){
    replay.current?.finish();
    setMode("finished");
  }
  function save(){
    const run=replay.current;
    if(!run)return;
    const url=URL.createObjectURL(new Blob([run.serialize()],{type:"application/json"}));
    const a=document.createElement("a");
    a.href=url;
    a.download="obieg-"+new Date(run.startedAt).toISOString().replace(/[:.]/g,"-")+".json";
    a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <div className="diagnostic-section">
    <div className="section-caption"><h2>Zapis obiegu</h2>
      {mode!=="idle" && <span>{replay.current?.full?"Osiągnięto limit zapisu · ":""}{count} próbek</span>}</div>
    {mode==="idle"&&<button className="secondary-button" onClick={start}>Nagraj obieg</button>}
    {mode==="recording"&&<button className="secondary-button" onClick={finish}>Zakończ nagranie</button>}
    {mode==="finished"&&<div className="action-toolbar">
      <button className="secondary-button" onClick={save}>Zapisz JSON</button>
      <button className="secondary-button" onClick={()=>{replay.current=null;setMode("idle");}}>Odrzuć nagranie</button>
    </div>}
  </div>;
}
