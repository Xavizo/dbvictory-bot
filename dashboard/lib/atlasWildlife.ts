import type {BotState} from "./api";

type Position=[number,number,number];
export type MapSighting={point?:Position;name:string;count:number};
export type Habitat={position:Position;species:[string,number][]};

function validPosition(p?:Position|null):p is Position {
  return !!p && p.length===3 && p.every(Number.isInteger) &&
    p[0]>=0 && p[0]<65535 && p[1]>=0 && p[1]<65535 && (p[0]!==0 || p[1]!==0) && p[2]>=0 && p[2]<=15;
}

// A stored position can centre the camera without advertising a live player marker.
export function atlasLocation(state:BotState|null, live:Position|null):Position|null {
  if(live)return live;
  if(!state||state.passive_map?.preview)return null;
  const p=state.player?.position;
  if(state.connected&&validPosition(p))return p;
  const last=state.passive_map?.center;
  return validPosition(last)?last:null;
}

export function localGround(choices:Map<string,{cells:Position[]}>,p:Position|null|undefined,side:number,activeDraft?:string) {
  if(!p||side<=0)return "";
  const x=Math.floor(p[0]/side)*side,y=Math.floor(p[1]/side)*side;
  let best="",size=Infinity,foundDiscovery=false;
  for(const [key,area] of choices){
    if(!area.cells.some(c=>c[0]===x&&c[1]===y&&c[2]===p[2]))continue;
    if(activeDraft&&key==="draft:"+activeDraft)return key;
    // Discoveries arrive newest first. An older overlapping visit can cover fewer rooms.
    if(key.startsWith("manual:")){
      if(foundDiscovery)continue;
      foundDiscovery=true;
    }
    if(area.cells.length<size){best=key;size=area.cells.length;}
  }
  return best;
}

export function atlasPlayer(state:BotState|null, now:number):Position|null {
  const map=state?.passive_map, p=map?.center;
  if(!state?.connected||map?.status!=="ready"||map.preview||map.stale||!map.captured_at||
     now<map.captured_at*1000||now-map.captured_at*1000>2000||!validPosition(p))return null;
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
