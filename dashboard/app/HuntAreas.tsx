"use client";
import { t, labels, num, language, onLanguage } from "@/lib/i18n";
import {useEffect,useMemo,useRef,useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import type {BotState} from "@/lib/api";
import {spawnAreas,visibleMonsters,type Witness} from "@/lib/spawnAreas";
import type {WorldZones} from "@/lib/useWorldZones";
import {localGround,sectorHabitats} from "@/lib/atlasWildlife";
import WorldMap,{type GroundLayer,type Position,type AtlasPassage,type AtlasConnector,type WorldViewport} from "./WorldMap";

type Observation={kind:"death"|"first_contact"|"first_seen";cell:Position;name:string;count:number;at_monster:boolean;point?:Position;observer_cell?:Position};
type Terrain={ground:string;sectors:Position[];via?:Position[]};
type TerrainChoice={sectors:Position[];via:Position[]};
type Draft={terrain?:Terrain|null;id:string;name:string;archived:boolean;laps:number;duration_ms:number;blocked_ms:number;xp_hour:number|null;senzu_hour:number|null;
  sightings?:Observation[];witnesses?:Witness[];sighting_laps?:number;latest:{id:string;cells:Position[];observations:Observation[]}|null};
type Area={id:string|null;version?:number;name:string;cells:Position[];drafts:string[]};
type Discovery=Area&{id:string;visits:number;xp:number;eligible:boolean;sightings:Observation[];witnesses?:Witness[];
  needs_recheck?:boolean;check?:{covered?:number;total?:number;missing?:Position[];reason?:string}};
type Catalogue={scope:string;sector_size:number;areas:Area[];drafts:Draft[];discoveries?:Discovery[]};
type VariantKind="short"|"respawn"|"full";
const variantNames=labels("hunt.variant",["short","respawn","full"] as const);
type Variant={kind:VariantKind;metrics:{seconds:number;xp_hour:number|null;encounters_hour:number;kept_visible:number;repeated_segments:number};
  geometry:Pick<Circuit,"path"|"points"|"links"|"covered"|"transitions"|"passages">;same_as?:VariantKind};
type Comparison={variants:Variant[];visits:number;respawn_seconds:number;view_radius:number;observed_return_sectors:number;reference_laps:number;step_seconds:number;fight_seconds:number};
type Circuit={floor_checks?:{point:Position;to_floor:number;exact:boolean}[];comparison?:Comparison;path:Position[];points:Position[];links:{from_index:number;id:string}[];entrance:Position;
  covered:number;total:number;missing:Position[];selected:Position[];via?:Position[];passages?:AtlasPassage[];connector?:AtlasConnector;sightings:number;transitions:number};
const endpoint="http://127.0.0.1:8089/api/map/grounds";
const label=(n:number|null)=>n===null?"—":num(n);
const seconds=(ms:number)=>Math.round(ms/1000);
function duration(ms:number){const s=seconds(ms);return Math.floor(s/60)+" min "+s%60+" s";}
function proposal(d:Draft):Area{return {id:null,name:d.name,cells:Array.from(new Map([...(d.latest?.cells??[]),...(d.terrain?.sectors??[])].map(p=>[p.join(","),p])).values()),drafts:[d.id]};}
function center(area:Area,floor:number){
  const z=area.cells.some(p=>p[2]===floor)?floor:(area.cells[0]?.[2]??floor);
  return {floor:z,cells:area.cells};
}
async function request(init:RequestInit={},url=endpoint) {
  const r=await authenticatedFetch(url,init);
  const body=await r.json();
  if(!r.ok)throw Error(body.error||t("hunt.loadFailed"));
  return body;
}
function post(url:string,body:unknown,signal?:AbortSignal){return request({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal},url);}

export default function HuntAreas({initialFloor:playerFloor,onOpen,onClose,state,player,location=player,world=null,worldError="",retryWorld,viewport,startInWorld=false}:{world?:WorldZones|null;worldError?:string;retryWorld?:()=>void;viewport?:{current:WorldViewport|null};startInWorld?:boolean;initialFloor:number;onOpen:(p:Position)=>void;onClose:()=>void;state?:BotState|null;player?:Position|null;location?:Position|null}){
  const [initialFloor]=useState(playerFloor);
  const [data,setData]=useState<Catalogue|null>(null),[key,setKey]=useState(startInWorld?"world":""),[copy,setCopy]=useState<Area|null>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const [showMonsters,setShowMonsters]=useState(true),[species,setSpecies]=useState("");
  const [layer,setLayer]=useState<Observation["kind"]|"spawns"|"live">("spawns");
  const kind=layer==="spawns"||layer==="live"?"first_seen":layer;
  const [focus,setFocus]=useState<GroundLayer["focus"]>(null);
  const [baseRoute,setRoute]=useState<Circuit|null>(null),[variant,setVariant]=useState<VariantKind>("full"),[approach,setApproach]=useState<Position[]>([]);
  const options=baseRoute?.comparison;
  const activeOption=options?.variants.find(v=>v.kind===variant);
  const route=baseRoute&&activeOption?{...baseRoute,...activeOption.geometry}:baseRoute;
  const [saved,setSaved]=useState<Partial<Record<VariantKind,string>>>({}),[routeError,setRouteError]=useState("");
  const savedDraft=saved[variant];
  const [planning,setPlanning]=useState("");
  const [selection,setSelection]=useState<Position[]|null>(null),[selecting,setSelecting]=useState(startInWorld);
  const [confirmedVia,setConfirmedVia]=useState<Position[]|null>(null);
  const [previousSelection,setPreviousSelection]=useState<TerrainChoice|null>(null);
  const baseEntrance=useRef<Position|null>(null);
  const previewRequest=useRef<AbortController|null>(null);
  useEffect(()=>()=>previewRequest.current?.abort(),[]);
  const createKey=useRef("");
  const [pinned,setPinned]=useState(startInWorld);
  useEffect(()=>{
    const ctl=new AbortController();setError("");
    request({signal:ctl.signal}).then((info:Catalogue)=>{
      if(ctl.signal.aborted)return;
      setData(info);
    }).catch(e=>{if(!ctl.signal.aborted)setError(e.message);});
    return ()=>ctl.abort();
  },[initialFloor,reload]);
  const scope=data?.scope;
  const [checkedScope,setCheckedScope]=useState("");
  useEffect(()=>{
    if(!scope)return;
    const ctl=new AbortController();
    let loading=false,revision="";
    async function refreshDiscoveries(){
      if(loading||document.hidden)return;
      loading=true;
      try{
        const next=await request({signal:ctl.signal},"http://127.0.0.1:8089/api/map/discoveries?since="+encodeURIComponent(revision));
        if(next.scope===scope&&!ctl.signal.aborted){
          revision=next.revision;
          if(next.discoveries)setData(old=>old&&old.scope===scope?{...old,discoveries:next.discoveries}:old);
        }
      }catch{/* Retain the last published suggestions during reconnect. */}
      finally{loading=false;if(!ctl.signal.aborted)setCheckedScope(scope!);}
    }
    void refreshDiscoveries();
    const interval=setInterval(refreshDiscoveries,20000);
    return ()=>{ctl.abort();clearInterval(interval);};
  },[scope]);
  const activeWorld=world?.scope===scope?world:null;
  const [shownLanguage,setShownLanguage]=useState(language());useEffect(()=>onLanguage(setShownLanguage),[]);
  const choices=useMemo(()=>{
    const all=new Map<string,Area>();
    if(activeWorld)all.set("world",{id:"world",name:t("hunt.worldZones"),cells:activeWorld.cells,drafts:[]});
    for(const a of data?.areas??[])if(a.id)all.set(a.id,a);
    for(const a of data?.discoveries??[])all.set(a.id,a);
    for(const d of data?.drafts??[])if(!d.archived)all.set("draft:"+d.id,proposal(d));
    return all;
  },[data,activeWorld,shownLanguage]);
  const draft=key.startsWith("draft:")?data?.drafts.find(d=>"draft:"+d.id===key):undefined;
  const terrain=draft?.terrain;
  const source=terrain?choices.get(terrain.ground):undefined;
  const sourceCells=useMemo(()=>new Set(source?.cells.map(p=>p.join(","))),[source]);
  const hasSource=!!source&&!!terrain&&terrain.sectors.every(p=>sourceCells.has(p.join(",")));
  const base=choices.get(key);
  const area=copy??(hasSource&&terrain?.ground!=="world"&&base?{...base,cells:source!.cells}:base);
  const selected=selection??terrain?.sectors??[];
  const cells=useMemo(()=>new Set(area?.cells.map(p=>p.join(","))),[area?.cells]);
  const planKey=activeWorld&&selected.some(p=>!cells.has(p.join(",")))?"world":hasSource?terrain!.ground:key;
  const worldLayer=key==="world"||hasSource&&terrain?.ground==="world";
  const via=confirmedVia??terrain?.via??[];
  const floors=[...new Set(selected.map(p=>p[2]))].sort((a,b)=>a-b);
  const discovery=data?.discoveries?.find(d=>d.id===key);
  const side=data?.sector_size??8;
  const sx=location?Math.floor(location[0]/side)*side:null,sy=location?Math.floor(location[1]/side)*side:null,z=location?.[2];
  const cavebot=state?.adaptive_cavebot;
  const activeDraft=cavebot?.mode==="route"&&cavebot.status==="running"?cavebot.draft_id:undefined;
  const localKey=useMemo(()=>sx===null||sy===null||z===undefined?"":localGround(choices,[sx,sy,z],side,activeDraft),
    [choices,sx,sy,z,side,activeDraft]);
  useEffect(()=>{
    if(key==="world"||!scope||checkedScope!==scope||copy||planning||busy||selecting||selection!==null||baseRoute)return;
    const exists=choices.has(key);
    if(exists&&(pinned||!localKey)||localKey===key)return;
    resetRoute();setKey(localKey);setPinned(false);setLayer("spawns");setSpecies("");
    setFocus(localKey?center(choices.get(localKey)!,z!):null);
  },[scope,checkedScope,choices,key,localKey,pinned,copy,planning,busy,selecting,selection,baseRoute,z]);
  const linked=useMemo(()=>data?.drafts.filter(d=>area?.drafts.includes(d.id))??[],[area?.drafts,data]);
  const [,expire]=useState(0);
  const captured=state?.passive_map?.captured_at;
  useEffect(()=>{
    if(!captured||layer!=="live")return;
    const delay=captured*1000+2001-Date.now();
    if(delay<=0)return;
    const timer=setTimeout(()=>expire(n=>n+1),delay);
    return ()=>clearTimeout(timer);
  },[captured,layer]);
  const live=layer==="live"?visibleMonsters(state,cells,side,Date.now()):null;
  const observations=useMemo(()=>{
    const names=new Map<string,number>(), locations=new Map<string,{position:Position;lines:Map<string,number>}>();
    if(worldLayer&&activeWorld){
      for(const h of activeWorld.habitats)for(const [n,count] of h.species)names.set(n,(names.get(n)??0)+count);
      return {names,marks:[],habitats:species?activeWorld.habitats.filter(h=>h.species.some(([n])=>n===species)):undefined};
    }
    const filtered:Observation[]=[];
    const rows=discovery?discovery.sightings:linked.flatMap(d=>(kind==="first_seen"?d.sightings:d.latest?.observations)??[]);
    for(const k of rows){
      if(k.kind!==kind)continue;
      const origin=k.kind==="first_seen"?k.observer_cell:k.cell;
      const position=k.kind==="first_seen"?k.point:k.cell;
      if(!origin||!position||!cells.has(origin.join(",")))continue;
      names.set(k.name,(names.get(k.name)??0)+k.count);
      if(species&&species!==k.name)continue;
      if(kind==="first_seen")filtered.push(k);
      if(layer==="spawns")continue;
      const loc=position.join(",");
      const item=locations.get(loc)??{position,lines:new Map<string,number>()};
      const line=k.name+(kind==="first_seen"?"":" · "+t(k.at_monster?"hunt.atMonster":"hunt.nearCharacter"));
      item.lines.set(line,(item.lines.get(line)??0)+k.count);
      locations.set(loc,item);
    }
    if(layer==="spawns"){
      const witnesses=(discovery?.witnesses??linked.flatMap(d=>d.witnesses??[])).filter(g=>cells.has(g.observer_cell.join(",")));
      const marks=spawnAreas(filtered,witnesses,!species).map(s=>{
        const title=[t(s.sightings===1?"hunt.single":"worldMap.spawn"),
          ...Array.from(s.species).sort(([,a],[,b])=>b.sightings-a.sightings).map(([n,v])=>
            n+" · "+t("hunt.firstSightings",{n:v.sightings})+(v.minimum?" · "+t("hunt.atLeast",{n:v.minimum}):""))].join("\n");
        return {position:s.position,exact:true,radius:s.radius,uncertain:s.sightings===1,minimum:s.minimum??undefined,title};
      });
      return {names,marks,habitats:species?sectorHabitats(filtered,side).habitats:undefined};
    }
    return {names,habitats:species&&layer==="first_seen"?sectorHabitats(filtered,side).habitats:undefined,marks:Array.from(locations.values(),v=>({position:v.position,exact:kind==="first_seen",
      title:[...(kind==="first_seen"?[t("hunt.firstSeenAt",{place:v.position.join(", ")})]:[]),
        ...Array.from(v.lines,([name,count])=>name+" ×"+count)].join("\n")}))};
  },[cells,linked,species,kind,layer,discovery,side,worldLayer,activeWorld]);
  const applied=useMemo(()=>new Set(route?.selected.map(p=>p.join(","))),[route]);
  const appliedVia=useMemo(()=>new Set(route?.via?.map(p=>p.join(","))),[route]);
  const dirty=!!route&&(selected.length!==applied.size||selected.some(p=>!applied.has(p.join(",")))||
    via.length!==appliedVia.size||via.some(p=>!appliedVia.has(p.join(","))));
  const connector=dirty?undefined:route?.connector;
  const connectorPlaces=connector?[...connector.sectors,...connector.passages.flatMap(t=>[t.entry,t.exit])]:[];
  const connectorFloors=[...new Set(connectorPlaces.map(p=>p[2]))].sort((a,b)=>a-b);
  const check=route&&!dirty?baseRoute:discovery?.check;
  const floorCoverage=useMemo(()=>{
    if(!discovery||!check?.missing?.length)return [];
    const outside=new Set(check.missing.map(p=>p.join(",")));
    const stats=new Map<number,{floor:number;covered:number;total:number}>();
    for(const p of discovery.cells){
      if(route&&!applied.has(p.join(",")))continue;
      const row=stats.get(p[2])??{floor:p[2],covered:0,total:0};
      row.total++;
      if(!outside.has(p.join(",")))row.covered++;
      stats.set(p[2],row);
    }
    return [...stats.values()].sort((a,b)=>a.floor-b.floor);
  },[discovery,check,applied,route]);
  const selectedPath=activeOption?.geometry.path??baseRoute?.path;
  const variantFloors=useMemo(()=>{
    const visited=new Set((selectedPath??[]).map(([x,y,z])=>[Math.floor(x/side)*side,Math.floor(y/side)*side,z].join(",")));
    const counts=new Map<number,{floor:number;covered:number;total:number}>();
    for(const p of baseRoute?.selected??[]){
      const row=counts.get(p[2])??{floor:p[2],covered:0,total:0};
      row.total++;
      if(visited.has(p.join(",")))row.covered++;
      counts.set(p[2],row);
    }
    return [...counts.values()].sort((a,b)=>a.floor-b.floor);
  },[selectedPath,baseRoute,side]);
  function pick(k:string){
    const automatic=k==="@local";
    if(automatic)k=localKey;
    const a=choices.get(k);if(!a)return;
    setPinned(!automatic);
    createKey.current="";
    resetRoute();
    if(k==="world")setSelecting(true);
    setKey(k);setCopy(null);setSpecies("");setLayer("spawns");setError("");setFocus(k==="world"?null:center(a,location?.[2]??initialFloor));
  }
  function resetRoute(){
    setRoute(null);setApproach([]);setSaved({});setVariant("full");setRouteError("");setSelection(null);setConfirmedVia(null);setPreviousSelection(null);setSelecting(false);baseEntrance.current=null;
  }
  function selectSectors(a:Position,b:Position){
    if(planning||!selecting||!area||a[2]!==b[2])return;
    const start=[Math.floor(a[0]/side)*side,Math.floor(a[1]/side)*side,a[2]].join(",");
    const next=new Map(selected.map(p=>[p.join(","),p]));
    const remove=next.has(start);
    for(const p of activeWorld?.cells??area.cells){
      if(p[2]!==a[2]||p[0]>Math.max(a[0],b[0])||p[0]+side<=Math.min(a[0],b[0])||
          p[1]>Math.max(a[1],b[1])||p[1]+side<=Math.min(a[1],b[1]))continue;
      if(remove)next.delete(p.join(","));else next.set(p.join(","),p);
    }
    if(next.size!==selected.length)changeSelection([...next.values()]);
  }
  function changeSelection(next:Position[]){
    setPreviousSelection({sectors:selected,via});setSelection(next);setConfirmedVia([]);setRouteError("");setApproach([]);
  }
  async function preview(from?:Position,addConnector=false){
    if(!data||!selected.length||busy||planning||copy||(from&&(!route||dirty)))return;
    setPlanning(t(from?"hunt.planningApproach":"hunt.comparing"));setRouteError("");
    setApproach([]);
    const ctl=new AbortController();previewRequest.current=ctl;
    try{
      const requestedVia=addConnector&&connector?[...via,...connector.sectors]:via;
      const anchor=baseEntrance.current;
      const keepAnchor=anchor&&selected.some(([x,y,z])=>z===anchor[2]&&x<=anchor[0]&&anchor[0]<x+side&&y<=anchor[1]&&anchor[1]<y+side);
      let result=await post(endpoint+"/route",{scope:data.scope,ground:planKey,selected,...(requestedVia.length?{via:requestedVia}:{}),
        ...(keepAnchor?{loop_start:anchor}:{}),...(from?{approach:from,entrance:route!.entrance}:{compare:true})},ctl.signal);
      while(result.ticket){
        await new Promise(resolve=>setTimeout(resolve,350));
        result=await post(endpoint+"/route",{scope:data.scope,ticket:result.ticket},ctl.signal);
      }
      if(from)setApproach(result.path);
      else {
        if(addConnector)setPreviousSelection({sectors:selected,via});
        setConfirmedVia(result.via??[]);
        baseEntrance.current=result.entrance;
        setRoute(result);setVariant(floors.length===1&&result.comparison?.visits?"respawn":"full");setSelecting(false);setSaved({});
      }
    }catch(e){if(!ctl.signal.aborted)setRouteError(e instanceof Error?e.message:t("hunt.routeFailed"));}
    finally{if(!ctl.signal.aborted)setPlanning("");}
  }
  async function saveCircuit(){
    if(!route||!area||!data||planning||savedDraft||dirty)return;
    setPlanning(t("hunt.savingDraft"));setRouteError("");
    try{
      const result=await post("http://127.0.0.1:8089/api/map/drafts",{scope:data.scope,
        name:(key==="world"?t("hunt.zonesName")+" · "+route.entrance.join(", "):area.name.slice(0,65))+" · "+(options?variantNames[variant]:t("hunt.atlasLoop")),points:route.points,links:route.links,loop:true,terrain:{ground:planKey,sectors:route.selected,...(route.via?.length?{via:route.via}:{})}});
      const saved=result.drafts?.find((d:Draft)=>d.id===result.saved_id);
      if(saved)setData(old=>old?{...old,drafts:[{id:saved.id,name:saved.name,terrain:saved.terrain,archived:false,
        laps:0,duration_ms:0,blocked_ms:0,xp_hour:null,senzu_hour:null,latest:null},...old.drafts.filter(d=>d.id!==saved.id)]}:old);
      setSaved(old=>({...old,[variant]:result.saved_id}));
    }catch(e){setRouteError(e instanceof Error?e.message:t("hunt.saveDraftFailed"));}
    finally{setPlanning("");}
  }
  async function editCircuit(draft:string){
    try{
      if(!window.dbvEditor)throw Error(t("hunt.openInCavebot"));
      if(!await window.dbvEditor.open(draft))setRouteError(t("hunt.editorOpen"));
    }catch(e){setRouteError(e instanceof Error?e.message:t("hunt.editorFailed"));}
  }
  function toggle(p:Position){
    if(!copy||busy)return;
    const cell:Position=[Math.floor(p[0]/side)*side,Math.floor(p[1]/side)*side,p[2]],k=cell.join(",");
    setCopy({...copy,cells:copy.cells.some(q=>q.join(",")===k)?copy.cells.filter(q=>q.join(",")!==k):[...copy.cells,cell]});
  }
  async function save(){
    if(!area||!data||busy)return;
    setBusy(true);setError("");
    try{
      if(!area.id&&!createKey.current)createKey.current=crypto.randomUUID();
      const {id}=await request({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...area,scope:data.scope,create_key:createKey.current||undefined})});
      const saved={...area,id,version:(area.version??0)+1};
      setData({...data,areas:[...data.areas.filter(a=>a.id!==id),saved]});
      setKey(id);setPinned(true);setCopy(null);
    }catch(e){setError(e instanceof Error?e.message:t("hunt.saveAreaFailed"));}
    finally{setBusy(false);}
  }
  return <div className="hunt-atlas-layout">
    <WorldMap spawns={worldLayer&&showMonsters?activeWorld?.spawns:undefined} viewport={viewport} zones={copy?undefined:activeWorld??undefined} initialFloor={initialFloor} player={player} location={location} onOpen={onOpen} loadingLabel={planning||undefined}
      habitats={showMonsters?observations.habitats:undefined} habitatSide={side} habitatName={showMonsters&&observations.habitats?species:""}
      toolbar={<button disabled={!!copy||busy||!!planning} onClick={onClose}>← {t("globalAtlas.world")}</button>}
      ground={{cells:area?.cells??[],marks:!showMonsters?[]:layer==="live"?(live?.marks??[]).map(m=>({position:m.position,exact:true,live:true,title:t("hunt.visibleMonsters",{n:m.count})})):observations.marks,side,editing:!!copy,toggle,focus,
        route:dirty?undefined:route?.path,connector,via,passages:dirty?undefined:route?.passages,approach,missing:dirty?undefined:route?.missing,selected:copy?undefined:selected,selecting,selectSectors,
        planApproach:route&&!planning&&!dirty&&!selecting?preview:undefined}}/>
    <aside className="hunt-area-panel">
      <h2>{t("hunt.title")}</h2>
      {worldError&&<p role="alert">{worldError} <button onClick={retryWorld}>{t("common.retry")}</button></p>}
      {data&&key==="world"&&!activeWorld&&!worldError&&<p role="status">{t("world.loadingZones")}</p>}
      {error&&<p role="alert">{error} {!copy&&<button onClick={()=>setReload(v=>v+1)}>{t("hunt.reload")}</button>}</p>}
      {!data&&!error&&<p role="status" className="atlas-loading"><i className="atlas-spinner"/>{t("common.loading")}</p>}
      {data&&<label><select aria-label={t("hunt.areaOrDraft")} disabled={!!copy||busy||!!planning} value={key} onChange={e=>pick(e.target.value)}>
        {!area&&key!=="world"&&<option value="" disabled>{t("hunt.chooseArea")}</option>}
        {(activeWorld||key==="world")&&<option value="world">{t("hunt.allZones")}</option>}
        {pinned&&localKey&&<option value="@local">{t("hunt.nearYou")} · {choices.get(localKey)?.name}</option>}
        {!!data.discoveries?.length&&<optgroup label={t("hunt.fromPlay")}>{data.discoveries.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</optgroup>}
        {!!data.areas.length&&<optgroup label={t("hunt.savedAreas")}>{data.areas.map(a=><option key={a.id} value={a.id!}>{a.name}</option>)}</optgroup>}
        <optgroup label={t("hunt.fromDrafts")}>{data.drafts.filter(d=>!d.archived).map(d=><option key={d.id} value={"draft:"+d.id}>{d.name}</option>)}</optgroup>
      </select></label>}
      {data&&!choices.size&&!area&&<p>{t("hunt.emptyAreas")}</p>}
      {area&&<>
        {!copy&&!area.id&&!area.cells.length&&<p>{t("hunt.noLap")}</p>}
        {discovery?<div>
          <p>{t("hunt.visits",{n:discovery.visits})} · {label(discovery.xp)} XP</p>
          <strong role="status">{check?.covered&&check.total
            ?(check.covered===check.total?t("hunt.loopReady"):t("hunt.loopPartial",{covered:check.covered,total:check.total}))
            :discovery.check?.reason??t(discovery.needs_recheck?"hunt.recheck":
              discovery.eligible?"hunt.checking":"hunt.comeBack")}</strong>
          {!!floorCoverage.length&&!route&&<details><summary>{t("hunt.outside",{n:check?.missing?.length ?? 0})}</summary>
            {floorCoverage.map(s=><small key={s.floor}>{t("hunt.floorCoverage",{floor:s.floor,covered:s.covered,total:s.total})}</small>)}</details>}
        </div>:copy?<fieldset disabled={busy}>
          <label>{t("hunt.areaName")}<input value={copy.name} maxLength={100} onChange={e=>setCopy({...copy,name:e.target.value})}/></label>
          <p>{t("hunt.editHint",{side})}</p>
          <details><summary>Przypisane szkice ({copy.drafts.length})</summary>{data?.drafts.filter(d=>!d.archived||choices.get(key)?.drafts.includes(d.id)).map(d=><label key={d.id}><input type="checkbox" checked={copy.drafts.includes(d.id)} onChange={e=>setCopy({...copy,drafts:e.target.checked?[...copy.drafts,d.id]:copy.drafts.filter(i=>i!==d.id)})}/>{d.name}{d.archived?" (kosz)":""}</label>)}</details>
          <button onClick={save} disabled={!copy.name.trim()||!copy.cells.length||!copy.drafts.length}>{t("hunt.saveArea")}</button> <button onClick={()=>{setCopy(null);setError("");}}>{t("common.cancel")}</button>
        </fieldset>:key==="world"?null:<div><button disabled={busy||!!planning} onClick={()=>{resetRoute();setCopy({...area});}}>{t(area.id?"hunt.editArea":"hunt.adjust")}</button> {!area.id&&<button disabled={busy||!!planning||!area.cells.length} onClick={save}>{t("hunt.saveArea")}</button>}</div>}
        {!copy&&<section aria-label={t("hunt.loopAria")} className="hunt-section"><h3>{t("hunt.route")}</h3>
          {key.startsWith("draft:")&&<button disabled={!!planning} onClick={()=>editCircuit(key.slice(6))}>{t("hunt.editDraft")}</button>}
          <div className="map-exclusions">
            <button disabled={!!planning||!area.cells.length} aria-pressed={selecting} onClick={()=>setSelecting(!selecting)}>{t("hunt.chooseGround")}</button>
            {key==="world"&&localKey&&<button disabled={!!planning} title={choices.get(localKey)?.name} onClick={()=>{changeSelection(choices.get(localKey)!.cells);setSelecting(true);}}>{t("hunt.selectNear")}</button>}
            {!worldLayer&&<button disabled={!!planning||!area.cells.length} onClick={()=>changeSelection(area.cells)}>{t("hunt.wholeArea")}</button>}
            {!!selected.length&&<button disabled={!!planning} onClick={()=>changeSelection([])}>{t("hunt.clear")}</button>}
            {previousSelection&&<button disabled={!!planning} onClick={()=>{setSelection(previousSelection.sectors);setConfirmedVia(previousSelection.via);setPreviousSelection(null);setRouteError("");setApproach([]);}}>{t("hunt.undo")}</button>}
            <small role="status">{t("hunt.selected")} · {selected.length}{floors.length?" · "+t("hunt.floors",{list:floors.join(", ")}):""}</small>
            {selecting&&<small>{t("hunt.selectHint")}</small>}
          </div>
          <button className="atlas-primary" disabled={!!planning||busy||!selected.length} onClick={()=>preview()}>{planning===t("hunt.comparing")?planning:t("hunt.compare")}</button>
          {routeError&&<p role="alert">{routeError}</p>}
          {dirty&&<small role="status">{t("hunt.changed")}</small>}
          {route&&!dirty&&options&&<section aria-label={t("hunt.variants")} className="hunt-variants">
            {options.variants.map(v=><button key={v.kind} disabled={!!planning} aria-pressed={variant===v.kind}
              onClick={()=>{setVariant(v.kind);setApproach([]);setRouteError("");}}>
              <strong>{variantNames[v.kind]}</strong><span>{duration(v.metrics.seconds*1000)} · {v.geometry.covered}/{route.total} {t("hunt.sectShort")}</span>
              <small>{v.metrics.xp_hour===null?t("hunt.noXp"):`≈ ${v.metrics.xp_hour>=1000000?t("hunt.million",{n:num(v.metrics.xp_hour/1000000,{maximumFractionDigits:1})}):label(v.metrics.xp_hour)} XP/h`}{v.metrics.kept_visible>0?` · ${t("hunt.keptVisible",{n:v.metrics.kept_visible})}`:""}{v.same_as?` · ${t("hunt.sameAs",{name:variantNames[v.same_as]})}`:""}</small>
            </button>)}
            <details><summary>{t("hunt.assumptions",{n:options.visits})}</summary>
              <small>{t("hunt.repeats",{n:options.variants.find(v=>v.kind===variant)?.metrics.repeated_segments ?? 0})}</small>
              <small>{t("hunt.model",{radius:options.view_radius,returns:options.observed_return_sectors,respawn:options.respawn_seconds,step:options.step_seconds,fight:options.fight_seconds})}{options.reference_laps>0&&" "+t("hunt.reference",{n:options.reference_laps})}</small>
            </details>
          </section>}
          {route&&!dirty&&<><p>{t("hunt.coverage",{covered:route.covered,total:route.total,steps:route.path.length-1,transitions:route.transitions})}</p>
            {variantFloors.length>1&&<div className="map-exclusions" aria-label={t("hunt.floorsAria")}>
              {variantFloors.map(row=><button key={row.floor} title={t("hunt.showFloor")}
                onClick={()=>setFocus({floor:row.floor,cells:route.selected.filter(p=>p[2]===row.floor)})}>{t("worldMap.floor")+" "}{row.floor} · {row.covered}/{row.total}</button>)}
            </div>}
            {connector&&<section aria-label={t("worldMap.proposed")} className="atlas-connector">
              <strong>{t("hunt.fullLoop",{covered:connector.covered,total:route.total})}</strong>
              <small>{t("hunt.extra",{n:connector.sectors.length})}</small>
              <div>{connectorFloors.map(z=><button key={z} disabled={!!planning} onClick={()=>setFocus({floor:z,cells:connectorPlaces})}>{t("worldMap.floor")+" "}{z}</button>)}</div>
              <button className="atlas-primary" disabled={!!planning} onClick={()=>preview(undefined,true)}>{t("hunt.addConnector")}</button>
            </section>}
            {route.missing.length>0&&!connector&&<small>{t("hunt.noConnector")}</small>}
            {!connector&&!!route.floor_checks?.length&&<details><summary>{t("hunt.floorChecks")}</summary>
              <small>{t("hunt.floorChecksNote")}</small>
              {route.floor_checks.map(h=><button key={h.point.join(",")+":"+h.to_floor}
                onClick={()=>setFocus({floor:h.point[2],cells:[h.point]})}>
                {h.point[2]} → {h.to_floor} · {t(h.exact?"hunt.tile":"hunt.area")} ({h.point.join(", ")})</button>)}
            </details>}
            {!!route.missing.length&&<details><summary>{t("hunt.missing",{n:route.missing.length})}</summary>
              {route.missing.map(p=><button key={p.join(",")} onClick={()=>setFocus({floor:p[2],cells:[p]})}>{p.join(", ")}</button>)}</details>}
            {!!approach.length&&<small>{t("hunt.approach",{n:approach.length-1})}</small>}
            <button disabled={!!planning||dirty} onClick={()=>savedDraft?editCircuit(savedDraft):saveCircuit()}>{t(savedDraft?"hunt.openSaved":route.missing.length?"hunt.savePart":"hunt.saveNew")}</button>
          </>}
        </section>}
        {!discovery&&linked.length>0&&<details className="hunt-results"><summary>{t("hunt.results")}</summary>
        {linked.map(d=><div key={d.id}><strong>{d.name}</strong>{d.laps?<dl>
          <dt>{t("hunt.laps")}</dt><dd>{d.laps}</dd><dt>{t("hunt.avgLap")}</dt><dd>{duration(d.duration_ms/d.laps)}</dd>
          <dt>XP/h</dt><dd>{label(d.xp_hour)}</dd><dt>Senzu/h</dt><dd>{label(d.senzu_hour)}</dd>
          <dt>{t("hunt.blocksPerLap")}</dt><dd>{seconds(d.blocked_ms/d.laps)} s</dd></dl>:<p>{t("hunt.noLaps")}</p>}</div>)}</details>}
        <h3>{t("rules.monsters")}</h3>{!worldLayer&&<select aria-label={t("hunt.observationKind")} value={layer} onChange={e=>{setLayer(e.target.value as typeof layer);setSpecies("");}}>
          <option value="spawns">{t("hunt.spawns")}</option><option value="live">{t("hunt.live")}</option><option value="first_seen">{t("hunt.firstSeen")}</option>
          {!discovery&&<><option value="death">{t("hunt.defeated")}</option><option value="first_contact">{t("reports.kind.first_contact")}</option></>}</select>}
        <label><input type="checkbox" checked={showMonsters} onChange={e=>setShowMonsters(e.target.checked)}/> {t("hunt.showOnMap")}</label>
        {layer==="live"?<p>{live?t("hunt.liveCount",{n:live.count,floor:live.floor})+(live.unknown?" · "+t("hunt.unknownCount",{n:live.unknown}):""):t("hunt.noLive")}</p>:observations.names.size?<><select aria-label={t("hunt.species")} value={species} onChange={e=>setSpecies(e.target.value)}><option value="">{t("world.allSpecies")}</option>
          {Array.from(observations.names).sort(([a],[b])=>a.localeCompare(b)).map(([n,count])=><option key={n} value={n}>{layer==="spawns"?n:n+" ×"+count}</option>)}</select>
          {(kind==="death"||kind==="first_contact")&&<small>{t("hunt.lastLap")}</small>}
        </>:<p>{kind==="first_seen"&&linked.some(d=>d.latest)&&!linked.some(d=>d.sighting_laps)
          ?t("hunt.noFirstSeen"):t("hunt.noObservations")}</p>}
      </>}
    </aside>
  </div>;
}
