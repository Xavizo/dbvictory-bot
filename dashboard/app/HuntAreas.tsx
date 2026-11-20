"use client";
import {useEffect,useMemo,useRef,useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import WorldMap,{type GroundLayer,type Position} from "./WorldMap";

type Observation={kind:"death"|"first_contact"|"first_seen";cell:Position;name:string;count:number;at_monster:boolean;point?:Position;observer_cell?:Position};
type Draft={id:string;name:string;archived:boolean;laps:number;duration_ms:number;blocked_ms:number;xp_hour:number|null;senzu_hour:number|null;
  sightings?:Observation[];sighting_laps?:number;latest:{id:string;cells:Position[];observations:Observation[]}|null};
type Area={id:string|null;version?:number;name:string;cells:Position[];drafts:string[]};
type Catalogue={scope:string;sector_size:number;areas:Area[];drafts:Draft[]};
type Circuit={path:Position[];points:Position[];links:{from_index:number;id:string}[];entrance:Position;
  covered:number;total:number;missing:Position[];sightings:number;transitions:number};
const endpoint="http://127.0.0.1:8089/api/map/grounds";
const label=(n:number|null)=>n===null?"—":n.toLocaleString("pl-PL");
const seconds=(ms:number)=>Math.round(ms/1000);
function duration(ms:number){const s=seconds(ms);return Math.floor(s/60)+" min "+s%60+" s";}
function proposal(d:Draft):Area{return {id:null,name:d.name,cells:d.latest?.cells??[],drafts:[d.id]};}
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

export default function HuntAreas({initialFloor:playerFloor,onOpen,onClose}:{initialFloor:number;onOpen:(p:Position)=>void;onClose:()=>void}){
  const [initialFloor]=useState(playerFloor);
  const [data,setData]=useState<Catalogue|null>(null),[key,setKey]=useState(""),[copy,setCopy]=useState<Area|null>(null);
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[reload,setReload]=useState(0);
  const [showMonsters,setShowMonsters]=useState(true),[species,setSpecies]=useState("");
  const [kind,setKind]=useState<Observation["kind"]>("first_seen");
  const [focus,setFocus]=useState<GroundLayer["focus"]>(null);
  const [route,setRoute]=useState<Circuit|null>(null),[approach,setApproach]=useState<Position[]>([]);
  const [savedDraft,setSavedDraft]=useState(""),[routeError,setRouteError]=useState("");
  const [planning,setPlanning]=useState(false);
  const previewRequest=useRef<AbortController|null>(null);
  useEffect(()=>()=>previewRequest.current?.abort(),[]);
  const createKey=useRef("");
  useEffect(()=>{
    const ctl=new AbortController();setError("");
    request({signal:ctl.signal}).then((info:Catalogue)=>{
      if(ctl.signal.aborted)return;
      setData(info);
      const saved=info.areas[0],draft=info.drafts.find(d=>d.latest)??info.drafts[0];
      if(saved||draft){const area=saved??proposal(draft);setKey(saved?.id??"draft:"+draft.id);setFocus(center(area,initialFloor));}
    }).catch(e=>{if(!ctl.signal.aborted)setError(e.message);});
    return ()=>ctl.abort();
  },[initialFloor,reload]);
  const choices=useMemo(()=>{
    const all=new Map<string,Area>();
    for(const a of data?.areas??[])if(a.id)all.set(a.id,a);
    for(const d of data?.drafts??[])if(!d.archived)all.set("draft:"+d.id,proposal(d));
    return all;
  },[data]);
  const area=copy??choices.get(key);
  const linked=useMemo(()=>data?.drafts.filter(d=>area?.drafts.includes(d.id))??[],[area?.drafts,data]);
  const observations=useMemo(()=>{
    const cells=new Set(area?.cells.map(p=>p.join(",")));
    const names=new Map<string,number>(), locations=new Map<string,{position:Position;lines:Map<string,number>}>();
    for(const d of linked)for(const k of (kind==="first_seen"?d.sightings:d.latest?.observations)??[]){
      if(k.kind!==kind)continue;
      const origin=k.kind==="first_seen"?k.observer_cell:k.cell;
      const position=k.kind==="first_seen"?k.point:k.cell;
      if(!origin||!position||!cells.has(origin.join(",")))continue;
      names.set(k.name,(names.get(k.name)??0)+k.count);
      if(species&&species!==k.name)continue;
      const loc=position.join(",");
      const item=locations.get(loc)??{position,lines:new Map<string,number>()};
      const line=k.name+(kind==="first_seen"?"":k.at_monster?" · pozycja potwora":" · okolica postaci");
      item.lines.set(line,(item.lines.get(line)??0)+k.count);
      locations.set(loc,item);
    }
    return {names,marks:Array.from(locations.values(),v=>({position:v.position,exact:kind==="first_seen",
      title:[...(kind==="first_seen"?["Pierwsze wykrycie ("+v.position.join(", ")+")"]:[]),
        ...Array.from(v.lines,([name,count])=>name+" ×"+count)].join("\n")}))};
  },[area?.cells,linked,species,kind]);
  const side=data?.sector_size??8;
  const floors=Array.from(new Set(area?.cells.map(p=>p[2]))).sort((a,b)=>a-b);
  function pick(k:string){
    const a=choices.get(k);if(!a)return;
    createKey.current="";
    resetRoute();
    setKey(k);setCopy(null);setSpecies("");setError("");setFocus(center(a,initialFloor));
  }
  function resetRoute(){setRoute(null);setApproach([]);setSavedDraft("");setRouteError("");}
  async function preview(from?:Position){
    if(!data||busy||planning||copy||(!route&&from))return;
    setPlanning(true);setRouteError("");
    if(from)setApproach([]);else resetRoute();
    const ctl=new AbortController();previewRequest.current=ctl;
    try{
      let result=await post(endpoint+"/route",{scope:data.scope,ground:key,...(from?{approach:from,entrance:route!.entrance}:{})},ctl.signal);
      while(result.ticket){
        await new Promise(resolve=>setTimeout(resolve,350));
        result=await post(endpoint+"/route",{scope:data.scope,ticket:result.ticket},ctl.signal);
      }
      if(from)setApproach(result.path);
      else setRoute(result);
    }catch(e){if(!ctl.signal.aborted)setRouteError(e instanceof Error?e.message:"Nie udało się wyznaczyć trasy.");}
    finally{if(!ctl.signal.aborted)setPlanning(false);}
  }
  async function saveCircuit(){
    if(!route||!area||!data||planning||savedDraft)return;
    setPlanning(true);setRouteError("");
    try{
      const result=await post("http://127.0.0.1:8089/api/map/drafts",{scope:data.scope,
        name:(area.name.slice(0,65)+" · pętla atlasu"),points:route.points,links:route.links,loop:true});
      setSavedDraft(result.saved_id);
    }catch(e){setRouteError(e instanceof Error?e.message:"Nie udało się zapisać szkicu.");}
    finally{setPlanning(false);}
  }
  async function editCircuit(){
    try{
      if(!window.dbvEditor)throw Error("Szkic możesz otworzyć w panelu Cavebota.");
      if(!await window.dbvEditor.open(savedDraft))setRouteError("Edytor jest już otwarty. Zamknij go przed edycją innego szkicu.");
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
    <WorldMap initialFloor={initialFloor} onOpen={onOpen} toolbar={<button disabled={!!copy||busy||planning} onClick={onClose}>← Świat</button>}
      ground={{cells:area?.cells??[],marks:showMonsters?observations.marks:[],side,editing:!!copy,toggle,focus,
        route:route?.path,approach,missing:route?.missing,planApproach:route&&!planning?preview:undefined}}/>
    <aside className="hunt-area-panel">
      <h2>Expowiska</h2>
      {error&&<p role="alert">{error} {!copy&&<button onClick={()=>setReload(v=>v+1)}>Wczytaj ponownie</button>}</p>}
      {!data&&!error&&<p role="status">Wczytywanie obiegów…</p>}
      {data&&<label>Obszar lub szkic<select aria-label="Obszar lub szkic" disabled={!!copy||busy||planning} value={key} onChange={e=>pick(e.target.value)}>
        {!!data.areas.length&&<optgroup label="Zapisane expowiska">{data.areas.map(a=><option key={a.id} value={a.id!}>{a.name}</option>)}</optgroup>}
        <optgroup label="Propozycje ze szkiców">{data.drafts.filter(d=>!d.archived).map(d=><option key={d.id} value={"draft:"+d.id}>{d.name}</option>)}</optgroup>
      </select></label>}
      {data&&!area&&<p>Zapisz pierwszy szkic trasy, aby wyznaczyć expowisko.</p>}
      {area&&<>
        {!copy&&!area.id&&<p>{area.cells.length?"Propozycja z ostatniego pełnego obiegu.":"Brak pełnego obiegu. Możesz zaznaczyć obszar ręcznie."}</p>}
        {!!floors.length&&<div className="hunt-floors">Piętra {floors.map(z=><button key={z} onClick={()=>setFocus({floor:z,cells:area.cells})}>{z}</button>)}</div>}
        {copy?<fieldset disabled={busy}>
          <label>Nazwa expowiska<input value={copy.name} maxLength={100} onChange={e=>setCopy({...copy,name:e.target.value})}/></label>
          <p>Kliknij sektor {side} × {side}, aby dodać go do obszaru lub usunąć. Przeciągnij mapę, aby ją przesunąć.</p>
          <details><summary>Przypisane szkice ({copy.drafts.length})</summary>{data?.drafts.filter(d=>!d.archived||choices.get(key)?.drafts.includes(d.id)).map(d=><label key={d.id}><input type="checkbox" checked={copy.drafts.includes(d.id)} onChange={e=>setCopy({...copy,drafts:e.target.checked?[...copy.drafts,d.id]:copy.drafts.filter(i=>i!==d.id)})}/>{d.name}{d.archived?" (kosz)":""}</label>)}</details>
          <button onClick={save} disabled={!copy.name.trim()||!copy.cells.length||!copy.drafts.length}>Zapisz obszar</button> <button onClick={()=>{setCopy(null);setError("");}}>Anuluj</button>
        </fieldset>:<div><button disabled={busy||planning} onClick={()=>{resetRoute();setCopy({...area});}}>{area.id?"Edytuj obszar":"Dostosuj granice"}</button> {!area.id&&<button disabled={busy||planning||!area.cells.length} onClick={save}>Zapisz obszar</button>}</div>}
        {!copy&&<section aria-label="Pętla expienia"><h3>Pętla expienia</h3>
          <button disabled={planning||busy||!area.cells.length} onClick={()=>preview()}>{route?"Przelicz pętlę":"Zaproponuj pętlę"}</button>
          {planning&&<small role="status">Przetwarzanie…</small>}
          {routeError&&<p role="alert">{routeError}</p>}
          {route&&<><p>Pokrycie {route.covered}/{route.total} sektorów · {route.path.length-1} kroków · przejścia: {route.transitions}</p>
            <small>{route.sightings?"Pierwsze wykrycia potworów w obszarze: "+route.sightings:"Trasa według zbadanych pól. Brak pierwszych wykryć w granicach obszaru."}</small>
            {!!route.missing.length&&<details><summary>Pominięte sektory ({route.missing.length})</summary><p>Atlas nie potwierdza dojścia i powrotu. Wybierz sektor na mapie.</p>
              {route.missing.map(p=><button key={p.join(",")} onClick={()=>setFocus({floor:p[2],cells:[p]})}>{p.join(", ")}</button>)}</details>}
            <small>{approach.length?"Dojście: "+(approach.length-1)+" kroków · niebieska linia przerywana.":"Aby wyznaczyć dojście, kliknij pole na mapie i wybierz „Dojście stąd”."}</small>
            <button disabled={planning} onClick={savedDraft?editCircuit:saveCircuit}>{savedDraft?"Otwórz zapisany szkic":"Zapisz jako nowy szkic"}</button>
          </>}
        </section>}
        <h3>Wyniki pełnych obiegów</h3>
        {linked.map(d=><div key={d.id}><strong>{d.name}</strong>{d.laps?<dl>
          <dt>Obiegi</dt><dd>{d.laps}</dd><dt>Średni obieg</dt><dd>{duration(d.duration_ms/d.laps)}</dd>
          <dt>XP/h</dt><dd>{label(d.xp_hour)}</dd><dt>Senzu/h</dt><dd>{label(d.senzu_hour)}</dd>
          <dt>Blokady / obieg</dt><dd>{seconds(d.blocked_ms/d.laps)} s</dd></dl>:<p>Brak pełnych obiegów.</p>}</div>)}
        <h3>Potwory</h3><select aria-label="Rodzaj obserwacji" value={kind} onChange={e=>{setKind(e.target.value as Observation["kind"]);setSpecies("");}}>
          <option value="first_seen">Pierwsze wykrycia na mapie</option><option value="death">Pokonane potwory</option><option value="first_contact">Dołączenie do walki lub grupy</option></select>
        <label><input type="checkbox" checked={showMonsters} onChange={e=>setShowMonsters(e.target.checked)}/> Pokaż na mapie</label>
        {observations.names.size?<><select aria-label="Gatunek potwora" value={species} onChange={e=>setSpecies(e.target.value)}><option value="">Wszystkie gatunki</option>
          {Array.from(observations.names).sort(([a],[b])=>a.localeCompare(b)).map(([n,count])=><option key={n} value={n}>{n} ×{count}</option>)}</select>
          <small>{kind==="first_seen"?"Pierwsze zaobserwowane pole każdego potwora ze wszystkich pełnych obiegów.":
            kind==="death"?"Ostatni pełny obieg. Starsze zapisy wskazują okolicę postaci w chwili pokonania.":"Miejsce kontaktu bojowego z ostatniego pełnego obiegu."}</small>
        </>:<p>{kind==="first_seen"&&linked.some(d=>d.latest)&&!linked.some(d=>d.sighting_laps)
          ?"W tych obiegach nie zapisano pierwszych wykryć.":"Brak obserwacji w tym obszarze."}</p>}
      </>}
    </aside>
  </div>;
}
