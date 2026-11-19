"use client";
import {useCallback,useEffect,useRef,useState} from 'react';
import dynamic from 'next/dynamic';
import type {BotState} from '@/lib/api';
import AtlasPanel from './AtlasPanel';
import PassiveMapPanel from './PassiveMapPanel';
const WorldMap=dynamic(()=>import('./WorldMap'),{ssr:false});
type MapTab='saved'|'live'|'world';
type Position=[number,number,number];

export default function GlobalAtlas({state,onRefresh,onClose}:{state:BotState|null;onRefresh:()=>void;onClose:()=>void}) {
  const [tab,setTab]=useState<MapTab>('saved'),close=useRef<HTMLButtonElement>(null);
  const [selected,setSelected]=useState<Position>();
  const openTile=useCallback((p:Position)=>{setSelected(p);setTab('saved');},[]);
  useEffect(()=>{const previous=document.activeElement;close.current?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.stopPropagation();onClose();}};window.addEventListener('keydown',key);return()=>{window.removeEventListener('keydown',key);if(previous instanceof HTMLElement)previous.focus();};},[onClose]);
  return <div className="global-atlas-dialog" role="dialog" aria-modal="true" aria-label="Atlas i mapa na żywo">
    <header><strong>Atlas</strong><div role="group" aria-label="Rodzaj mapy"><button aria-pressed={tab==='world'} onClick={()=>setTab('world')}>Świat</button><button aria-pressed={tab==='saved'} onClick={()=>setTab('saved')}>Zapisana mapa</button><button aria-pressed={tab==='live'} onClick={()=>setTab('live')}>Mapa na żywo</button></div><button ref={close} className="spell-remove" aria-label="Zamknij atlas" onClick={onClose}>×</button></header>
    <div className="global-atlas-content">{tab==='world'?<WorldMap initialFloor={selected?.[2]??state?.passive_map?.center?.[2]??7} onOpen={openTile}/>:tab==='live'?<PassiveMapPanel map={state?.passive_map} preview={state?.adaptive_cavebot?.map_preview}/>:<AtlasPanel state={state} active onRefresh={onRefresh} initialPosition={selected} browseOnly/>}</div>
  </div>;
}
