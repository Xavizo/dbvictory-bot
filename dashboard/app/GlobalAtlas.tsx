"use client";
import {useCallback,useEffect,useRef,useState} from 'react';
import dynamic from 'next/dynamic';
import type {BotState} from '@/lib/api';
import type {WorldViewport} from './WorldMap';
import {useWorldZones} from '@/lib/useWorldZones';
import {atlasLocation,atlasPlayer} from '@/lib/atlasWildlife';
import AtlasPanel from './AtlasPanel';
import PassiveMapPanel from './PassiveMapPanel';
function MapLoading(){return <p role="status" className="atlas-loading"><i className="atlas-spinner"/>Wczytywanie atlasu…</p>;}
const WorldOverview=dynamic(()=>import('./WorldOverview'),{ssr:false,loading:MapLoading});
const HuntAreas=dynamic(()=>import('./HuntAreas'),{ssr:false,loading:MapLoading});
type MapTab='saved'|'live'|'world';
type Position=[number,number,number];

export default function GlobalAtlas({state,onRefresh,onClose}:{state:BotState|null;onRefresh:()=>void;onClose:()=>void}) {
  const [tab,setTab]=useState<MapTab>('world'),close=useRef<HTMLButtonElement>(null);
  const [selected,setSelected]=useState<Position>();
  const [grounds,setGrounds]=useState(false);
  const index=useWorldZones(tab==="world");
  const worldView=useRef<WorldViewport|null>(null);
  const [,expire]=useState(0);
  const captured=state?.passive_map?.captured_at;
  useEffect(()=>{
    if(tab!=='world'||!captured)return;
    const remaining=captured*1000+2001-Date.now();
    if(remaining<=0)return;
    const timer=setTimeout(()=>expire(n=>n+1),remaining);
    return ()=>clearTimeout(timer);
  },[tab,captured]);
  const player=atlasPlayer(state,Date.now());
  const location=atlasLocation(state,player)??selected??null, floor=location?.[2]??7;
  const openTile=useCallback((p:Position)=>{setSelected(p);setTab('saved');},[]);
  useEffect(()=>{const previous=document.activeElement;close.current?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.stopPropagation();onClose();}};window.addEventListener('keydown',key);return()=>{window.removeEventListener('keydown',key);if(previous instanceof HTMLElement)previous.focus();};},[onClose]);
  return <div className="global-atlas-dialog" role="dialog" aria-modal="true" aria-label="Atlas i mapa na żywo">
    <header><strong>Atlas</strong><div role="group" aria-label="Rodzaj mapy"><button aria-pressed={tab==='world'} onClick={()=>{setGrounds(false);setTab('world');}}>Świat</button><button aria-pressed={tab==='saved'} onClick={()=>setTab('saved')}>Zapisana mapa</button><button aria-pressed={tab==='live'} onClick={()=>setTab('live')}>Mapa na żywo</button></div><button ref={close} className="spell-remove" aria-label="Zamknij atlas" onClick={onClose}>×</button></header>
    <div className="global-atlas-content">{tab==='world'?(grounds?
      <HuntAreas world={index.zones} worldError={index.error} retryWorld={index.retry} viewport={worldView} startInWorld state={state} location={location} player={player} initialFloor={floor} onOpen={openTile} onClose={()=>setGrounds(false)}/>:
      <WorldOverview {...index} viewport={worldView} location={location} player={player} initialFloor={floor} onOpen={openTile} onGrounds={()=>setGrounds(true)}/>):
      tab==='live'?<PassiveMapPanel map={state?.passive_map} preview={state?.adaptive_cavebot?.map_preview}/>:
      <AtlasPanel state={state} active onRefresh={onRefresh} initialPosition={selected} browseOnly/>}</div>
  </div>;
}
