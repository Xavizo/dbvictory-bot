"use client";
import {useEffect,useRef,useState} from 'react';
import type {BotState} from '@/lib/api';
import AtlasPanel from './AtlasPanel';
import PassiveMapPanel from './PassiveMapPanel';

export default function GlobalAtlas({state,onRefresh,onClose}:{state:BotState|null;onRefresh:()=>void;onClose:()=>void}) {
  const [live,setLive]=useState(false),close=useRef<HTMLButtonElement>(null);
  useEffect(()=>{const previous=document.activeElement;close.current?.focus();const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.stopPropagation();onClose();}};window.addEventListener('keydown',key);return()=>{window.removeEventListener('keydown',key);if(previous instanceof HTMLElement)previous.focus();};},[onClose]);
  return <div className="global-atlas-dialog" role="dialog" aria-modal="true" aria-label="Atlas i mapa na żywo">
    <header><strong>Atlas</strong><div role="group" aria-label="Rodzaj mapy"><button aria-pressed={!live} onClick={()=>setLive(false)}>Zapisana mapa</button><button aria-pressed={live} onClick={()=>setLive(true)}>Mapa na żywo</button></div><button ref={close} className="spell-remove" aria-label="Zamknij atlas" onClick={onClose}>×</button></header>
    <div className="global-atlas-content">{live?<PassiveMapPanel map={state?.passive_map}/>:<AtlasPanel state={state} active onRefresh={onRefresh} browseOnly/>}</div>
  </div>;
}
