/** Retry a read while the atlas initializes after login. Never retry mutations. */
export async function readAtlasWhenReady<T>(read:()=>Promise<T>,waiting:(value:boolean)=>void,cancelled:()=>boolean,
  wait:()=>Promise<void>=()=>new Promise(resolve=>setTimeout(resolve,1500))):Promise<T|undefined> {
  while(!cancelled()) {
    try {const value=await read();if(cancelled())return;waiting(false);return value;}
    catch(error) {
      if(cancelled())return;
      if((error as {status?:number})?.status!==503){waiting(false);throw error;}
      waiting(true);await wait();
    }
  }
}

type AtlasPosition=[number,number,number];
type Chunk={scope:string;center:AtlasPosition;radius:number;cells:{x:number;y:number;z:number;seen_at?:number}[];floors:number[];total:number;limit:number|null;route_transitions?:{id:string;origin:AtlasPosition;entry:AtlasPosition;exit:AtlasPosition}[];error?:string};
export function atlasRegionQueries(center:AtlasPosition,columns:number,rows:number){
  const extent=Math.max(5,Math.ceil(Math.max(columns,rows)/2));
  if(extent<=32)return [{center,radius:extent}];
  const bounds=(n:number,size:number)=>[Math.max(0,Math.floor(n-Math.floor(size/2))),Math.min(65534,Math.ceil(n-Math.floor(size/2)+size)-1)];
  const [x0,x1]=bounds(center[0],columns),[y0,y1]=bounds(center[1],rows),queries:{center:AtlasPosition;radius:number}[]=[];
  for(let x=Math.floor(x0/64);x<=Math.floor(x1/64);x++)for(let y=Math.floor(y0/64);y<=Math.floor(y1/64);y++)
    queries.push({center:[Math.min(65534,x*64+31),Math.min(65534,y*64+31),center[2]],radius:32});
  return queries;
}
/** View-local cache: bounded memory, disposed with the map; no persistent state writes. */
export function createAtlasRegionReader<T extends Chunk>(read:(center:AtlasPosition,radius:number)=>Promise<T>,now=()=>Date.now()){
  const cache=new Map<string,{at:number;data:T}>();let cacheScope:string|undefined;
  return async(center:AtlasPosition,columns:number,rows:number,scope:string|undefined,cancelled:()=>boolean):Promise<T|undefined>=>{
    if(cacheScope!==scope){cache.clear();cacheScope=scope;}
    const queries=atlasRegionQueries(center,columns,rows),results:T[]=[];
    // Limit concurrent work; cancelled/closed maps never schedule the next batch.
    for(let i=0;i<queries.length;i+=3){
      if(cancelled())return;
      const batch=await Promise.all(queries.slice(i,i+3).map(async q=>{
        const key=`${scope}:${q.center.join(',')}:${q.radius}`,cached=cache.get(key);
        if(queries.length>1&&cached&&now()-cached.at<10000){cache.delete(key);cache.set(key,cached);return cached.data;}
        const data=await read(q.center,q.radius);if(cancelled())return;
        if(scope&&data.scope!==scope)throw Error('Profil atlasu zmienił się podczas odczytu.');
        if(!data.error){cache.delete(key);cache.set(key,{at:now(),data});while(cache.size>24)cache.delete(cache.keys().next().value!);}
        return data;
      }));
      if(cancelled())return;
      for(const data of batch)if(data!==undefined)results.push(data);
    }
    if(!results.length)return;
    const cells=new Map<string,T['cells'][number]>(),edges=new Map<string,NonNullable<T['route_transitions']>[number]>();
    const actualScope=results[0].scope;
    for(const data of results){
      if(data.scope!==actualScope)throw Error('Fragmenty atlasu mają różne profile.');
      for(const cell of data.cells){const key=`${cell.x},${cell.y},${cell.z}`,old=cells.get(key);if(!old||(cell.seen_at??0)>=(old.seen_at??0))cells.set(key,cell);}
      for(const edge of data.route_transitions??[])edges.set(JSON.stringify([edge.id,edge.origin,edge.entry,edge.exit]),edge);
    }
    return {...results[0],center,radius:Math.ceil(Math.max(columns,rows)/2),cells:[...cells.values()],route_transitions:[...edges.values()],floors:[...new Set(results.flatMap(r=>r.floors))],total:Math.max(...results.map(r=>r.total)),error:results.find(r=>r.error)?.error} as T;
  };
}
