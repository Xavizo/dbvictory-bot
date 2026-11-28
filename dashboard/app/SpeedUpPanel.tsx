"use client";
import { t } from "@/lib/i18n";
import {useEffect,useState} from "react";
import {useFold} from "@/app/Fold";
import {toggleAction,updateActionConfig,type BotState} from "@/lib/api";
import {LocalSpellIcon} from "./SpellPicker";

export default function SpeedUpPanel({state,onRefresh}:{state:BotState|null;onRefresh:()=>void}) {
  const action=state?.actions.find(a=>a.name==="speed_up"),cfg=action?.config;
  const saved={duration:Number(cfg?.duration??33.3),before:Number(cfg?.before??0),after:Number(cfg?.after??5)};
  const serial=JSON.stringify(saved);
  const [draft,setDraft]=useState(saved),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>setDraft(JSON.parse(serial)),[serial]);
  const fold=useFold("Speed Up");
  if(!action)return null;
  const dirty=JSON.stringify(draft)!==serial;
  async function change(save:boolean){setBusy(true);setError("");try{
    if(save)await updateActionConfig("speed_up",draft);else await toggleAction("speed_up",!action?.enabled);
    await onRefresh();
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="speed-up-panel" aria-label={t("speedUp.aria")}>
    <div className="speed-heading fold-heading" onClick={fold.onHeading}>{fold.arrow}<LocalSpellIcon src="/local-spells/icons/11713.png" size={28}/><strong>Speed Up</strong><span>{action.enabled?(state?.speed_up?.status??t("speedUp.waiting")):t("barrierSpell.off")}</span><button className={`action-switch ${action.enabled?"on":""}`} role="switch" aria-checked={action.enabled} aria-label={t("speedUp.aria")} disabled={busy||dirty||!!state?.automation_paused} onClick={()=>void change(false)}><span/></button></div>
    <div className="speed-times" hidden={!fold.open}>{([{key:"duration",label:"speedUp.duration"},{key:"before",label:"speedUp.before"},{key:"after",label:"speedUp.after"}] as const).map(f=><label key={f.key}>{t(f.label)}<input aria-label={t(f.label)} type="number" min={f.key==="duration"?2:0} max="3600" step="0.1" value={draft[f.key]} disabled={busy||action.enabled} onChange={e=>setDraft({...draft,[f.key]:Number(e.target.value)})}/></label>)}{dirty&&<button className="secondary-button" disabled={busy||action.enabled} onClick={()=>void change(true)}>{t("common.save")}</button>}</div>
    {error&&<p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
