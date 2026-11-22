"use client";
import {useEffect,useMemo,useRef,useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import type {BotState} from "@/lib/api";
import {spawnAreas,visibleMonsters,type Witness} from "@/lib/spawnAreas";
import {sectorHabitats} from "@/lib/atlasWildlife";
import WorldMap,{type GroundLayer,type Position} from "./WorldMap";

type Observation={kind:"death"|"first_contact"|"first_seen";cell:Position;name:string;count:number;at_monster:boolean;point?:Position;observer_cell?:Position};
type Terrain={ground:string;sectors:Position[]};
type Draft={terrain?:Terrain|null;id:string;name:string;archived:boolean;laps:number;duration_ms:number;blocked_ms:number;xp_hour:number|null;senzu_hour:number|null;
  sightings?:Observation[];witnesses?:Witness[];sighting_laps?:number;latest:{id:string;cells:Position[];observations:Observation[]}|null};
type Area={id:string|null;version?:number;name:string;cells:Position[];drafts:string[]};
type Discovery=Area&{id:string;visits:number;xp:number;eligible:boolean;sightings:Observation[];witnesses?:Witness[];
  needs_recheck?:boolean;check?:{covered?:number;total?:number;missing?:Position[];reason?:string}};
type Catalogue={scope:string;sector_size:number;areas:Area[];drafts:Draft[];discoveries?:Discovery[]};
type Circuit={path:Position[];points:Position[];links:{from_index:number;id:string}[];entrance:Position;
  covered:number;total:number;missing:Position[];selected:Position[];sightings:number;transitions:number};
const endpoint="http://127.0.0.1:8089/api/map/grounds";
const label=(n:number|null)=>n===null?"—":n.toLocaleString("pl-PL");
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
  if(!r.ok)throw Error(body.error||"Nie udało się wczytać obszarów.");
  return body;
}
function post(url:string,body:unknown,signal?:AbortSignal){return request({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal},url);}

export default function HuntAreas({initialFloor:playerFloor,onOpen,onClose,state,player}:{initialFloor:number;onOpen:(p:Position)=>void;onClose:()=>void;state?:BotState|null;player?:Position|null}){
  const [initialFloor]=useState(playerFloor);
  const [data,setData]=useState<Catalogue|null>(null),[key,setKey]=useState(""),[copy,setCopy]=useState<Area|null>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const [showMonsters,setShowMonsters]=useState(true),[species,setSpecies]=useState("");
  const [layer,setLayer]=useState<Observation["kind"]|"spawns"|"live">("spawns");
  const kind=layer==="spawns"||layer==="live"?"first_seen":layer;
  const [focus,setFocus]=useState<GroundLayer["focus"]>(null);
  const [route,setRoute]=useState<Circuit|null>(null),[approach,setApproach]=useState<Position[]>([]);
  const [savedDraft,setSavedDraft]=useState(""),[routeError,setRouteError]=useState("");
  const [planning,setPlanning]=useState("");
  const [selection,setSelection]=useState<Position[]|null>(null),[selecting,setSelecting]=useState(false);
  const [previousSelection,setPreviousSelection]=useState<Position[]|null>(null);
  const baseEntrance=useRef<Position|null>(null);
  const previewRequest=useRef<AbortController|null>(null);
  useEffect(()=>()=>previewRequest.current?.abort(),[]);
  const createKey=useRef("");
  useEffect(()=>{
    const ctl=new AbortController();setError("");
    request({signal:ctl.signal}).then((info:Catalogue)=>{
      if(ctl.signal.aborted)return;
      setData(info);
      const saved=info.discoveries?.[0]??info.areas[0],draft=info.drafts.find(d=>d.latest)??info.drafts[0];
      if(saved||draft){const area=saved??proposal(draft);setKey(saved?.id??"draft:"+draft.id);setFocus(center(area,initialFloor));}
    }).catch(e=>{if(!ctl.signal.aborted)setError(e.message);});
    return ()=>ctl.abort();
  },[initialFloor,reload]);
  const scope=data?.scope;
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
      finally{loading=false;}
    }
    void refreshDiscoveries();
    const interval=setInterval(refreshDiscoveries,20000);
    return ()=>{ctl.abort();clearInterval(interval);};
  },[scope]);
  const choices=useMemo(()=>{
    const all=new Map<string,Area>();
    for(const a of data?.areas??[])if(a.id)all.set(a.id,a);
    for(const a of data?.discoveries??[])all.set(a.id,a);
    for(const d of data?.drafts??[])if(!d.archived)all.set("draft:"+d.id,proposal(d));
    return all;
  },[data]);
  const draft=key.startsWith("draft:")?data?.drafts.find(d=>"draft:"+d.id===key):undefined;
  const terrain=draft?.terrain;
  const source=terrain?choices.get(terrain.ground):undefined;
  const sourceCells=useMemo(()=>new Set(source?.cells.map(p=>p.join(","))),[source]);
  const hasSource=!!source&&!!terrain&&terrain.sectors.every(p=>sourceCells.has(p.join(",")));
  const planKey=hasSource?terrain!.ground:key;
  const base=choices.get(key);
  const area=copy??(hasSource&&base?{...base,cells:source!.cells}:base);
  const selected=selection??terrain?.sectors??[];
  const floors=[...new Set(selected.map(p=>p[2]))].sort((a,b)=>a-b);
  const discovery=data?.discoveries?.find(d=>d.id===key);
  useEffect(()=>{
    const first=data?.discoveries?.[0];
    if(first&&!choices.has(key)&&!copy&&!planning){
      setKey(first.id);setLayer("spawns");setSpecies("");setFocus(center(first,initialFloor));resetRoute();
    }
  },[data?.discoveries,choices,key,copy,planning,initialFloor]);
  const linked=useMemo(()=>data?.drafts.filter(d=>area?.drafts.includes(d.id))??[],[area?.drafts,data]);
  const cells=useMemo(()=>new Set(area?.cells.map(p=>p.join(","))),[area?.cells]);
  const side=data?.sector_size??8;
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
      const line=k.name+(kind==="first_seen"?"":k.at_monster?" · pozycja potwora":" · okolica postaci");
      item.lines.set(line,(item.lines.get(line)??0)+k.count);
      locations.set(loc,item);
    }
    if(layer==="spawns"){
      const witnesses=(discovery?.witnesses??linked.flatMap(d=>d.witnesses??[])).filter(g=>cells.has(g.observer_cell.join(",")));
      const marks=spawnAreas(filtered,witnesses,!species).map(s=>{
        const title=[s.sightings===1?"Pojedyncza obserwacja":"Przybliżony spawn",
          ...Array.from(s.species).sort(([,a],[,b])=>b.sightings-a.sightings).map(([n,v])=>
            n+" · pierwsze wykrycia: "+v.sightings+(v.minimum?" · jednocześnie co najmniej "+v.minimum:""))].join("\n");
        return {position:s.position,exact:true,radius:s.radius,uncertain:s.sightings===1,minimum:s.minimum??undefined,title};
      });
      return {names,marks,habitats:species?sectorHabitats(filtered,side).habitats:undefined};
    }
    return {names,habitats:species&&layer==="first_seen"?sectorHabitats(filtered,side).habitats:undefined,marks:Array.from(locations.values(),v=>({position:v.position,exact:kind==="first_seen",
      title:[...(kind==="first_seen"?["Pierwsze wykrycie ("+v.position.join(", ")+")"]:[]),
        ...Array.from(v.lines,([name,count])=>name+" ×"+count)].join("\n")}))};
  },[cells,linked,species,kind,layer,discovery,side]);
  const applied=useMemo(()=>new Set(route?.selected.map(p=>p.join(","))),[route]);
  const dirty=!!route&&(selected.length!==applied.size||selected.some(p=>!applied.has(p.join(","))));
  const check=route&&!dirty?route:discovery?.check;
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
  function pick(k:string){
    const a=choices.get(k);if(!a)return;
    createKey.current="";
    resetRoute();
    setKey(k);setCopy(null);setSpecies("");setLayer("spawns");setError("");setFocus(center(a,initialFloor));
  }
  function resetRoute(){
    setRoute(null);setApproach([]);setSavedDraft("");setRouteError("");setSelection(null);setPreviousSelection(null);setSelecting(false);baseEntrance.current=null;
  }
  function selectSectors(a:Position,b:Position){
    if(planning||!selecting||!area||a[2]!==b[2])return;
    const start=[Math.floor(a[0]/side)*side,Math.floor(a[1]/side)*side,a[2]].join(",");
    const next=new Map(selected.map(p=>[p.join(","),p]));
    const remove=next.has(start);
    for(const p of area.cells){
      if(p[2]!==a[2]||p[0]>Math.max(a[0],b[0])||p[0]+side<=Math.min(a[0],b[0])||
          p[1]>Math.max(a[1],b[1])||p[1]+side<=Math.min(a[1],b[1]))continue;
      if(remove)next.delete(p.join(","));else next.set(p.join(","),p);
    }
    if(next.size!==selected.length)changeSelection([...next.values()]);
  }
  function changeSelection(next:Position[]){
    setPreviousSelection(selected);setSelection(next);setRouteError("");setApproach([]);
  }
  async function preview(from?:Position){
    if(!data||!selected.length||busy||planning||copy||(from&&(!route||dirty)))return;
    setPlanning(from?"Wyznaczanie dojścia…":"Przeliczanie pętli…");setRouteError("");
    setApproach([]);
    const ctl=new AbortController();previewRequest.current=ctl;
    try{
      const anchor=baseEntrance.current;
      const keepAnchor=anchor&&selected.some(([x,y,z])=>z===anchor[2]&&x<=anchor[0]&&anchor[0]<x+side&&y<=anchor[1]&&anchor[1]<y+side);
      let result=await post(endpoint+"/route",{scope:data.scope,ground:planKey,selected,
        ...(keepAnchor?{loop_start:anchor}:{}),...(from?{approach:from,entrance:route!.entrance}:{})},ctl.signal);
      while(result.ticket){
        await new Promise(resolve=>setTimeout(resolve,350));
        result=await post(endpoint+"/route",{scope:data.scope,ticket:result.ticket},ctl.signal);
      }
      if(from)setApproach(result.path);
      else {
        baseEntrance.current=result.entrance;
        setRoute(result);setSelecting(false);setSavedDraft("");
      }
    }catch(e){if(!ctl.signal.aborted)setRouteError(e instanceof Error?e.message:"Nie udało się wyznaczyć trasy.");}
    finally{if(!ctl.signal.aborted)setPlanning("");}
  }
  async function saveCircuit(){
    if(!route||!area||!data||planning||savedDraft||dirty)return;
    setPlanning("Zapisywanie szkicu…");setRouteError("");
    try{
      const result=await post("http://127.0.0.1:8089/api/map/drafts",{scope:data.scope,
        name:(area.name.slice(0,65)+" · pętla atlasu"),points:route.points,links:route.links,loop:true,terrain:{ground:planKey,sectors:route.selected}});
      const saved=result.drafts?.find((d:Draft)=>d.id===result.saved_id);
      if(saved)setData(old=>old?{...old,drafts:[{id:saved.id,name:saved.name,terrain:saved.terrain,archived:false,
        laps:0,duration_ms:0,blocked_ms:0,xp_hour:null,senzu_hour:null,latest:null},...old.drafts.filter(d=>d.id!==saved.id)]}:old);
      setSavedDraft(result.saved_id);
    }catch(e){setRouteError(e instanceof Error?e.message:"Nie udało się zapisać szkicu.");}
    finally{setPlanning("");}
  }
  async function editCircuit(draft:string){
    try{
      if(!window.dbvEditor)throw Error("Szkic możesz otworzyć w panelu Cavebota.");
      if(!await window.dbvEditor.open(draft))setRouteError("Edytor jest już otwarty. Zamknij go przed edycją innego szkicu.");
    }catch(e){setRouteError(e instanceof Error?e.message:"Nie udało się otworzyć edytora.");}
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
      setKey(id);setCopy(null);
    }catch(e){setError(e instanceof Error?e.message:"Nie udało się zapisać obszaru.");}
    finally{setBusy(false);}
  }
  return <div className="hunt-atlas-layout">
    <WorldMap initialFloor={initialFloor} player={player} onOpen={onOpen} loadingLabel={planning||undefined}
      habitats={showMonsters?observations.habitats:undefined} habitatSide={side} habitatName={showMonsters&&observations.habitats?species:""}
      toolbar={<button disabled={!!copy||busy||!!planning} onClick={onClose}>← Świat</button>}
      ground={{cells:area?.cells??[],marks:!showMonsters?[]:layer==="live"?(live?.marks??[]).map(m=>({position:m.position,exact:true,live:true,title:"Widoczne potwory: "+m.count})):observations.marks,side,editing:!!copy,toggle,focus,
        route:dirty?undefined:route?.path,approach,missing:dirty?undefined:route?.missing,selected:copy?undefined:selected,selecting,selectSectors,
        planApproach:route&&!planning&&!dirty&&!selecting?preview:undefined}}/>
    <aside className="hunt-area-panel">
      <h2>Expowiska</h2>
      {error&&<p role="alert">{error} {!copy&&<button onClick={()=>setReload(v=>v+1)}>Wczytaj ponownie</button>}</p>}
      {!data&&!error&&<p role="status" className="atlas-loading"><i className="atlas-spinner"/>Wczytywanie…</p>}
      {data&&<label><select aria-label="Obszar lub szkic" disabled={!!copy||busy||!!planning} value={key} onChange={e=>pick(e.target.value)}>
        {!!data.discoveries?.length&&<optgroup label="Z ręcznej gry">{data.discoveries.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</optgroup>}
        {!!data.areas.length&&<optgroup label="Zapisane expowiska">{data.areas.map(a=><option key={a.id} value={a.id!}>{a.name}</option>)}</optgroup>}
        <optgroup label="Propozycje ze szkiców">{data.drafts.filter(d=>!d.archived).map(d=><option key={d.id} value={"draft:"+d.id}>{d.name}</option>)}</optgroup>
      </select></label>}
      {data&&!area&&<p>Expowiska pojawią się tutaj podczas ręcznej gry, gdy atlas zarejestruje walki z przyrostem XP.</p>}
      {area&&<>
        {!copy&&!area.id&&!area.cells.length&&<p>Brak pełnego obiegu. Możesz zaznaczyć sektory ręcznie.</p>}
        {discovery?<div>
          <p>Wizyty: {discovery.visits} · {label(discovery.xp)} XP</p>
          <strong role="status">{check?.covered&&check.total
            ?(check.covered===check.total?"Propozycja pętli gotowa":`Pętla gotowa na części expowiska · ${check.covered}/${check.total} sektorów`)
            :discovery.check?.reason??(discovery.needs_recheck?"Możesz przeliczyć pętlę z zapisanej wizyty.":
              discovery.eligible?"Trwa sprawdzanie połączeń na mapie.":"Wróć do odwiedzonych sal lub na kolejną wizytę.")}</strong>
          {!!floorCoverage.length&&!route&&<details><summary>Poza pętlą: {check?.missing?.length} sektorów</summary>
            {floorCoverage.map(s=><small key={s.floor}>Piętro {s.floor} · w pętli {s.covered}/{s.total}</small>)}</details>}
        </div>:copy?<fieldset disabled={busy}>
          <label>Nazwa expowiska<input value={copy.name} maxLength={100} onChange={e=>setCopy({...copy,name:e.target.value})}/></label>
          <p>Kliknij sektor {side} × {side}, aby dodać go do obszaru lub usunąć. Przeciągnij mapę, aby ją przesunąć.</p>
          <details><summary>Przypisane szkice ({copy.drafts.length})</summary>{data?.drafts.filter(d=>!d.archived||choices.get(key)?.drafts.includes(d.id)).map(d=><label key={d.id}><input type="checkbox" checked={copy.drafts.includes(d.id)} onChange={e=>setCopy({...copy,drafts:e.target.checked?[...copy.drafts,d.id]:copy.drafts.filter(i=>i!==d.id)})}/>{d.name}{d.archived?" (kosz)":""}</label>)}</details>
          <button onClick={save} disabled={!copy.name.trim()||!copy.cells.length||!copy.drafts.length}>Zapisz obszar</button> <button onClick={()=>{setCopy(null);setError("");}}>Anuluj</button>
        </fieldset>:<div><button disabled={busy||!!planning} onClick={()=>{resetRoute();setCopy({...area});}}>{area.id?"Edytuj obszar":"Dostosuj granice"}</button> {!area.id&&<button disabled={busy||!!planning||!area.cells.length} onClick={save}>Zapisz obszar</button>}</div>}
        {!copy&&<section aria-label="Pętla expienia" className="hunt-section"><h3>Trasa</h3>
          {key.startsWith("draft:")&&<button disabled={!!planning} onClick={()=>editCircuit(key.slice(6))}>Edytuj szkic</button>}
          <div className="map-exclusions">
            <button disabled={!!planning||!area.cells.length} aria-pressed={selecting} onClick={()=>setSelecting(!selecting)}>Wybierz teren</button>
            <button disabled={!!planning||!area.cells.length} onClick={()=>changeSelection(area.cells)}>Całe expowisko</button>
            {!!selected.length&&<button disabled={!!planning} onClick={()=>changeSelection([])}>Wyczyść</button>}
            {previousSelection&&<button disabled={!!planning} onClick={()=>{setSelection(previousSelection);setPreviousSelection(null);setRouteError("");setApproach([]);}}>Cofnij wybór</button>}
            <small role="status">Wybrane sektory · {selected.length}{floors.length?" · piętra "+floors.join(", "):""}</small>
            {selecting&&<small>Kliknij sektor lub przeciągnij prostokąt. Zacznij na zielonym, aby odjąć. Ctrl + przeciągnięcie przesuwa mapę.</small>}
          </div>
          <button className="atlas-primary" disabled={!!planning||busy||!selected.length} onClick={()=>preview()}>{planning==="Przeliczanie pętli…"?planning:route?"Przelicz pętlę":"Zaproponuj pętlę"}</button>
          {routeError&&<p role="alert">{routeError}</p>}
          {dirty&&<small role="status">Wybór zmieniony. Przelicz pętlę przed zapisem.</small>}
          {route&&!dirty&&<><p>Pokrycie {route.covered}/{route.total} sektorów · {route.path.length-1} kroków · przejścia: {route.transitions}</p>
            {!!route.missing.length&&<details><summary>Bez dojścia i powrotu ({route.missing.length})</summary>
              {route.missing.map(p=><button key={p.join(",")} onClick={()=>setFocus({floor:p[2],cells:[p]})}>{p.join(", ")}</button>)}</details>}
            {!!approach.length&&<small>Dojście · {approach.length-1} kroków</small>}
            <button disabled={!!planning||dirty} onClick={()=>savedDraft?editCircuit(savedDraft):saveCircuit()}>{savedDraft?"Otwórz zapisany szkic":route.missing.length?"Zapisz dostępną część jako szkic":"Zapisz jako nowy szkic"}</button>
          </>}
        </section>}
        {!discovery&&linked.length>0&&<details className="hunt-results"><summary>Wyniki obiegów</summary>
        {linked.map(d=><div key={d.id}><strong>{d.name}</strong>{d.laps?<dl>
          <dt>Obiegi</dt><dd>{d.laps}</dd><dt>Średni obieg</dt><dd>{duration(d.duration_ms/d.laps)}</dd>
          <dt>XP/h</dt><dd>{label(d.xp_hour)}</dd><dt>Senzu/h</dt><dd>{label(d.senzu_hour)}</dd>
          <dt>Blokady / obieg</dt><dd>{seconds(d.blocked_ms/d.laps)} s</dd></dl>:<p>Brak pełnych obiegów.</p>}</div>)}</details>}
        <h3>Potwory</h3><select aria-label="Rodzaj obserwacji" value={layer} onChange={e=>{setLayer(e.target.value as typeof layer);setSpecies("");}}>
          <option value="spawns">Przybliżone spawny</option><option value="live">Widoczne teraz</option><option value="first_seen">Pierwsze wykrycia na mapie</option>
          {!discovery&&<><option value="death">Pokonane potwory</option><option value="first_contact">Dołączenie do walki lub grupy</option></>}</select>
        <label><input type="checkbox" checked={showMonsters} onChange={e=>setShowMonsters(e.target.checked)}/> Pokaż na mapie</label>
        {layer==="live"?<p>{live?"Na widocznych polach expowiska: "+live.count+" · piętro "+live.floor+(live.unknown?" · nierozpoznane: "+live.unknown:""):"Brak świeżego widoku tego expowiska."}</p>:observations.names.size?<><select aria-label="Gatunek potwora" value={species} onChange={e=>setSpecies(e.target.value)}><option value="">Wszystkie gatunki</option>
          {Array.from(observations.names).sort(([a],[b])=>a.localeCompare(b)).map(([n,count])=><option key={n} value={n}>{layer==="spawns"?n:n+" ×"+count}</option>)}</select>
          {(kind==="death"||kind==="first_contact")&&<small>Ostatni pełny obieg.</small>}
        </>:<p>{kind==="first_seen"&&linked.some(d=>d.latest)&&!linked.some(d=>d.sighting_laps)
          ?"W tych obiegach nie zapisano pierwszych wykryć.":"Brak obserwacji w tym obszarze."}</p>}
      </>}
    </aside>
  </div>;
}
