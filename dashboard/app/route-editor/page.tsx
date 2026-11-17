"use client";
import {useEffect,useState} from "react";
import {useBot} from "@/lib/useBot";
import AtlasPanel from "../AtlasPanel";
import CavebotRunBar from "../CavebotRunBar";
export default function RouteEditor(){
  const {state,refresh,dashboardError,mcpConnected}=useBot();const [draft,setDraft]=useState("");
  useEffect(()=>{setDraft(new URLSearchParams(window.location.search).get("draft")??"");document.title="DBVictory — Edytor trasy";},[]);
  return <main className="route-editor-window"><header><div><strong>Edytor trasy</strong><small>Rysowanie · piętra · symulacja</small></div><span>Zapisz szkic, aby pojawił się w głównym oknie.</span></header>
    <CavebotRunBar state={state} connected={mcpConnected} onRefresh={refresh}/>{dashboardError&&<p role="alert">{dashboardError}</p>}
    <AtlasPanel state={state} active={true} onRefresh={refresh} initialDraftId={draft}/>
  </main>;
}
