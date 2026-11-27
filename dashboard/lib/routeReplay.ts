import type {BotState} from "./api";

type Point = [number, number, number];
type MobCell = [number, number, number, number];
const point = (v:unknown):v is Point =>
  Array.isArray(v) && v.length===3 && v.every(Number.isInteger);
const waypointNumber = (index:number|null|undefined) =>
  typeof index==="number" && Number.isInteger(index) && index>=0 ? index+1 : null;

function capture(state:BotState|null, online:boolean, now:number) {
  const inGame=online && state?.connected===true;
  const position=inGame && point(state?.player?.position) ? [...state.player.position] as Point : null;
  const route=(online ? state?.adaptive_cavebot : null) as
    (NonNullable<BotState["adaptive_cavebot"]>&{blockage?:{target?:unknown}|null})|null|undefined;
  const group=inGame ? route?.combat?.lure : null;
  const level=state?.player?.level, experience=state?.player?.experience;
  const levelXp=(n:number)=>Math.floor(50*(n**3-6*n**2+17*n-12)/3);
  const xp=inGame && typeof level==="number" && level>0 && typeof experience==="number" &&
    (state?.player?.stats_age??-1)>=0 && experience>=levelXp(level) && experience<levelXp(level+1) ? experience:null;
  const map=state?.passive_map;
  const age=typeof map?.captured_at==="number" ? now-map.captured_at*1000 : null;
  const validMap=!!(position && point(map?.center) && map.center.every((v,i)=>v===position[i]) &&
    map.status==="ready" && map.cells.length>0 && !map.preview && !map.stale && age!==null && age>=0 && age<=2000);
  let monsters:MobCell[]|null=null;
  let unclassified:number|null=null;
  if(validMap && map && position) {
    monsters=[];
    unclassified=0;
    for(const cell of map.cells){
      if(cell.z!==position[2])continue;
      const types=cell.occupant_types??{};
      unclassified+=Math.max(types.unknown??0,(cell.occupants??0)-
        (types.player??0)-(types.self??0)-(types.npc??0)-(types.monster??0)-(types.pet??0),0);
      const n=cell.occupant_types?.monster;
      if(typeof n==="number" && Number.isInteger(n) && n>0)
        monsters.push([cell.x,cell.y,cell.z,n]);
    }
    monsters.sort((a,b)=>a[1]-b[1] || a[0]-b[0]);
  }
  return {
    connection:!online?"panel_offline":inGame?"in_game":"logged_out", position,
    route:route?.name??null, status:route?.status??null, lap:route?.loops??null,
    mode:route?.mode??null,
    xp, senzu:inGame ? state?.senzu_usage?.total??null:null,
    deaths:inGame ? state?.session_kills??null:null,
    profile:route ? state?.profile_selection?.combat??null:null,
    waypoint:waypointNumber(route?.waypoint?.current), reached:waypointNumber(route?.waypoint?.last_reached),
    index:route?.index??null,revision:route?.waypoint?.navigation_revision??null,
    blocked_target:point(route?.blockage?.target)?[...route.blockage.target]:null,
    movement:route?.domains?.movement.status??null, combat:route?.combat?.phase??null,
    lure:group?{goal:group.goal,following:group.following,engaged:group.engaged??null,
                nearby:group.nearby??null,waiting:group.waiting,reason:group.stop_reason}:null,
    plan:inGame && route?.status==="running" && route.map_preview ? {
      path:route.map_preview.path.slice(0,9).map(p=>[...p]),
      stop:route.map_preview.stop ? [...route.map_preview.stop] : null,
    }:null,
    monsters,unclassified_occupants:unclassified,
  };
}

type Frame={ms:number;map_at:number|null}&ReturnType<typeof capture>;
export class RouteReplay {
  readonly frames:Frame[]=[];
  readonly startedAt:number;
  endedAt:number|null=null;
  private previous="";
  private bytes=0;
  full=false;
  constructor(startedAt=Date.now(),private readonly maxBytes=12*1024*1024){this.startedAt=startedAt;}

  observe(state:BotState|null, online:boolean, now=Date.now()){
    if(this.endedAt!==null)return false;
    const data=capture(state,online,now);
    const signature=JSON.stringify(data);
    const previousMs=this.frames.at(-1)?.ms??0;
    const ms=Math.max(previousMs,now-this.startedAt);
    if(this.previous===signature && ms-previousMs<1000)return false;
    const frame={ms,map_at:data.monsters===null ? null : state?.passive_map?.captured_at??null,...data};
    const size=new TextEncoder().encode(JSON.stringify(frame)).length+1;
    if(this.bytes+size>this.maxBytes){this.full=true;this.finish(now);return true;}
    this.bytes+=size;
    this.frames.push(frame);
    this.previous=signature;
    return true;
  }
  finish(now=Date.now()){this.endedAt??=Math.max(this.startedAt+(this.frames.at(-1)?.ms??0),now);}
  serialize(){
    return JSON.stringify({format:"dbv-route-replay",version:1,started_at:new Date(this.startedAt).toISOString(),
      ended_at:this.endedAt===null?null:new Date(this.endedAt).toISOString(),stopped_at_size_limit:this.full,
      frames:this.frames});
  }
}
