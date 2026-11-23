"use client";
import {useMemo,useState} from "react";
import type {WorldZones} from "@/lib/useWorldZones";
import WorldMap,{type Position,type WorldViewport} from "./WorldMap";

export default function WorldOverview({initialFloor,player,location,viewport,onOpen,onGrounds,zones,error,retry}:{initialFloor:number;player:Position|null;
  location:Position|null;viewport:{current:WorldViewport|null};onOpen:(p:Position)=>void;onGrounds:()=>void;zones:WorldZones|null;error:string;retry:()=>void}){
  const [species,choose]=useState("");
  const names=useMemo(()=>[...new Set(zones?.habitats.flatMap(h=>h.species.map(([n])=>n)))].sort((a,b)=>a.localeCompare(b,"pl")),[zones]);
  const habitats=useMemo(()=>species?zones?.habitats.filter(h=>h.species.some(([n])=>n===species)):undefined,[zones,species]);
  return <WorldMap initialFloor={initialFloor} player={player} location={location} viewport={viewport} onOpen={onOpen}
    spawns={zones?.spawns} zones={zones??undefined} habitats={habitats} habitatSide={zones?.side} habitatName={species}
    loadingLabel={!zones&&!error?"Wczytywanie stref…":undefined}
    toolbar={<><button onClick={onGrounds}>Planuj pętlę</button>
      {!!names.length&&<select aria-label="Gatunek na mapie świata" value={species} onChange={e=>choose(e.target.value)}>
        <option value="">Wszystkie gatunki</option>{names.map(n=><option key={n}>{n}</option>)}</select>}
      {error&&<span role="alert">{error} <button onClick={retry}>Ponów</button></span>}</>}/>;
}
