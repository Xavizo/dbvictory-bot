"use client";
import {memo, useEffect, useMemo, useRef, useState} from "react";
import type {PointerEvent as ReactPointerEvent, ReactNode} from "react";
import {authenticatedFetch} from "@/lib/auth";

export type Position = [number, number, number];
export type GroundLayer = {cells:Position[]; marks:{cell:Position;title:string}[]; side:number;
  editing:boolean; toggle:(p:Position)=>void; focus:{floor:number;cells:Position[]}|null};
type Sector = {x:number; y:number; terrain?:string; atlas?:string};
type Floor = {floor:number; sectors:Sector[]};
type Camera = {x:number; y:number; width:number; height:number};

const Sectors = memo(function Sectors({sectors, overlay}:{sectors:Sector[]; overlay:boolean}) {
  return <>{sectors.map(s => <g key={s.x+","+s.y}>
    {s.terrain && <image x={s.x} y={s.y} width={64} height={64} href={"data:image/png;base64,"+s.terrain}/>}
    {overlay && s.atlas && <image x={s.x} y={s.y} width={64} height={64} opacity={.48} href={"data:image/png;base64,"+s.atlas}/>}
  </g>)}</>;
});

export default memo(function WorldMap({initialFloor,onOpen,ground,toolbar}:{initialFloor:number;onOpen:(p:Position)=>void;ground?:GroundLayer;toolbar?:ReactNode}) {
  const [floor,setFloor]=useState(initialFloor), [retry,setRetry]=useState(0);
  const [result,setResult]=useState<Floor|null>(null), [error,setError]=useState("");
  const [overlay,setOverlay]=useState(true), [grid,setGrid]=useState(false);
  const [camera,setCamera]=useState<Camera|null>(null), [selection,select]=useState<Position|null>(null);
  const svg=useRef<SVGSVGElement>(null);
  const drag=useRef<{camera:Camera; matrix:DOMMatrix; x:number; y:number; moved:boolean}|null>(null);
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
    const points=focus.cells.filter(p=>p[2]===focus.floor);
    setFloor(focus.floor);
    if(!points.length){setCamera(null);return;}
    let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
    for(const [x,y] of points){left=Math.min(left,x);right=Math.max(right,x+side);top=Math.min(top,y);bottom=Math.max(bottom,y+side);}
    setCamera({x:left-side,y:top-side,width:right-left+2*side,height:bottom-top+2*side});
  },[focus,side]);
  const bounds=useMemo(()=>{
    if(!result?.sectors.length)return null;
    let x=Infinity,y=Infinity,right=-Infinity,bottom=-Infinity;
    for(const s of result.sectors){x=Math.min(x,s.x);y=Math.min(y,s.y);right=Math.max(right,s.x+64);bottom=Math.max(bottom,s.y+64);}
    return {x:x-4,y:y-4,width:right-x+8,height:bottom-y+8};
  },[result]);
  const view=camera??bounds;
  function zoom(factor:number, point?:DOMPoint) {
    if(!view||!bounds)return;
    const width=Math.min(bounds.width*2,Math.max(16,view.width*factor));
    const ratio=width/view.width;
    const x=point?.x??view.x+view.width/2,y=point?.y??view.y+view.height/2;
    setCamera({x:x+(view.x-x)*ratio,y:y+(view.y-y)*ratio,width,height:view.height*ratio});
  }
  function point(x:number,y:number) {
    const m=svg.current?.getScreenCTM();
    return m?new DOMPoint(x,y).matrixTransform(m.inverse()):null;
  }
  function release(e:ReactPointerEvent<SVGSVGElement>){
    if(!drag.current)return;
    const p=point(e.clientX,e.clientY);
    if(p&&!drag.current.moved&&p.x>=0&&p.x<65535&&p.y>=0&&p.y<65535){
      const pos:Position=[Math.floor(p.x),Math.floor(p.y),floor];
      if(ground?.editing)ground.toggle(pos);else select(pos);
    }
    drag.current=null;
  }
  return <section className="world-atlas">
    <div className="world-atlas-tools">
      {toolbar}
      <label>Piętro <select value={floor} onChange={e=>{setFloor(Number(e.target.value));if(!ground)setCamera(null);}}>{Array.from({length:16},(_,z)=><option key={z}>{z}</option>)}</select></label>
      <label><input type="checkbox" checked={overlay} onChange={e=>setOverlay(e.target.checked)}/> Pola zbadane przez atlas</label>
      <label><input type="checkbox" checked={grid} onChange={e=>setGrid(e.target.checked)}/> Siatka 64 × 64</label>
      <button disabled={!view} aria-label="Przybliż mapę świata" onClick={()=>zoom(.5)}>+</button>
      <button disabled={!view} aria-label="Oddal mapę świata" onClick={()=>zoom(2)}>−</button>
      <button disabled={!camera} onClick={()=>setCamera(null)}>Całe piętro</button>
      {selection&&!ground?.editing&&<button onClick={()=>onOpen(selection)}>Otwórz pole ({selection.join(", ")})</button>}
    </div>
    {error?<div role="status">{error} <button onClick={()=>setRetry(v=>v+1)}>Ponów</button></div>
      :!result?<p role="status">Wczytywanie minimapy…</p>:!view?<p>Brak zapisanych pól na tym piętrze.</p>:
    <svg ref={svg} aria-label="Mapa świata DBVictory" viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
      onWheel={e=>{const p=point(e.clientX,e.clientY);if(p)zoom(e.deltaY>0?1.25:.8,p);}}
      onPointerDown={e=>{const m=svg.current?.getScreenCTM();if(!m||e.button!==0)return;
        e.currentTarget.setPointerCapture(e.pointerId);drag.current={camera:view,matrix:m.inverse(),x:e.clientX,y:e.clientY,moved:false};}}
      onPointerMove={e=>{const d=drag.current;if(!d)return;
        if(!d.moved && Math.hypot(e.clientX-d.x,e.clientY-d.y)<4)return;
        d.moved=true;const a=new DOMPoint(d.x,d.y).matrixTransform(d.matrix),b=new DOMPoint(e.clientX,e.clientY).matrixTransform(d.matrix);
        setCamera({...d.camera,x:d.camera.x+a.x-b.x,y:d.camera.y+a.y-b.y});}}
      onPointerUp={release} onPointerCancel={()=>{drag.current=null;}}>
      <defs><pattern id="world-sector-grid" x={0} y={0} width={64} height={64} patternUnits="userSpaceOnUse">
        <path d="M64 0H0V64" fill="none" stroke="white" strokeOpacity={.55} vectorEffect="non-scaling-stroke"/></pattern></defs>
      <Sectors sectors={result.sectors} overlay={overlay}/>
      {ground&&<g fill="#ffbc61" fillOpacity=".31" stroke="#ffd497" strokeWidth=".2">{ground.cells.filter(p=>p[2]===floor).map(([x,y])=>
        <rect key={x+','+y} x={x} y={y} width={side} height={side}/>)}</g>}
      {ground&&<g>{ground.marks.filter(m=>m.cell[2]===floor).map(m=>
        <circle key={m.cell.join(',')} cx={m.cell[0]+side/2} cy={m.cell[1]+side/2} r={side/4} fill="#eee6ff" stroke="#4f2872" strokeWidth=".6"><title>{m.title}</title></circle>)}</g>}
      {grid&&<rect x={view.x} y={view.y} width={view.width} height={view.height} fill="url(#world-sector-grid)"/>}
      {selection&&<circle cx={selection[0]+.5} cy={selection[1]+.5} r={Math.max(.8,view.width/190)} fill="none" stroke="white" strokeWidth={2} vectorEffect="non-scaling-stroke"/>}
    </svg>}
  </section>;
});
