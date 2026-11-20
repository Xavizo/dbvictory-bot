import type {BotState} from "./api";

type Position=[number,number,number];
export type MapSighting={point?:Position;name:string;count:number};
export type Habitat={position:Position;species:[string,number][]};

export function atlasPlayer(state:BotState|null, now:number):Position|null {
  const map=state?.passive_map, p=map?.center;
  if(!state?.connected||map?.status!=="ready"||map.preview||map.stale||!map.captured_at||
     now<map.captured_at*1000||now-map.captured_at*1000>2000||!p||p.length!==3||
     !p.every(Number.isInteger)||p[0]<0||p[1]<0||(!p[0]&&!p[1])||p[2]<0||p[2]>15)return null;
  return p;
}

export function sectorHabitats(rows:MapSighting[],side:number){
  const sectors=new Map<string,{position:Position;counts:Map<string,number>}>();
  const names=new Set<string>();
  for(const {point,name,count} of rows){
    if(!point||!name||count<=0)continue;
    const p:Position=[Math.floor(point[0]/side)*side,Math.floor(point[1]/side)*side,point[2]];
    const key=p.join(",");
    const cell=sectors.get(key)??{position:p,counts:new Map<string,number>()};
    cell.counts.set(name,(cell.counts.get(name)??0)+count);
    sectors.set(key,cell);
    names.add(name);
  }
  const habitats:Habitat[]=Array.from(sectors.values(),v=>({position:v.position,
    species:[...v.counts].sort(([a,n],[b,m])=>m-n||a.localeCompare(b,"pl"))}));
  return {names:[...names].sort((a,b)=>a.localeCompare(b,"pl")),habitats};
}
