"use client";
import {useEffect,useRef,useState} from "react";
import type {BotState} from "@/lib/api";
import {authenticatedFetch} from "@/lib/auth";
import {defaultCombat,defaultCombatSpell,visibleWaypointProgress,moveRule,usesRoute,v2StartRequest,parseTargetPreview,type TargetPreview,type CombatConfig,type RouteDraft} from "@/lib/cavebotModel";
import AtlasPanel from "./AtlasPanel";
import ProfilePicker from "./ProfilePicker";
import ActivationCheckbox from "./ActivationCheckbox";
import MonsterRules from "./MonsterRules";
import SpellPicker,{SpellCooldown} from './SpellPicker';
import {LureSettings} from './LureStatus';
import SpellConditions,{SpellDiagnostics} from './SpellConditions';
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
  const hasLure=moveOnRoute&&(combat.mode==='lure'||combat.rules.some(r=>r.action==='attack'&&r.stance==='lure'));
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
      setId(next);const row=routes.find(r=>r.id===next);setLoop(row?.loop??true);setDeleting(false);setPreview(null);setNotice(binding&&edited.current?'Zachowano niezapisane ustawienia walki; profil przypisany do nowej trasy nie został wczytany.':'');
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  function change(next:CombatConfig){setCombat(next);setDirty(true);setPreview(null);}
  async function command(action:string){if(action==="start"||action==="resume")setPendingStart(true);setBusy(true);setError("");try{await request("/api/adaptive/control",action==="start"?v2StartRequest(movement,route,combat,loop,scope):{command:action});await onRefresh();}catch(e){setError(String(e));}finally{setBusy(false);if(action==="start"||action==="resume")setPendingStart(false);}}
  async function save(){setBusy(true);setError("");try{await request("/api/adaptive/control",{command:"configure_combat",combat});setDirty(false);edited.current=false;setNotice("Zapisano konfigurację.");await refresh();await onRefresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function removeOrRestore(target:string,operation:string){setBusy(true);setError("");try{await request("/api/map/drafts",{scope,id:target,operation});if(operation==="delete"){choose("");setNotice("Szkic przeniesiony do usuniętych. Możesz go przywrócić.");}else{setNotice("Szkic przywrócony — jest ponownie na liście tras.");}await refresh();}catch(e){setError(String(e));}finally{setBusy(false);}}
  async function openEditor(draftId:string){setError("");try{if(!window.dbvEditor)throw new Error("Edytor wymaga panelu Electron.");const opened=await window.dbvEditor.open(draftId);if(!opened)setNotice("Edytor jest już otwarty — przełączono do niego. Zapisz lub zamknij go przed edycją innego szkicu.");}catch(e){setError(String(e));}}
  async function inspectTargets(){setBusy(true);setError("");try{setPreview(parseTargetPreview(await request("/api/adaptive/preview",{combat})));}catch(e){setError(String(e));}finally{setBusy(false);}}
  return <div className="v2-config">
    <header className="v2-config-header"><h2>Cavebot</h2><small>{moveOnRoute?(combat.enabled?"Trasa i walka":"Tylko trasa"):(combat.enabled?"Tylko walka · bez ruchu":"Wybierz waypointy lub walkę")}</small></header>
    <div className="v2-execution"><span className={`v2-state-dot ${run?.status==="running"?"on":""}`}/><strong>{run?.status==="running"?"Uruchomiony":run?.status==="paused"?"Wstrzymany":run?.status==="complete"?"Zakończony":"Zatrzymany"}</strong><span>{run?.reason}</span>
      <button className="v2-start" title={startError||(moveOnRoute&&(dirty||sketchDirty)?"Zapisz konfigurację i szkic przed uruchomieniem trasy.":undefined)} disabled={(moveOnRoute&&(dirty||sketchDirty))||busy||executing||!state?.connected||state?.automation_paused||!!state?.cavebot?.recording.active||!!startError} onClick={()=>command("start")}>{preparing?"Przygotowanie…":resumable?"▶ Wznów trasę":"▶ Uruchom"}</button>
      {run?.status==="paused"?<button disabled={busy||state?.automation_paused} onClick={()=>command("resume")}>Wznów</button>:<button disabled={busy||run?.status!=="running"} onClick={()=>command("pause")}>Pauza</button>}
      <button className="v2-stop" disabled={!preparing&&(busy||!executing)} onClick={()=>command("stop")}>■ Stop</button>
      {run?.resumable&&run.status!=="running"&&<button disabled={busy||preparing} title="Czyści tylko zapamiętany postęp. Nie usuwa szkicu ani ustawień walki." onClick={()=>command("discard_progress")}>Odrzuć postęp</button>}</div>
    {(error||loadError)&&<p role="alert" className="v2-error">{error||loadError}</p>}{notice&&<p role="status" className="v2-inline-note">{notice}</p>}
    <div className="v2-config-columns">
    <section className="v2-waypoints">
    {sketchDirty&&!state?.cavebot?.recording.active&&<div className="v2-inline-note" role="status">Niezapisany szkic <button disabled={busy} onClick={()=>{setSelectionVersion(v=>v+1);setSketchDirty(false);}}>Odrzuć szkic</button></div>}
    {deleting&&route&&<div className="v2-inline-note">Usunąć „{route.name}”? <button onClick={()=>removeOrRestore(id,"delete")} disabled={busy}>Usuń</button><button onClick={()=>setDeleting(false)}>Anuluj</button></div>}
      <AtlasPanel state={state} active={active} profileScope={scope} onRefresh={onRefresh} initialDraftId={id} selectionVersion={selectionVersion} embedded={true} onEditingChange={setSketchDirty} onLoopChange={setLoop} onDraftSaved={async saved=>{setSketchDirty(false);setId(saved);selected.current=saved;await refresh();}}
        routeEnabledControl={<ActivationCheckbox label="Aktywuj ruch" title={movement&&!hasRoute?"Brak waypointów — bot będzie działać bez ruchu.":"Poruszaj się po trasie. Wyłącz, aby uruchomić samą walkę."} checked={movement} disabled={busy||executing} onChange={setMovement}/>}
        routePicker={<div className="v2-routebar"><select aria-label="Wybrana trasa" value={id} disabled={busy||sketchDirty} onChange={e=>{choose(e.target.value);setSelectionVersion(v=>v+1);}}><option value="">{scope?"Wybierz zapisany szkic":"Trasy dostępne po zalogowaniu"}</option>{routes.map(r=><option key={r.id} value={r.id}>{r.name} · {r.points.length} pkt{r.tags?.map(t=>` · ${t.text}`).join("")}</option>)}</select></div>}
        moreActions={<><button disabled={busy||sketchDirty} onClick={()=>{choose("");setSelectionVersion(v=>v+1);}}>Nowy szkic</button><button disabled={busy||sketchDirty} onClick={()=>openEditor(id)}>Otwórz w osobnym oknie</button><button disabled={!route||busy||executing} onClick={()=>setDeleting(!deleting)}>Usuń szkic</button>{sketchDirty&&!state?.cavebot?.recording.active&&<button onClick={()=>{setSelectionVersion(v=>v+1);setSketchDirty(false);}}>Odrzuć niezapisane zmiany</button>}</>}/>
    </section>
      <section className="v2-combat"><header className="combat-section-header"><ActivationCheckbox label="Aktywuj walkę" checked={combat.enabled} disabled={busy||executing} onChange={enabled=>change({...combat,enabled})}/><ProfilePicker kind="combat" state={state} config={combat} routeId={id} scope={scope} legacy={route?.combat} onImport={value=>change(value as CombatConfig)} disabled={busy||executing} dirty={dirty} onRefresh={onRefresh} onApplied={value=>{setCombat(value as CombatConfig);setDirty(false);edited.current=false;setPreview(null);}}/></header>
      {dirty&&<p className="combat-unsaved" role="status">Niezapisana walka · zapisz lub odrzuć poniżej. Szkic możesz edytować niezależnie.</p>}
      <fieldset disabled={busy||executing} aria-label="Konfiguracja walki">
        {moveOnRoute&&<div className="combat-behavior" role="group" aria-label="Zachowanie podczas walki">{([['stop','Stój i walcz','Zatrzymaj się przy celu.'],['chase','Goń','Podejdź do celu i wróć na trasę.'],['lure','Luruj','Zbieraj podążające moby wzdłuż trasy.']] as const).map(([mode,label,hint])=><button key={mode} type="button" aria-pressed={combat.mode===mode} title={hint} onClick={()=>change({...combat,mode})}>{label}</button>)}</div>}
        <div className="v2-engage"><label>Zaznaczaj i atakuj<select aria-label="Warunek wyboru celu" value={combat.engage} onChange={e=>change({...combat,engage:e.target.value as CombatConfig['engage']})}><option value="visible">Gdy potwór jest na ekranie</option><option value="near">Gdy podejdzie na X SQM</option></select></label>{combat.engage==="near"&&<label>Do <input aria-label="Odległość ataku SQM" type="number" min={1} max={7} value={combat.radius} onChange={e=>change({...combat,radius:Number(e.target.value)})}/> SQM</label>}</div>
        {hasLure&&<LureSettings value={combat} onChange={change}/>}
        <MonsterRules rules={combat.rules} movement={moveOnRoute} disabled={busy||executing} onChange={rules=>change({...combat,rules})}/>
        <details className="v2-options"><summary>Wspólne czary <span>{combat.spells.length}</span></summary>
          <small className="spell-priority-note">Priorytet od góry · pierwszy dostępny czar · wspólny odstęp</small>
          {combat.spells.map((s,i)=><div className="combat-spell-card" key={i}>
            <div className="combat-spell-header"><b className="spell-priority-number">{i+1}</b><SpellPicker value={s} label={`Czar ${i+1}`} disabled={busy||executing} onChange={next=>change({...combat,spells:combat.spells.map((v,j)=>j===i?next:v)})}/>
              <SpellCooldown value={s} label={`Odstęp czaru ${i+1}`} disabled={busy||executing} onChange={next=>change({...combat,spells:combat.spells.map((v,j)=>j===i?next:v)})}/>
              <div className="spell-order"><button type="button" disabled={i===0} aria-label={`Czar ${i+1} w górę`} onClick={()=>change({...combat,spells:moveRule(combat.spells,i,-1)})}>↑</button><button type="button" disabled={i===combat.spells.length-1} aria-label={`Czar ${i+1} w dół`} onClick={()=>change({...combat,spells:moveRule(combat.spells,i,1)})}>↓</button></div>
              <button type="button" className="spell-remove" aria-label={`Usuń czar ${i+1}`} onClick={()=>change({...combat,spells:combat.spells.filter((_,j)=>j!==i)})}>×</button>
            </div>
            <SpellConditions value={s} disabled={busy||executing} onChange={next=>change({...combat,spells:combat.spells.map((v,j)=>j===i?next:v)})}/>
          </div>)}
          <button disabled={combat.spells.length>=12} onClick={()=>change({...combat,spells:[...combat.spells,defaultCombatSpell()]})}>＋ Dodaj czar</button></details>
        <details className="v2-options"><summary>Dodatkowe ustawienia</summary><div className="v2-select-row">
          <label>Przy równym priorytecie<select value={combat.strategy} onChange={e=>change({...combat,strategy:e.target.value as CombatConfig['strategy']})}><option value="nearest">Najbliższy</option><option value="lowest_hp">Najmniej HP</option><option value="highest_hp">Najwięcej HP</option><option value="finish">Dokończ obecny cel</option></select></label>
          {moveOnRoute&&combat.mode==='chase'&&<label>Dystans pościgu (SQM)<input type="number" min={1} max={7} value={combat.distance} onChange={e=>change({...combat,distance:Number(e.target.value)})}/></label>}
          {hasLure&&<label title="Po tym czasie zatrzymaj ruch i walcz, nawet jeśli grupa nie jest pełna.">Limit zbierania (s)<input type="number" min={1} max={30} value={combat.lure_seconds} onChange={e=>change({...combat,lure_seconds:Number(e.target.value)})}/></label>}</div></details>
      </fieldset><div className="v2-savebar"><button disabled={!dirty||busy||executing} onClick={save}>Zapisz walkę</button><button disabled={!dirty||busy} onClick={()=>{setCombat(JSON.parse(globalCombat));setDirty(false);edited.current=false;setPreview(null);setError("");}}>Odrzuć zmiany</button><button disabled={busy||!state?.connected} onClick={inspectTargets}>Sprawdź cele</button></div>
      {executing&&!!run?.combat?.spells?.length&&<details className="v2-options" open><summary>Warunki czarów</summary><SpellDiagnostics rows={run.combat.spells}/>{run.combat.last_spell_attempt&&<small className="spell-attempt-note">Ostatnia próba: {run.combat.last_spell_attempt.words} · {new Date(run.combat.last_spell_attempt.at*1000).toLocaleTimeString('pl-PL')} · bez potwierdzenia serwera</small>}</details>}
      {preview?.spells&&<SpellDiagnostics rows={preview.spells} passive/>}
      {preview&&<div className="v2-targets"><strong>Pasujące cele</strong><small>{preview.message}</small>{preview.targets.map(t=><p key={t.id}>{preview.selected?.id===t.id?'◎ ':''}{t.name} · {t.health}% HP · {t.distance} pól · priorytet {t.priority}</p>)}{!preview.targets.length&&<p>Brak pasujących, świeżo rozpoznanych potworów.</p>}</div>}
      </section></div>
    {!!trash.length&&<details className="v2-trash"><summary>Usunięte szkice ({trash.length})</summary>{trash.map(r=><div key={r.id}><span>{r.name}</span><button disabled={busy} onClick={()=>removeOrRestore(r.id,"restore")}>Przywróć</button></div>)}</details>}

  </div>;
}
