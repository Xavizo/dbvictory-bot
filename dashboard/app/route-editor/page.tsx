"use client";
import { t } from "@/lib/i18n";
import {useEffect,useState} from "react";
import {useBot} from "@/lib/useBot";
import AtlasPanel from "../AtlasPanel";
import CavebotRunBar from "../CavebotRunBar";
export default function RouteEditor(){
  const {state,refresh,dashboardError,mcpConnected}=useBot();const [draft,setDraft]=useState("");
  useEffect(()=>{setDraft(new URLSearchParams(window.location.search).get("draft")??"");},[]);
  useEffect(()=>{document.title="DBVictory — "+t("routeEditor.title");});
  return <main className="route-editor-window"><header><div><strong>{t("routeEditor.title")}</strong><small>{t("routeEditor.subtitle")}</small></div><span>{t("routeEditor.hint")}</span></header>
    <CavebotRunBar state={state} connected={mcpConnected} onRefresh={refresh}/>{dashboardError&&<p role="alert">{dashboardError}</p>}
    <AtlasPanel state={state} active={true} onRefresh={refresh} initialDraftId={draft}/>
  </main>;
}
