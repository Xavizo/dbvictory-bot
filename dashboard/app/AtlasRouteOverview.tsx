"use client";
import {routeColor,routeLines,routeSegments,type Pos} from "@/lib/atlasModel";

export default function AtlasRouteOverview({segments,floor,onFloor,hidden=[]}:{segments:ReturnType<typeof routeSegments>;floor:number;onFloor:(points:Pos[])=>void;hidden?:number[]}) {
  const floors=[...new Set(segments.filter((_,i)=>!hidden.includes(i)).map(s=>s.floor))].sort((a,b)=>a-b);
  if(!segments.length)return null;
  return <div className="atlas-floor-overview" aria-label="Cała trasa — schemat wszystkich pięter">{floors.map(z=>{
    const parts=segments.map((s,i)=>({...s,index:i})).filter(s=>s.floor===z&&!hidden.includes(s.index)),points=parts.flatMap(s=>s.points);
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),minX=Math.min(...xs)-1,minY=Math.min(...ys)-1;
    const width=Math.max(4,Math.max(...xs)-minX+1),height=Math.max(4,Math.max(...ys)-minY+1);
    return <button key={z} aria-pressed={z===floor} onClick={()=>onFloor(points)} title={`Pokaż wszystkie odcinki na piętrze ${z}`}>
      <strong>{z}</strong><span>Odcinki {parts.map(s=>s.index+1).join(", ")}</span>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`Przebieg trasy na piętrze ${z}`}>
        {parts.map(s=><g key={s.index} stroke={routeColor(s.index)} fill={routeColor(s.index)}>
          {routeLines(s.points,z).map((line,i)=><polyline key={i} points={line.map(p=>`${p[0]-minX},${p[1]-minY}`).join(" ")} fill="none" strokeWidth={Math.max(width,height)*.012} strokeDasharray={s.index%2?`${Math.max(width,height)*.045} ${Math.max(width,height)*.018}`:undefined}/>)}
          <circle cx={s.points[0][0]-minX} cy={s.points[0][1]-minY} r={Math.max(width,height)*.025}/>
        </g>)}
      </svg>
    </button>;
  })}</div>;
}
