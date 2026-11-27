"use client";
import { t } from "@/lib/i18n";
import {useState} from "react";
import {toggleAction,updateActionConfig,type BotState} from "@/lib/api";

export default function BarrierBandPanel({state,onRefresh}:{state:BotState|null;onRefresh:()=>void}) {
  const [busy,setBusy]=useState(false),[error,setError]=useState("");
  const action=state?.actions.find(a=>a.name==="auto_barrier_band");
  if(!action)return null;
  const enabled=action.enabled,slot=String(action.config?.slot??"band");
  async function change(next?:string){setBusy(true);setError("");try {
    if(next)await updateActionConfig("auto_barrier_band",{slot:next});
    else await toggleAction("auto_barrier_band",!enabled);
    await onRefresh();
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="barrier-band-panel" aria-label={t("band.aria")}>
    <div><strong>Barrier Band</strong><small>3051 → 3088 · {t("band.refill")}</small></div>
    <label>{t("band.slot")} <select aria-label={t("band.slotAria")} value={slot} disabled={busy||enabled} onChange={e=>change(e.target.value)}>
      <option value="band">{t("band.slotBand")}</option><option value="right_hand">{t("band.rightHand")}</option><option value="left_hand">{t("band.leftHand")}</option>
    </select></label>
    <span className="barrier-band-status">{enabled?(state?.barrier_band?.status??t("speedUp.waiting")):t("barrierSpell.off")}</span>
    <button className={`action-switch ${enabled?"on":""}`} role="switch" aria-checked={enabled} aria-label={t("band.switch")} disabled={busy||!!state?.automation_paused} onClick={()=>change()}><span/></button>
    <small className="barrier-band-help">{t("band.help")}</small>
    {error&&<p role="alert">{error}</p>}
  </section>;
}
