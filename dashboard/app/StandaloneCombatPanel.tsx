"use client";
import { t } from "@/lib/i18n";
import Help from "@/app/Help";
import {useFold} from "@/app/Fold";
import {useEffect,useRef,useState} from "react";
import {toggleAction,updateActionConfig,type BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
import {defaultStandaloneCombat,parseTargetPreview,type CombatConfig,type TargetPreview} from "@/lib/cavebotModel";
import CombatEditor from "./CombatEditor";
import {SpellDiagnostics} from "./SpellConditions";

/** Walka: combat without a route (actions/auto_combat.py), with settings of its own. The cavebot comes first. */
export default function StandaloneCombatPanel({state,onRefresh}:{state:BotState|null;onRefresh:()=>void}) {
  const action=state?.actions.find(a=>a.name==="auto_combat");
  const saved=JSON.stringify({...defaultStandaloneCombat(),...(action?.config?.combat??{})});
  const [combat,setCombat]=useState<CombatConfig>(()=>JSON.parse(saved));
  const [dirty,setDirty]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const [preview,setPreview]=useState<TargetPreview|null>(null);
  const fold=useFold(t("combatPanel.aria"));
  const edited=useRef(dirty);edited.current=dirty;
  useEffect(()=>{if(!edited.current)setCombat(JSON.parse(saved));},[saved]);
  if(!action)return null;
  const live=state?.standalone_combat,on=action.enabled||action.running;
  const held=live?.combat?.held;
  const status=!on?t("combatPanel.off"):!state?.connected?t("action.waitingForGame"):live?.status==="yielding"?t("combatPanel.yielding"):live?.status==="fighting"?t("combatPanel.fighting"):live?.reason||t("automation.ready");
  function change(next:CombatConfig){setCombat(next);setDirty(true);setPreview(null);}
  async function run(work:()=>Promise<unknown>){setBusy(true);setError("");try{await work();await onRefresh();}catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  const save=()=>run(async()=>{await updateActionConfig("auto_combat",{combat:{...combat,enabled:true}});setDirty(false);edited.current=false;});
  const inspect=()=>run(async()=>{const r=await authenticatedFetch("http://127.0.0.1:8089/api/adaptive/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({combat})});const d=await r.json();if(!r.ok)throw new Error(d.error??`HTTP ${r.status}`);setPreview(parseTargetPreview(d));});
  return <section className="speed-up-panel standalone-combat" aria-label={t("combatPanel.aria")}>
    <div className="speed-heading fold-heading" onClick={fold.onHeading}>
      {fold.arrow}<span className="combat-icon" aria-hidden="true">⚔</span>
      <strong>{t("action.title.auto_combat")}<Help text={t("combatPanel.help")}/></strong>
      <span className={live?.status==="yielding"&&on?"combat-yielding":undefined}>{status}{on&&held?.lost?" · "+t("combatPanel.holding",{name:held.name}):""}</span>
      <button className={`action-switch ${on?"on":""}`} role="switch" aria-checked={on} aria-label={t("combatPanel.aria")}
        disabled={busy||dirty||!!state?.automation_paused} title={dirty?t("combatPanel.saveFirst"):undefined} onClick={()=>void run(()=>toggleAction("auto_combat",!on))}><span/></button>
    </div>
    <div className="v2-config standalone-combat-editor" hidden={!fold.open}>
      <fieldset disabled={busy} aria-label={t("combatPanel.aria")}><CombatEditor combat={combat} onChange={change} disabled={busy} movement={false}/></fieldset>
      <div className="v2-savebar"><button disabled={!dirty||busy} onClick={save}>{t("common.save")}</button>
        <button disabled={!dirty||busy} onClick={()=>{setCombat(JSON.parse(saved));setDirty(false);edited.current=false;setPreview(null);setError("");}}>{t("workspace.discardChanges")}</button>
        <button disabled={busy||!state?.connected} onClick={inspect}>{t("workspace.checkTargets")}</button></div>
      {on&&live?.status==="fighting"&&!!live.combat?.spells?.length&&<SpellDiagnostics rows={live.combat.spells}/>}
      {preview?.spells&&<SpellDiagnostics rows={preview.spells} passive/>}
      {preview&&<div className="v2-targets"><strong>{t("workspace.targets")}</strong>{preview.targets.map(target=><p key={target.id}>{preview.selected?.id===target.id?'◎ ':''}{target.name} · {target.health}% HP · {t("workspace.targetLine",{distance:target.distance,priority:target.priority})}</p>)}{!preview.targets.length&&<p>{t("workspace.noTargets")}</p>}</div>}
    </div>
    {error&&<p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
