"use client";
import {memo, useEffect, useMemo, useRef, useState} from "react";
import type {PointerEvent as ReactPointerEvent, ReactNode} from "react";
import {authenticatedFetch} from "@/lib/auth";
import type {Habitat} from "@/lib/atlasWildlife";

export type Position = [number, number, number];
export type GroundLayer = {cells:Position[]; marks:{position:Position;title:string;exact:boolean;radius?:number;live?:boolean;uncertain?:boolean;minimum?:number}[]; side:number;
  route?:Position[]; approach?:Position[]; missing?:Position[]; planApproach?:(p:Position)=>void;
  selected?:Position[];selecting?:boolean;selectSectors?:(a:Position,b:Position)=>void;
  editing:boolean; toggle:(p:Position)=>void; focus:{floor:number;cells:Position[]}|null};
type Sector = {x:number; y:number; terrain?:string; atlas?:string};
type Floor = {floor:number; sectors:Sector[]};
type Camera = {x:number; y:number; width:number; height:number};

function fitCells(cells:Position[],floor:number,side:number):Camera|null{
  let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
  for(const [x,y,z] of cells)if(z===floor){
    left=Math.min(left,x);right=Math.max(right,x+side);top=Math.min(top,y);bottom=Math.max(bottom,y+side);
  }
  return Number.isFinite(left)?{x:left-side,y:top-side,width:right-left+2*side,height:bottom-top+2*side}:null;
}

function RouteLine({path,floor,color,dashed=false}:{path?:Position[];floor:number;color:string;dashed?:boolean}){
  const line=useMemo(()=>{
    let previous:Position|undefined;
    return (path??[]).map(p=>{
      const join=previous?.[2]===floor&&Math.max(Math.abs(previous[0]-p[0]),Math.abs(previous[1]-p[1]))<=1;
      previous=p;
      return p[2]===floor?(join?"L":"M")+(p[0]+.5)+" "+(p[1]+.5):"";
    }).join(" ");
  },[path,floor]);
  return line?<path d={line} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke"
    strokeDasharray={dashed?"5 3":undefined} strokeLinejoin="round" pointerEvents="none"/>:null;
}

const Sectors = memo(function Sectors({sectors, overlay}:{sectors:Sector[]; overlay:boolean}) {
  return <>{sectors.map(s => <g key={s.x+","+s.y}>
    {s.terrain && <image x={s.x} y={s.y} width={64} height={64} href={"data:image/png;base64,"+s.terrain}/>}
    {overlay && s.atlas && <image x={s.x} y={s.y} width={64} height={64} opacity={.48} href={"data:image/png;base64,"+s.atlas}/>}
  </g>)}</>;
});

type Props={initialFloor:number;onOpen:(p:Position)=>void;ground?:GroundLayer;toolbar?:ReactNode;
  player?:Position|null;habitats?:Habitat[];habitatName?:string;habitatSide?:number;loadingLabel?:string};
export default memo(function WorldMap({initialFloor,onOpen,ground,toolbar,player,habitats,habitatName,habitatSide=8,loadingLabel}:Props) {
  const [floor,setFloor]=useState(initialFloor), [retry,setRetry]=useState(0);
  const [result,setResult]=useState<Floor|null>(null), [error,setError]=useState("");
  const [overlay,setOverlay]=useState(true), [grid,setGrid]=useState(false);
  const [following,follow]=useState(!ground),[layers,setLayers]=useState(false);
  const layerMenu=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!layers)return;
    function outside(e:PointerEvent){if(!layerMenu.current?.contains(e.target as Node))setLayers(false);}
    document.addEventListener("pointerdown",outside);
    return ()=>document.removeEventListener("pointerdown",outside);
  },[layers]);
  const playerFloor=player?.[2];
  useEffect(()=>{if(following&&playerFloor!==undefined)setFloor(playerFloor);},[following,playerFloor]);
  const [camera,setCamera]=useState<Camera|null>(null), [selection,select]=useState<Position|null>(null);
  const svg=useRef<SVGSVGElement>(null);
  const drag=useRef<{camera:Camera; matrix:DOMMatrix; x:number; y:number; moved:boolean;box:boolean;panOnly:boolean}|null>(null);
  const [box,setBox]=useState<{x:number;y:number;width:number;height:number}|null>(null);
  useEffect(()=>{
    const controller=new AbortController();
    setResult(null);setError("");select(null);
    async function load() {
      const r=await authenticatedFetch("http://127.0.0.1:8089/api/map/world?z="+floor,{signal:controller.signal});
      const data=await r.json();
      if(!r.ok)throw Error(data.error || "Nie udało się wczytać minimapy.");
      if(!controller.signal.aborted)setResult(data);
    }
    void load().catch(e=>{if(!controller.signal.aborted)setError(e.message);});
    return ()=>controller.abort();
  },[floor,retry]);
  const focus=ground?.focus,side=ground?.side??8;
  useEffect(()=>{
    if(!focus)return;
    follow(false);
    setFloor(focus.floor);
    setCamera(fitCells(focus.cells,focus.floor,side));
  },[focus,side]);
  const bounds=useMemo(()=>{
    if(!result?.sectors.length)return null;
    let x=Infinity,y=Infinity,right=-Infinity,bottom=-Infinity;
    for(const s of result.sectors){x=Math.min(x,s.x);y=Math.min(y,s.y);right=Math.max(right,s.x+64);bottom=Math.max(bottom,s.y+64);}
    return {x:x-4,y:y-4,width:right-x+8,height:bottom-y+8};
  },[result]);
  const view=following&&player&&player[2]===floor?
    {width:camera?.width??100,height:camera?.height??76,x:player[0]+.5-(camera?.width??100)/2,y:player[1]+.5-(camera?.height??76)/2}:camera??bounds;
  const here=selection&&habitats?.find(h=>h.position[2]===selection[2]&&h.position[0]===Math.floor(selection[0]/habitatSide)*habitatSide&&h.position[1]===Math.floor(selection[1]/habitatSide)*habitatSide);
  function showHabitat(){
    if(!habitats?.length)return;
    const points=habitats.map(h=>h.position);
    const z=points.some(p=>p[2]===floor)?floor:points[0][2];
    follow(false);setFloor(z);setCamera(fitCells(points,z,habitatSide));
  }
  function zoom(factor:number, point?:DOMPoint) {
    if(!view||!bounds)return;
    const width=Math.min(bounds.width*2,Math.max(16,view.width*factor));
    const ratio=width/view.width;
    const x=point?.x??view.x+view.width/2,y=point?.y??view.y+view.height/2;
    follow(false);setCamera({x:x+(view.x-x)*ratio,y:y+(view.y-y)*ratio,width,height:view.height*ratio});
  }
  function point(x:number,y:number) {
    const m=svg.current?.getScreenCTM();
    return m?new DOMPoint(x,y).matrixTransform(m.inverse()):null;
  }
  function release(e:ReactPointerEvent<SVGSVGElement>){
    const d=drag.current;if(!d)return;
    const p=point(e.clientX,e.clientY);
    if(p&&d.box&&ground?.selectSectors){
      const a=new DOMPoint(d.x,d.y).matrixTransform(d.matrix);
      ground.selectSectors([Math.floor(a.x),Math.floor(a.y),floor],[Math.floor(p.x),Math.floor(p.y),floor]);
    }else if(p&&!d.moved&&!d.panOnly&&p.x>=0&&p.x<65535&&p.y>=0&&p.y<65535){
      const pos:Position=[Math.floor(p.x),Math.floor(p.y),floor];
      if(ground?.editing)ground.toggle(pos);else select(pos);
    }
    drag.current=null;setBox(null);
  }
  return <section className="world-atlas" aria-busy={!!loadingLabel||(!result&&!error)}>
    <div className="world-atlas-tools">
      {toolbar}
      {habitatName&&!!habitats?.length&&<button className="habitat-key" onClick={showHabitat} title={"Pokaż sektory: "+habitatName}>Sektory · {habitats.length}</button>}
      <div className="world-map-navigation">
        <label>Piętro <select aria-label="Piętro mapy" value={floor} onChange={e=>{
          const z=Number(e.target.value);follow(false);setFloor(z);setCamera(ground?fitCells(ground.cells,z,side):null);
        }}>{Array.from({length:16},(_,z)=><option key={z}>{z}</option>)}</select></label>
        <div className="map-zoom"><button disabled={!view} aria-label="Oddal mapę świata" onClick={()=>zoom(2)}>−</button>
          <button disabled={!view} aria-label="Przybliż mapę świata" onClick={()=>zoom(.5)}>+</button></div>
        <button disabled={!player} aria-pressed={following&&!!player} title={player?"Wyśrodkuj i śledź · "+player.join(", "):"Brak aktualnej pozycji"} onClick={()=>{if(player){setFloor(player[2]);setCamera(null);follow(true);}}}>◎ Moja pozycja</button>
        <div className="map-layer-menu" ref={layerMenu} onKeyDown={e=>{if(e.key==="Escape"&&layers){e.stopPropagation();setLayers(false);layerMenu.current?.querySelector("button")?.focus();}}}>
          <button aria-expanded={layers} onClick={()=>setLayers(!layers)}>Warstwy</button>
          {layers&&<div><label><input type="checkbox" checked={overlay} onChange={e=>setOverlay(e.target.checked)}/> Zbadane pola</label>
            <label><input type="checkbox" checked={grid} onChange={e=>setGrid(e.target.checked)}/> Siatka 64 × 64</label>
            <button onClick={()=>{follow(false);setCamera(null);setLayers(false);}}>Pokaż całe piętro</button></div>}
        </div>
      </div>
    </div>
    {error?<div role="status">{error} <button onClick={()=>setRetry(v=>v+1)}>Ponów</button></div>
      :!result?<p role="status" className="atlas-loading"><i className="atlas-spinner"/>Wczytywanie piętra…</p>:!view?<p>Brak zapisanych pól na tym piętrze.</p>:
    <svg ref={svg} className={ground?.selecting?"select-sectors":undefined} aria-label="Mapa świata DBVictory" viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
      onWheel={e=>{const p=point(e.clientX,e.clientY);if(p)zoom(e.deltaY>0?1.25:.8,p);}}
      onPointerDown={e=>{const m=svg.current?.getScreenCTM();if(!m||(e.button!==0&&e.button!==1)||!e.isPrimary)return;
        const panOnly=e.button===1||e.altKey||e.ctrlKey;
        e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);
        drag.current={camera:view,matrix:m.inverse(),x:e.clientX,y:e.clientY,moved:false,box:!!ground?.selecting&&!panOnly,panOnly};}}
      onPointerMove={e=>{const d=drag.current;if(!d)return;
        if(!d.moved && Math.hypot(e.clientX-d.x,e.clientY-d.y)<4)return;
        d.moved=true;const a=new DOMPoint(d.x,d.y).matrixTransform(d.matrix),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(d.matrix);
        if(d.box){const left=Math.floor(Math.min(a.x,b.x)/side)*side,top=Math.floor(Math.min(a.y,b.y)/side)*side;
          setBox({x:left,y:top,width:(Math.floor(Math.max(a.x,b.x)/side)+1)*side-left,height:(Math.floor(Math.max(a.y,b.y)/side)+1)*side-top});return;}
        follow(false);
        setCamera({...d.camera,x:d.camera.x+a.x-b.x,y:d.camera.y+a.y-b.y});}}
      onPointerUp={release} onPointerCancel={()=>{drag.current=null;setBox(null);}}>
      <defs><pattern id="world-sector-grid" x={0} y={0} width={64} height={64} patternUnits="userSpaceOnUse">
        <path d="M64 0H0V64" fill="none" stroke="white" strokeOpacity={.55} vectorEffect="non-scaling-stroke"/></pattern></defs>
      <Sectors sectors={result.sectors} overlay={overlay}/>
      {ground&&<g fill="#ffbc61" fillOpacity=".31" stroke="#ffd497" strokeWidth=".2">{ground.cells.filter(p=>p[2]===floor).map(([x,y])=>
        <rect key={x+','+y} x={x} y={y} width={side} height={side}/>)}</g>}
      {ground?.selected&&<g aria-label="Wybrany teren" fill="#29b86a" fillOpacity=".54" stroke="#a4ffc3" strokeWidth=".38" pointerEvents="none">
        {ground.selected.filter(p=>p[2]===floor).map(([x,y])=><rect key={x+","+y} x={x} y={y} width={side} height={side}/>)}</g>}
      {habitats&&<g fill="#ad85f2" fillOpacity=".35" stroke="#e8cbff" strokeWidth=".56">{habitats.filter(h=>h.position[2]===floor).map(h=>
        <rect key={h.position.join(",")} x={h.position[0]} y={h.position[1]} width={habitatSide} height={habitatSide}><title>{h.species.map(([n,k])=>n+" · wykrycia: "+k).join("\n")}</title></rect>)}</g>}
      {ground&&<g>{ground.marks.filter(m=>m.position[2]===floor).map(m=>
        <g key={m.position.join(',')} transform={`translate(${m.position[0]+(m.exact ? .5 : side/2)} ${m.position[1]+(m.exact ? .5 : side/2)})`}>
          <title>{m.title}</title>
          {m.radius!==undefined&&<circle r={Math.max(1.4,m.radius+.5)} fill="#a780ee" fillOpacity=".16" stroke="#eadbff" strokeOpacity=".68" strokeWidth="1" strokeDasharray={m.uncertain?"2 3":undefined} vectorEffect="non-scaling-stroke"/>}
          <circle r={m.radius!==undefined ? .66 : m.exact ? 1 : side/4} fill={m.live?"#5df0bd":"#eee6ff"} stroke={m.live?"#144937":"#4f2872"} strokeWidth=".4"/>
          {m.minimum&&<text x=".95" y="-.5" fontSize="1.7" fill="white" stroke="#30203c" strokeWidth=".32" paintOrder="stroke">{m.minimum}+</text>}
        </g>)}</g>}
      {ground?.missing?.filter(p=>p[2]===floor).map(([x,y])=><rect key={x+","+y} x={x} y={y} width={side} height={side}
        fill="#242734" fillOpacity=".28" stroke="#c0b7c6" strokeOpacity=".7" strokeDasharray="1 1" strokeWidth=".3" pointerEvents="none"><title>Sektor poza wyznaczoną pętlą</title></rect>)}
      <RouteLine path={ground?.route} floor={floor} color="#effff7"/>
      <RouteLine path={ground?.approach} floor={floor} color="#73bfff" dashed/>
      {ground?.route?.[0]?.[2]===floor&&<circle cx={ground.route[0][0]+.5} cy={ground.route[0][1]+.5} r="1.2"
        fill="#173a34" stroke="#59f1bc" strokeWidth=".4"><title>Początek pętli expienia</title></circle>}
      {grid&&<rect x={view.x} y={view.y} width={view.width} height={view.height} fill="url(#world-sector-grid)" pointerEvents="none"/>}
      {box&&<rect {...box} fill="#29b86a" fillOpacity=".25" stroke="#a4ffc3" strokeWidth="2" vectorEffect="non-scaling-stroke" pointerEvents="none"/>}
      {selection&&<circle cx={selection[0]+.5} cy={selection[1]+.5} r={Math.max(.8,view.width/190)} fill="none" stroke="white" strokeWidth={2} vectorEffect="non-scaling-stroke"/>}
      {player&&player[2]===floor&&<g aria-label={"Twoja postać: "+player.join(", ")} transform={`translate(${player[0]+.5} ${player[1]+.5})`}>
        <title>Twoja postać · {player.join(", ")}</title>
        {([["#10252b",17],["#4af0d4",13],["#153b43",8],["#fff",4]] as const).map(([color,width])=>
          <path key={color} d="M0 0h.0001" fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" vectorEffect="non-scaling-stroke"/>)}</g>}
    </svg>}
    {loadingLabel&&<div role="status" className="atlas-loading map-work-indicator"><i className="atlas-spinner"/>{loadingLabel}</div>}
    {selection&&!ground?.editing&&!ground?.selecting&&<div className="map-selection">
      <span>{selection.join(", ")}</span>{here&&<span className="habitat-key">{here.species.map(([n])=>n).join(" · ")}</span>}
      <button onClick={()=>onOpen(selection)}>Otwórz pole</button>
      {ground?.planApproach&&<button onClick={()=>ground.planApproach?.(selection)}>Dojście stąd</button>}
      <button aria-label="Zamknij dane pola" onClick={()=>select(null)}>×</button></div>}
  </section>;
});
