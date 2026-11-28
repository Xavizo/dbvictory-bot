"use client";
import { t, locale } from "@/lib/i18n";
import {useEffect,useRef,useState} from "react";
import type {BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
import {defaultCombat,visibleWaypointProgress,usesRoute,v2StartRequest,parseTargetPreview,type TargetPreview,type CombatConfig,type RouteDraft} from "@/lib/cavebotModel";
import AtlasPanel from "./AtlasPanel";
import ProfilePicker from "./ProfilePicker";
import ActivationCheckbox from "./ActivationCheckbox";
import CombatEditor from "./CombatEditor";
import Help from "@/app/Help";
import {SpellDiagnostics} from './SpellConditions';
async function request(path:string,body?:unknown){const r=await authenticatedFetch(`http://127.0.0.1:8089${path}`,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:undefined);const d=await r.json();if(!r.ok)throw Object.assign(new Error(d.error??`HTTP ${r.status}`),{status:r.status});return d;}
export default function CavebotWorkspace({state,active,onRefresh}:{state:BotState|null;active:boolean;onRefresh:()=>void}){
  const [movement,setMovement]=useState(true);
  const [pendingStart,setPendingStart]=useState(false);
  const [routes,setRoutes]=useState<RouteDraft[]>([]),[trash,setTrash]=useState<RouteDraft[]>([]),[scope,setScope]=useState("");
  const [id,setId]=useState(""),[combat,setCombat]=useState<CombatConfig>(defaultCombat),[loop,setLoop]=useState(true),[dirty,setDirty]=useState(false);
  const [loadError,setLoadError]=useState("");
  const [busy,setBusy]=useState(false),[error,setError]=useState(""),[notice,setNotice]=useState(""),[deleting,setDeleting]=useState(false);
  const [preview,setPreview]=useState<TargetPreview|null>(null);
  const [selectionVersion,setSelectionVersion]=useState(0),[sketchDirty,setSketchDirty]=useState(false);
  const selected=useRef(id);selected.current=id;const edited=useRef(dirty);edited.current=dirty;
  const route=routes.find(r=>r.id===id),run=state?.adaptive_cavebot,executing=["running","paused","ready"].includes(run?.status??"");
  const moveOnRoute=usesRoute(movement,route),hasRoute=usesRoute(true,route);
  const resumable=moveOnRoute&&run?.resumable&&!!visibleWaypointProgress(run,id,route?.updated,sketchDirty,route?.navigation_revision)&&run?.loop===loop;
  const preparing=pendingStart||run?.preparing;
  const globalCombat=JSON.stringify(state?.combat_profile??defaultCombat());
  useEffect(()=>{if(!edited.current)setCombat(JSON.parse(globalCombat));},[globalCombat]);
  let startError="";try{v2StartRequest(movement,route,combat,loop,scope);}catch(e){startError=(e as Error).message;}
  const lastRoute=useRef({id:"",shape:""});
  useEffect(()=>{const shape=route?JSON.stringify([route.points,route.labels,route.links,route.loop,route.name,route.updated]):"";
    if(!sketchDirty&&lastRoute.current.id===id&&lastRoute.current.shape&&shape!==lastRoute.current.shape)setSelectionVersion(v=>v+1);
    lastRoute.current={id,shape};
  },[route,id,sketchDirty]);
  async function refresh(){try{const d=await request("/api/map/drafts");setRoutes(d.drafts);setTrash(d.trash??[]);setScope(d.scope);setLoadError("");
    if(!edited.current){const row=d.drafts.find((r:RouteDraft)=>r.id===selected.current);if(row){setLoop(row.loop);}else if(selected.current){setId("");}}
  }catch(e){setLoadError((e as {status?:number}).status===503?"":String(e));}}
  useEffect(()=>{if(!active)return;void refresh();const timer=setInterval(()=>void refresh(),2500);const focus=()=>void refresh();window.addEventListener("focus",focus);return()=>{clearInterval(timer);window.removeEventListener("focus",focus);};},[active]);
  async function choose(next:string){
    setError('');setBusy(true);
    try{
      const binding=state?.profile_selection?.routes?.[`${scope}:${next}`];
      if(binding&&!edited.current){const loaded=await request('/api/profiles',{command:'apply',kind:'combat',id:binding});setCombat(loaded.message.config);await onRefresh();}
      setId(next);const row=routes.find(r=>r.id===next);setLoop(row?.loop??true);setDeleting(false);setPreview(null);setNotice(binding&&edited.current?t('workspace.keptCombat'):'');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  function change(next:CombatConfig){setCombat(next);setDirty(true);setPreview(null);}
  async function command(action:string){if(action==="start"||action==="resume")setPendingStart(true);setBusy(true);setError("");try{await request("/api/adaptive/control",action==="start"?v2StartRequest(movement,route,combat,loop,scope):{command:action});await onRefresh();}catch(e){setError(String(e));}finally{setBusy(false);if(action==="start"||action==="resume")setPendingStart(false);}}
  async function save(){setBusy(true);setError("");try{await request("/api/adaptive/control",{command:"configure_combat",combat});setDirty(false);edited.current=false;setNotice(t("workspace.saved"));await refresh();await onRefresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function removeOrRestore(target:string,operation:string){setBusy(true);setError("");try{await request("/api/map/drafts",{scope,id:target,operation});if(operation==="delete"){choose("");setNotice(t("workspace.trashed"));}else{setNotice(t("workspace.restored"));}await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function openEditor(draftId:string){setError("");try{if(!window.dbvEditor)throw new Error(t("workspace.editorNeedsPanel"));const opened=await window.dbvEditor.open(draftId);if(!opened)setNotice(t("workspace.editorOpen"));}catch(e){setError(String(e));}}
  async function exportRoute(){if(!route)return;setBusy(true);setError("");setNotice("");try{
    if(!window.dbvRoutes)throw new Error(t("workspace.exportNeedsPanel"));
    const d=await request(`/api/map/drafts/export?id=${encodeURIComponent(route.id)}`);
    const saved=await window.dbvRoutes.save(d.file_name,JSON.stringify(d.pack));
    if(saved)setNotice(t("workspace.exported",{file:saved})+(d.pack.complete?"":" "+t("workspace.exportIncomplete")));
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function importRoute(){setBusy(true);setError("");setNotice("");try{
    if(!window.dbvRoutes)throw new Error(t("workspace.importNeedsPanel"));
    const text=await window.dbvRoutes.open();if(!text)return;
    const d=await request("/api/map/drafts/import",{scope,pack:JSON.parse(text)});
    setRoutes(d.drafts);setTrash(d.trash??[]);
    const added=d.drafts.find((r:RouteDraft)=>r.id===d.imported?.[0]);
    if(added){setId(added.id);selected.current=added.id;setLoop(added.loop);setDeleting(false);setPreview(null);setSelectionVersion(v=>v+1);
      setNotice(t("workspace.imported",{name:added.name}));}
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function inspectTargets(){setBusy(true);setError("");try{setPreview(parseTargetPreview(await request("/api/adaptive/preview",{combat})));}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="v2-config">
    <header className="v2-config-header"><h2>{t("nav.cavebot")}</h2><small>{t(moveOnRoute?(combat.enabled?"workspace.routeAndCombat":"workspace.routeOnly"):(combat.enabled?"workspace.combatOnly":"workspace.chooseMode"))}</small></header>
    <div className="v2-execution"><span className={`v2-state-dot ${run?.status==="running"?"on":""}`}/><strong>{t(run?.status==="running"?"workspace.running":run?.status==="paused"?"workspace.paused":run?.status==="complete"?"workspace.complete":"workspace.stopped")}</strong><span>{run?.reason}</span>
      <button className="v2-start" title={startError||(moveOnRoute&&(dirty||sketchDirty)?t("workspace.saveFirst"):undefined)} disabled={(moveOnRoute&&(dirty||sketchDirty))||busy||executing||!state?.connected||state?.automation_paused||!!state?.cavebot?.recording.active||!!startError} onClick={()=>command("start")}>{preparing?t("workspace.preparing"):resumable?"▶ "+t("workspace.resumeRoute"):"▶ "+t("workspace.start")}</button>
      {run?.status==="paused"?<button disabled={busy||state?.automation_paused} onClick={()=>command("resume")}>{t("common.resume")}</button>:<button disabled={busy||run?.status!=="running"} onClick={()=>command("pause")}>{t("common.pause")}</button>}
      <button className="v2-stop" disabled={!preparing&&(busy||!executing)} onClick={()=>command("stop")}>■ {t("workspace.stop")}</button>
      {run?.resumable&&run.status!=="running"&&<button disabled={busy||preparing} title={t("workspace.discardTitle")} onClick={()=>command("discard_progress")}>{t("run.discard")}</button>}</div>
    {(error||loadError)&&<p role="alert" className="v2-error">{error||loadError}</p>}{notice&&<p role="status" className="v2-inline-note">{notice}</p>}
    <div className="v2-config-columns">
    <section className="v2-waypoints">
    {sketchDirty&&!state?.cavebot?.recording.active&&<div className="v2-inline-note" role="status">{t("workspace.unsavedSketch")} <button disabled={busy} onClick={()=>{setSelectionVersion(v=>v+1);setSketchDirty(false);}}>{t("workspace.discardSketch")}</button></div>}
    {deleting&&route&&<div className="v2-inline-note">{t("workspace.deleteAsk",{name:route.name})} <button onClick={()=>removeOrRestore(id,"delete")} disabled={busy}>{t("common.delete")}</button><button onClick={()=>setDeleting(false)}>{t("common.cancel")}</button></div>}
      <AtlasPanel state={state} active={active} profileScope={scope} onRefresh={onRefresh} initialDraftId={id} selectionVersion={selectionVersion} embedded={true} onEditingChange={setSketchDirty} onLoopChange={setLoop} onDraftSaved={async saved=>{setSketchDirty(false);setId(saved);selected.current=saved;await refresh();}}
        routeEnabledControl={<ActivationCheckbox label={t("workspace.movement")} title={t(movement&&!hasRoute?"workspace.noWaypoints":"workspace.movementTitle")} checked={movement} disabled={busy||executing} onChange={setMovement}/>}
        routePicker={<div className="v2-routebar"><select aria-label={t("workspace.route")} value={id} disabled={busy||sketchDirty} onChange={e=>{choose(e.target.value);setSelectionVersion(v=>v+1);}}><option value="">{t(scope?"workspace.chooseSketch":"workspace.afterLogin")}</option>{routes.map(r=><option key={r.id} value={r.id}>{r.name} · {t("workspace.points",{n:r.points.length})}{r.tags?.map(tag=>` · ${tag.text}`).join("")}</option>)}</select></div>}
        moreActions={<><button disabled={busy||sketchDirty} onClick={()=>{choose("");setSelectionVersion(v=>v+1);}}>{t("workspace.newSketch")}</button><button disabled={busy||sketchDirty} onClick={()=>openEditor(id)}>{t("workspace.openWindow")}</button><button disabled={!route||busy||sketchDirty} title={t("workspace.exportTitle")} onClick={exportRoute}>{t("workspace.export")}</button><button disabled={busy||sketchDirty||!scope} title={t("workspace.importTitle")} onClick={importRoute}>{t("workspace.import")}</button><button disabled={!route||busy||executing} onClick={()=>setDeleting(!deleting)}>{t("workspace.deleteSketch")}</button>{sketchDirty&&!state?.cavebot?.recording.active&&<button onClick={()=>{setSelectionVersion(v=>v+1);setSketchDirty(false);}}>{t("workspace.discardUnsaved")}</button>}</>}/>
    </section>
      <section className="v2-combat"><header className="combat-section-header"><ActivationCheckbox label={t("workspace.combat")} checked={combat.enabled} disabled={busy||executing} onChange={enabled=>change({...combat,enabled})}/><ProfilePicker kind="combat" state={state} config={combat} routeId={id} scope={scope} legacy={route?.combat} onImport={value=>change(value as CombatConfig)} disabled={busy||executing} dirty={dirty} onRefresh={onRefresh} onApplied={value=>{setCombat(value as CombatConfig);setDirty(false);edited.current=false;setPreview(null);}}/></header>
      {dirty&&<p className="combat-unsaved" role="status">{t("workspace.unsavedCombatShort")}<Help text={t("workspace.unsavedCombat")}/></p>}
      <fieldset disabled={busy||executing} aria-label={t("workspace.combatSetup")}>
        <CombatEditor combat={combat} onChange={change} disabled={busy||executing} movement={moveOnRoute}/>
      </fieldset><div className="v2-savebar"><button disabled={!dirty||busy||executing} onClick={save}>{t("workspace.saveCombat")}</button><button disabled={!dirty||busy} onClick={()=>{setCombat(JSON.parse(globalCombat));setDirty(false);edited.current=false;setPreview(null);setError("");}}>{t("workspace.discardChanges")}</button><button disabled={busy||!state?.connected} onClick={inspectTargets}>{t("workspace.checkTargets")}</button></div>
      {executing&&!!run?.combat?.spells?.length&&<details className="v2-options" open><summary>{t("workspace.spellConditions")}</summary><SpellDiagnostics rows={run.combat.spells}/>{run.combat.last_spell_attempt&&<small className="spell-attempt-note">{t("workspace.lastAttempt")}: {run.combat.last_spell_attempt.words} · {new Date(run.combat.last_spell_attempt.at*1000).toLocaleTimeString(locale())} · {t("workspace.unconfirmed")}</small>}</details>}
      {preview?.spells&&<SpellDiagnostics rows={preview.spells} passive/>}
      {preview&&<div className="v2-targets"><strong>{t("workspace.targets")}</strong><small>{preview.message}</small>{preview.targets.map(target=><p key={target.id}>{preview.selected?.id===target.id?'◎ ':''}{target.name} · {target.health}% HP · {t("workspace.targetLine",{distance:target.distance,priority:target.priority})}</p>)}{!preview.targets.length&&<p>{t("workspace.noTargets")}</p>}</div>}
      </section></div>
    {!!trash.length&&<details className="v2-trash"><summary>{t("workspace.trash",{n:trash.length})}</summary>{trash.map(r=><div key={r.id}><span>{r.name}</span><button disabled={busy} onClick={()=>removeOrRestore(r.id,"restore")}>{t("workspace.restore")}</button></div>)}</details>}

  </div>;
}
