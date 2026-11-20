import type {BotState} from "./api";

type Point = [number, number, number];
export type Witness = {name:string; observer_cell:Point; points:Point[]};
type Sighting = {name:string; point?:Point; count:number};
type Tile = {position:Point; count:number; density:number};
const radius = 4;

export function spawnAreas(sightings:Sighting[], witnesses:Witness[], combineSpecies=false) {
  const species = new Map<string, Map<string, Tile>>();
  const countsByTile=new Map<string,Map<string,number>>();
  for (const {point,name,count} of sightings) {
    if (!point || count<=0) continue;
    const track=combineSpecies?"":name;
    const map = species.get(track) ?? new Map<string,Tile>();
    species.set(track,map);
    const key=point.join(",");
    const tile=map.get(key) ?? {position:point,count:0,density:0};
    tile.count+=count;
    map.set(key,tile);
    const counts=countsByTile.get(key)??new Map<string,number>();
    counts.set(name,(counts.get(name)??0)+count);
    countsByTile.set(key,counts);
  }
  const result:{position:Point; radius:number; name:string; sightings:number; minimum:number|null;
    species:Map<string,{sightings:number;minimum:number|null}>}[]=[];
  for (const [track,tiles] of species) {
    function nearby<T>(p:Point, map:Map<string,T>, visit:(item:T,distance:number)=>void) {
      for (let dx=-radius; dx<=radius; dx++) for (let dy=-radius; dy<=radius; dy++) {
        const distance=Math.hypot(dx,dy);
        if (distance>radius) continue;
        const item=map.get([p[0]+dx,p[1]+dy,p[2]].join(","));
        if (item!==undefined) visit(item,distance);
      }
    }
    for (const t of tiles.values()) nearby(t.position,tiles,(other,distance)=>{
      t.density+=other.count*(radius+1-distance);
    });
    const ordered=[...tiles.values()].sort((a,b)=>b.density-a.density || b.count-a.count ||
      a.position[2]-b.position[2] || a.position[1]-b.position[1] || a.position[0]-b.position[0]);
    const centers=new Map<string,number>(),assigned=new Map<string,number>();
    for(const t of ordered){
      let covered=false;
      nearby(t.position,centers,()=>{covered=true;});
      if(!covered){
        centers.set(t.position.join(","),result.length);
        result.push({position:t.position,radius:0,name:track,sightings:0,minimum:null,species:new Map()});
      }
    }
    for(const t of ordered){
      let best=-1,nearest=Infinity;
      nearby(t.position,centers,(index,distance)=>{
        if(distance<nearest || (distance===nearest && index<best)){nearest=distance;best=index;}
      });
      result[best].sightings+=t.count;
      result[best].radius=Math.max(result[best].radius,nearest);
      for(const [name,count] of countsByTile.get(t.position.join(","))!){
        if(!combineSpecies&&name!==track)continue;
        const item=result[best].species.get(name)??{sightings:0,minimum:null};
        item.sightings+=count;
        result[best].species.set(name,item);
      }
      assigned.set(t.position.join(","),best);
    }
    for(const frame of witnesses){
      if(!combineSpecies&&frame.name!==track)continue;
      const concurrent=new Map<number,number>();
      for(const p of frame.points){
        const index=assigned.get(p.join(","));
        if(index!==undefined)concurrent.set(index,(concurrent.get(index)??0)+1);
      }
      for(const [index,n] of concurrent){
        const item=result[index].species.get(frame.name);
        if(item&&n>1){
          item.minimum=Math.max(item.minimum??0,n);
          result[index].minimum=Math.max(result[index].minimum??0,n);
        }
      }
    }
  }
  return result;
}

export function visibleMonsters(state:BotState|null|undefined, cells:Set<string>, side:number, now:number){
  const map=state?.passive_map, position=state?.player?.position;
  if(!state?.connected || !map || map.status!=="ready" || map.preview || map.stale || !map.captured_at ||
     !position || !map.center?.every((v,i)=>v===position[i]) || now<map.captured_at*1000 || now-map.captured_at*1000>2000)return null;
  let count=0,unknown=0,observed=0;
  const marks:{position:Point;count:number}[]=[];
  for(const cell of map.cells){
    if(cell.z!==position[2] || !cells.has([Math.floor(cell.x/side)*side,Math.floor(cell.y/side)*side,cell.z].join(",")))continue;
    observed++;
    const types=cell.occupant_types;
    const n=types?.monster??0;
    unknown+=Math.max(types?.unknown??0,cell.occupants-n-(types?.player??0)-(types?.npc??0)-(types?.self??0)-(types?.pet??0),0);
    count+=n;
    if(n)marks.push({position:[cell.x,cell.y,cell.z],count:n});
  }
  return observed?{count,unknown,marks,floor:position[2]}:null;
}
