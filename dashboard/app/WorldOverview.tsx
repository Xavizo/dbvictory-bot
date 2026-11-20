"use client";
import {useEffect,useMemo,useState} from "react";
import {authenticatedFetch} from "@/lib/auth";
import {sectorHabitats,type MapSighting} from "@/lib/atlasWildlife";
import WorldMap,{type Position} from "./WorldMap";

type ObservedArea={sightings?:MapSighting[]};
export default function WorldOverview({initialFloor,player,onOpen,onGrounds}:{initialFloor:number;player:Position|null;
  onOpen:(p:Position)=>void;onGrounds:()=>void}){
  const [enabled,enable]=useState(false),[species,choose]=useState("");
  const [sample,setSample]=useState<{rows:MapSighting[];side:number}|null>(null);
  const [loading,setLoading]=useState(false),[error,setError]=useState(""),[retry,setRetry]=useState(0);
  useEffect(()=>{
    if(!enabled)return;
    const ctl=new AbortController();
    async function load(){
      setLoading(true);setError("");
      async function read(path:string){
        const response=await authenticatedFetch("http://127.0.0.1:8089/api/map/"+path,{signal:ctl.signal});
        if(!response.ok)throw Error("Nie udało się wczytać występowania potworów.");
        return response.json();
      }
      try{
        const [catalogue,manual]=await Promise.all([read("grounds"),read("discoveries")]);
        if(catalogue.scope!==manual.scope)throw Error("Atlas zmienił się podczas odczytu.");
        if(!ctl.signal.aborted){
          const areas:ObservedArea[]=[...catalogue.drafts,...manual.discoveries];
          setSample({rows:areas.flatMap(a=>a.sightings??[]),side:catalogue.sector_size});
        }
      }catch(e){if(!ctl.signal.aborted){setSample(null);setError(e instanceof Error?e.message:"Błąd odczytu atlasu.");}}
      finally{if(!ctl.signal.aborted)setLoading(false);}
    }
    void load();return ()=>ctl.abort();
  },[enabled,retry]);
  const index=useMemo(()=>sectorHabitats(sample?.rows??[],sample?.side??8),[sample]);
  const habitats=useMemo(()=>index.habitats.filter(h=>!species||h.species.some(([n])=>n===species)),[index,species]);
  return <WorldMap initialFloor={initialFloor} player={player} onOpen={onOpen}
    habitats={enabled&&!loading?habitats:undefined} habitatSide={sample?.side} habitatName={enabled?species:""}
    loadingLabel={enabled&&loading?"Wczytywanie gatunków…":undefined}
    toolbar={<><button onClick={onGrounds}>Expowiska</button>
      <button aria-pressed={enabled} onClick={()=>{enable(!enabled);choose("");}}>Potwory</button>
      {enabled&&!loading&&(error?<span role="alert">{error} <button onClick={()=>setRetry(v=>v+1)}>Ponów</button></span>
        :index.names.length?<select aria-label="Gatunek na mapie świata" value={species} onChange={e=>choose(e.target.value)}>
          <option value="">Wszystkie gatunki</option>{index.names.map(n=><option key={n}>{n}</option>)}</select>
        :<small>Brak zapisanych wykryć.</small>)}</>}/>;
}
