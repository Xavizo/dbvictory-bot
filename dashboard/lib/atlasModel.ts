import type {PassiveMapCell,PassiveMapState} from "./api";
export type Pos=[number,number,number];
export type RouteTransition={id:string;origin:Pos;entry:Pos;exit:Pos;action:string;item_id?:number|null;tool_id?:number|null;prepared_item_id?:number|null;tool_ids?:number[];count?:number};
export type RouteLink={from_index:number;id:string};
export type RouteEdit={points:Pos[];center:Pos;labels?:string[];links?:RouteLink[]};
export type AtlasCell=PassiveMapCell & {seen_at?:number;live?:boolean};
export const positionKey=(p:Pos)=>p.join(",");
export const MAX_ATLAS_RADIUS=96;
export function atlasViewport(width:number,height:number,radius:number) {
  // Scale the longest side: every zoom step changes tile size, even on a wide map.
  const pixel=Math.max(1,width,height)/(2*radius+1);
  const columns=Math.max(1,width)/pixel,rows=Math.max(1,height)/pixel;
  const visibleRadius=Math.max(5,Math.ceil(Math.max(columns,rows)/2));
  return {columns,rows,offsetX:Math.floor(columns/2),offsetY:Math.floor(rows/2),visibleRadius,queryRadius:Math.min(32,visibleRadius)};
}
export const routeColors=["#75e3bd","#ecb66e","#aeb0ff","#ed8dba","#68cfee","#d4de75","#f39a7a","#c6a1ef"];
export const routeColor=(index:number)=>routeColors[index%routeColors.length];
export function directionMarkers(points:Pos[],spacing=4) {
  const markers:{x:number;y:number;angle:number}[]=[];let next=spacing/2;
  for(let i=1;i<points.length;i++){
    const a=points[i-1],b=points[i];if(a[2]!==b[2]){next=spacing/2;continue;}
    const dx=b[0]-a[0],dy=b[1]-a[1],length=Math.hypot(dx,dy);if(!length)continue;
    while(next<length){markers.push({x:a[0]+dx*next/length,y:a[1]+dy*next/length,angle:Math.atan2(dy,dx)*180/Math.PI});next+=spacing;}
    next-=length;
  }
  return markers;
}
export function transitionChoices(rows:RouteTransition[],entry:Pos,from?:Pos) {
  const choices=new Map<string,RouteTransition>();
  const score=(r:RouteTransition)=>from&&from[2]===r.origin[2]?Math.max(Math.abs(from[0]-r.origin[0]),Math.abs(from[1]-r.origin[1])):0;
  for(const r of rows){if(positionKey(r.entry)!==positionKey(entry))continue;
    // Conditional digging already carries the server-approved interchangeable tools.
    // Keep different mechanisms/destinations separate, but do not show a duplicate
    // choice for each primary tool in the same observed dig transition.
    const tools=r.action==='use_with_then_step'?[...new Set(r.tool_ids?.length?r.tool_ids:[r.tool_id])].sort():r.tool_id;
    const key=JSON.stringify([r.exit,r.action,r.item_id,r.prepared_item_id,tools]);const old=choices.get(key);
    if(!old||score(r)<score(old)||score(r)===score(old)&&(r.count??0)>(old.count??0))choices.set(key,r);}
  return [...choices.values()].sort((a,b)=>score(a)-score(b));
}
export function transitionDestination(entry:Pos,rows:{entry:Pos;exit:Pos}[]) {
  const exits=[...new Set(rows.filter(r=>positionKey(r.entry)===positionKey(entry)).map(r=>r.exit[2]))];
  return exits.length===1&&exits[0]!==entry[2]?`${exits[0]<entry[2]?'↑':'↓'} ${exits[0]}`:'';
}
export const stepFloor=(z:number,delta:number)=>Math.max(0,Math.min(15,z+delta));
export function atlasWheel(deltaY:number,ctrl:boolean,shift:boolean,deltaX=0) {
  // Chromium can translate Shift + mouse wheel into a horizontal wheel event.
  const delta=deltaY || deltaX;
  return {kind:delta===0?"none":ctrl?"floor":shift?"zoom":"none",delta:Math.sign(delta)};
}
export function segmentLane(segments:ReturnType<typeof routeSegments>,index:number) {
  const peers=segments.map((s,i)=>({floor:s.floor,index:i})).filter(s=>s.floor===segments[index].floor);
  return (peers.findIndex(s=>s.index===index)-(peers.length-1)/2)*Math.min(.2,.7/Math.max(1,peers.length-1));
}
export function moveAnchor(points:Pos[],index:number,target:Pos):Pos[] {
  const closed=isRouteClosed(points);
  return points.map((p,i)=>i===index || (closed && (index===0 || index===points.length-1) && (i===0 || i===points.length-1))?[...target]:p);
}
export const isRouteClosed=(points:Pos[])=>points.length>1 && positionKey(points[0])===positionKey(points.at(-1)!);
export const canCloseNearby=(points:Pos[])=>points.length>1 && points[0][2]===points.at(-1)![2] && Math.max(Math.abs(points[0][0]-points.at(-1)![0]),Math.abs(points[0][1]-points.at(-1)![1]))<=2;
export function draftFingerprint(name:string,points:Pos[],labels:string[],links:RouteLink[],loop:boolean) {
  return JSON.stringify([name.trim(),points,points.map((_,i)=>(labels[i]??'').trim()),[...links].sort((a,b)=>a.from_index-b.from_index)]);
}
export function hasDraftChanges(id:string,baseline:string,name:string,points:Pos[],labels:string[],links:RouteLink[],loop:boolean) {
  return id ? baseline!==draftFingerprint(name,points,labels,links,loop) : points.length>0 || !!name.trim();
}
export function closeRoute(points:Pos[]):Pos[] {return points.length>1 && !isRouteClosed(points)?[...points,[...points[0]]]:points;}
export function finishAnchorEdit(points:Pos[],index:number,target:Pos,moved:boolean):Pos[] {
  return index===0 && !moved?closeRoute(points):moveAnchor(points,index,target);
}
export function insertAnchor(points:Pos[],after:number,target:Pos):Pos[] {
  if(after<0 || after>=points.length-1 || [points[after],points[after+1]].some(p=>positionKey(p)===positionKey(target)))return points;
  return [...points.slice(0,after+1),[...target],...points.slice(after+1)];
}
export function removeAnchor(points:Pos[],index:number):Pos[] {
  if(isRouteClosed(points) && (index===0 || index===points.length-1))return closeRoute(points.slice(1,-1));
  return points.filter((_,i)=>i!==index);
}
export function appendTransition(points:Pos[],edge:RouteTransition):Pos[] {
  const result=points.map(p=>[...p] as Pos);
  // Repair a trailing ordinary anchor mistakenly placed directly on stairs.
  if(result.length && positionKey(result.at(-1)!)===positionKey(edge.entry) && positionKey(edge.entry)!==positionKey(edge.origin))result.pop();
  for(const p of [edge.origin,edge.exit])if(!result.length || positionKey(result.at(-1)!)!==positionKey(p))result.push([...p]);
  return result;
}
export function remapLinks(links:RouteLink[],before:Pos[],after:Pos[]):RouteLink[] {
  const result:RouteLink[]=[];let cursor=0;
  for(const link of [...links].sort((a,b)=>a.from_index-b.from_index)){const a=before[link.from_index],b=before[link.from_index+1];if(!a||!b)continue;
    for(let i=cursor;i<after.length-1;i++)if(positionKey(a)===positionKey(after[i])&&positionKey(b)===positionKey(after[i+1])){result.push({...link,from_index:i});cursor=i+1;break;}}return result;
}
export function selectTransition(points:Pos[],links:RouteLink[],edge:RouteTransition,segment?:number){
  const same=(a:Pos|undefined,b:Pos)=>!!a&&positionKey(a)===positionKey(b);
  const adjacent=(p:Pos)=>p[2]===edge.entry[2]&&Math.max(Math.abs(p[0]-edge.entry[0]),Math.abs(p[1]-edge.entry[1]))<=1;
  const candidates=points.flatMap((p,i)=>same(p,edge.entry)||(same(p,edge.origin)||adjacent(p))&&same(points[i+1],edge.exit)?[i]:[]);
  const preferred=segment===undefined?undefined:[segment,segment+1].find(i=>candidates.includes(i));
  const unlinked=candidates.filter(i=>!links.some(l=>l.from_index===i));
  if(preferred===undefined&&(unlinked.length>1||!unlinked.length&&candidates.length>1))throw Error('To pole występuje w trasie kilka razy. Najpierw kliknij komunikat konkretnego błędnego odcinka.');
  const existing=preferred??unlinked[0]??(candidates.length===1?candidates[0]:undefined);
  if(segment!==undefined&&preferred===undefined)throw Error('To przejście nie pasuje do wskazanego odcinka. Nie dopisano punktów na końcu trasy.');
  let next=points.map(p=>[...p] as Pos),sourceIndices:(number|null)[]=points.map((_,i)=>i),index:number;
  if(existing!==undefined){
    index=existing;
    // Keep the selected approach for known rope/dig functions as well as
    // simple steps/uses. The backend rejects conflicting learned exits.
    const knownTool=['use_with','use_with_then_step'].includes(edge.action)&&supportedTransition(edge);
    if(!same(next[index],edge.entry)&&!same(next[index],edge.origin)&&!['step','use'].includes(edge.action)&&!knownTool)next[index]=[...edge.origin];
    if(same(next[index],edge.entry)&&!same(next[index],edge.origin)&&!(knownTool&&edge.action==='use_with')){
      if(index>0&&same(next[index-1],edge.origin)){next.splice(index,1);sourceIndices.splice(index,1);index--;}
      else next[index]=[...edge.origin];
    }
    if(!same(next[index+1],edge.exit)){next.splice(index+1,0,[...edge.exit]);sourceIndices.splice(index+1,0,null);}
  }else{
    next=appendTransition(points,edge);index=next.length-2;
    sourceIndices=next.map((p,i)=>i<points.length&&same(p,points[i])?i:null);
  }
  // Map by occurrence, not just coordinates: a loop can visit the same stairs twice.
  const kept=links.flatMap(link=>{const i=sourceIndices.indexOf(link.from_index);
    return i>=0&&sourceIndices[i+1]===link.from_index+1&&same(next[i],points[link.from_index])&&same(next[i+1],points[link.from_index+1])?[{...link,from_index:i}]:[];});
  return {points:next,sourceIndices,from_index:index,mode:existing===undefined?'append' as const:'repair' as const,links:[...kept.filter(l=>l.from_index!==index),{from_index:index,id:edge.id}].sort((a,b)=>a.from_index-b.from_index)};
}
export function rememberEdit(history:RouteEdit[],points:Pos[],center:Pos,labels?:string[],links?:RouteLink[]):RouteEdit[] {
  return [...history.slice(-39),{points:points.map(p=>[...p] as Pos),center:[...center] as Pos,labels:labels?[...labels]:undefined,links:links?.map(l=>({...l}))}];
}
export function remapLabels(before:Pos[],after:Pos[],labels:string[]):string[]{
  if(before.length===after.length)return after.map((_,i)=>labels[i]??"");
  let cursor=0;
  return after.map(p=>{const index=before.findIndex((q,i)=>i>=cursor&&positionKey(p)===positionKey(q));if(index<0)return "";cursor=index+1;return labels[index]??"";});
}
export function unresolvedLegs(plan:{points:Pos[];gaps:{segment:number}[]}) {
  const first=plan.gaps[0]?.segment;
  return first===undefined?[]:plan.points.slice(first,-1).map((p,i)=>[p,plan.points[first+i+1]]);
}
export function undoShortcut(key:string,ctrl:boolean,shift:boolean,targetTag:string,editable:boolean) {
  return ctrl && !shift && key.toLowerCase()==="z" && !editable && !["INPUT","TEXTAREA","SELECT"].includes(targetTag);
}
export function panCenter(origin:Pos,dx:number,dy:number,pixelsPerTile:number):Pos {
  return [Math.max(0,Math.min(65534,origin[0]-Math.round(dx/pixelsPerTile))),Math.max(0,Math.min(65534,origin[1]-Math.round(dy/pixelsPerTile))),origin[2]];
}
export function routeSegments(path:Pos[]) {
  const result:{floor:number;start:number;end:number;points:Pos[]}[]=[];
  for(let index=0;index<path.length;index++) {
    const p=path[index],old=result.at(-1);
    if(old && old.floor===p[2]){old.end=index;old.points.push(p);}
    else result.push({floor:p[2],start:index,end:index,points:[p]});
  }
  return result;
}
export function transitionStages(path:Pos[],transitions:{origin:Pos;exit:Pos}[]) {
  const stages=routeSegments(path).flatMap((s,i)=>s.points.map(()=>i));let cursor=0;
  return transitions.map(t=>{for(let i=cursor;i<path.length-1;i++){
    if(positionKey(path[i])===positionKey(t.origin)&&positionKey(path[i+1])===positionKey(t.exit)){cursor=i+1;return {from:stages[i],to:stages[i+1]};}
  }return {from:-1,to:-1};});
}
type DisplayTransition={origin:Pos;entry:Pos;exit:Pos;action:string;tool_id?:number|null;item_id?:number|null;prepared_item_id?:number|null};
export function transitionMarkers(path:Pos[],transitions:DisplayTransition[],floor:number,hidden:number[]=[]){
  const stages=transitionStages(path,transitions),groups=new Map<string,{pos:Pos;target:Pos;out:boolean;indices:number[];action:string}>();
  transitions.forEach((t,i)=>{for(const m of [{pos:t.entry,target:t.exit,out:true},{pos:t.exit,target:t.entry,out:false}]){
    if(m.pos[2]!==floor||hidden.includes(m.out?stages[i].from:stages[i].to))continue;
    const key=JSON.stringify([m.pos,m.target,m.out,t.action]),old=groups.get(key);
    if(old)old.indices.push(i);else groups.set(key,{...m,indices:[i],action:t.action});
  }});return [...groups.values()];
}
export function transitionApproaches(path:Pos[],transitions:DisplayTransition[],floor:number,hidden:number[]=[]){
  const stages=transitionStages(path,transitions),seen=new Set<string>();
  return transitions.flatMap((t,i)=>{
    // This is the actual step into the trigger tile; tools alone do not imply walking.
    if(t.origin[2]!==floor||t.entry[2]!==floor||hidden.includes(stages[i].from)||!['step','use_then_step','use_with_then_step'].includes(t.action))return [];
    const dx=t.entry[0]-t.origin[0],dy=t.entry[1]-t.origin[1],key=JSON.stringify([t.origin,t.entry]);
    if(Math.max(Math.abs(dx),Math.abs(dy))!==1||seen.has(key))return [];
    seen.add(key);return [{from:t.origin,to:t.entry,stage:stages[i].from,index:i,angle:Math.atan2(dy,dx)*180/Math.PI}];
  });
}
export function endpointMarkers(path:Pos[],floor:number,hidden:number[]=[]){
  const markers=new Map<string,{pos:Pos;labels:string[];stage:number}>();
  routeSegments(path).forEach((s,i)=>{
    if(s.floor!==floor||hidden.includes(i))return;
    const closed=positionKey(s.points[0])===positionKey(s.points.at(-1)!);
    (closed?[s.points[0]]:[s.points[0],s.points.at(-1)!]).forEach((p,j)=>{
      const label=`${i+1}${closed?'A/B':j?'B':'A'}`,old=markers.get(positionKey(p));
      if(old)old.labels.push(label);else markers.set(positionKey(p),{pos:p,labels:[label],stage:i});
    });
  });return [...markers.values()];
}
export function supportedTransition(t:Partial<DisplayTransition>&{action:string}):boolean {
  if(['step','use','use_then_step'].includes(t.action))return true;
  const item=(v:unknown)=>typeof v==='number'&&Number.isInteger(v)&&v>=100&&v<=65535;
  if(!t.origin||!t.entry||!t.exit||!item(t.item_id)||t.origin[2]!==t.entry[2])return false;
  const distance=Math.max(Math.abs(t.origin[0]-t.entry[0]),Math.abs(t.origin[1]-t.entry[1]));
  if(t.action==='use_with')return [3003,8899,8900].includes(t.tool_id??0)&&t.exit[2]===t.origin[2]-1&&distance<=1;
  return t.action==='use_with_then_step'&&[3457,8899,8900].includes(t.tool_id??0)&&item(t.prepared_item_id)&&t.prepared_item_id!==t.item_id&&t.exit[2]===t.origin[2]+1&&distance===1;
}
export function routeExecutionIssues(plan:{transitions:DisplayTransition[];avoid_tiles?:Pos[]}){
  const issues:{key:string;message:string;pos:Pos}[]=[];
  plan.transitions.forEach((t,i)=>{
    const supported=supportedTransition(t);
    if(!supported)issues.push({key:`transition-${i}`,pos:t.entry,message:`T${i+1}: ${t.action==='use_with'?`użycie narzędzia${t.tool_id?` ${t.tool_id}`:''}`:'ten rodzaj przejścia'} — wykonanie nieobsługiwane w V2. (${t.entry.join(', ')}) → (${t.exit.join(', ')})`});
  });
  const seen=new Set<string>();for(const p of plan.avoid_tiles??[]){const key=positionKey(p);if(seen.has(key))continue;seen.add(key);issues.push({key:`avoid-${key}`,pos:p,message:`Pole (${p.join(', ')}) jest oznaczone jako omijane i blokuje uruchomienie trasy.`});}
  return issues;
}
export function atlasCenter(follow:boolean,manual:Pos,live?:Pos):Pos {
  return follow && live ? live:manual;
}
export function visibleAtlasCells(saved:AtlasCell[],live:PassiveMapState|undefined,scope:string|undefined,center:Pos,radius:number):AtlasCell[] {
  const visible=(c:PassiveMapCell)=>c.z===center[2] && Math.abs(c.x-center[0])<=radius && Math.abs(c.y-center[1])<=radius;
  const cells=new Map(saved.filter(visible).map(c=>[`${c.x},${c.y},${c.z}`,{...c,live:false}]));
  if(scope && live?.annotation_scope===scope && live.status==="ready" && !live.stale) {
    for(const c of live.cells)if(visible(c) && c.kind!=="unknown")cells.set(`${c.x},${c.y},${c.z}`,{...c,live:true});
  }
  return [...cells.values()];
}
export function routeLines(points:Pos[],z:number):Pos[][] {
  const result:Pos[][]=[];let line:Pos[]=[];
  for(const p of points) {
    // No fake straight line across a teleport or a missing observed sample.
    const old=line.at(-1);
    if(old && Math.max(Math.abs(old[0]-p[0]),Math.abs(old[1]-p[1]))>1){result.push(line);line=[];}
    if(p[2]===z)line.push(p);else if(line.length){result.push(line);line=[];}
  }
  if(line.length)result.push(line);return result;
}
export function simulationStep(path:Pos[],index:number,transitions:{origin:Pos;exit:Pos;action:string}[]) {
  const current=path[index],next=path[index+1];
  const transition=next && transitions.find(t=>positionKey(t.origin)===positionKey(current) && positionKey(t.exit)===positionKey(next));
  return {current,next,transition};
}
export function simulationReport(plan:{path:Pos[];complete:boolean;loop_closed?:boolean;return_error?:string|null;gaps:{reason:string;from?:Pos;to?:Pos;segment?:number}[];transitions:(Partial<DisplayTransition>&{action:string})[];requires_action_review?:boolean;requires_path_review?:boolean;recorded_actions?:{item_id?:number;x?:number;y?:number;z?:number;to_x?:number;to_y?:number;to_z?:number;matched_transition?:number|null}[]},loop:boolean,points:Pos[]) {
  const unsupported=plan.transitions.filter(t=>!supportedTransition(t));
  const unmatched=plan.recorded_actions?.filter(a=>a.matched_transition==null)??[];
  const actionIssues=plan.requires_action_review&&unmatched.length?unmatched.slice(0,3).map(a=>`Dodatkowe użycie ID ${a.item_id??'?'} na (${[a.x??a.to_x,a.y??a.to_y,a.z??a.to_z].join(', ')}): nie jest częścią zaplanowanych przejść.`):[];
  const issues=[...plan.gaps.map(g=>g.from&&g.to?`Odcinek ${(g.segment??0)+1} → ${(g.segment??0)+2}: (${g.from.join(", ")}) → (${g.to.join(", ")}). ${g.reason}`:g.reason),...(unsupported.length?[`${unsupported.length} przejść wymaga nieobsługiwanego jeszcze narzędzia lub czaru.`]:[]),...(loop&&plan.complete&&!(plan.loop_closed??isRouteClosed(points))?[plan.return_error??"Brak znanego powrotu do początku trasy."]:[]),...actionIssues,...((plan.requires_path_review??(plan.requires_action_review&&!actionIssues.length))?["Pola lub ciągłość odczytu pozycji wymagają sprawdzenia."]:[]),...(plan.path.length<2?["Brak ruchu w trasie."]:[])];
  return {ok:plan.complete&&!issues.length,issues,steps:Math.max(0,plan.path.length-1),floors:routeSegments(plan.path).map(s=>s.floor),transitions:plan.transitions.length};
}
// Animation is only an illustration: the whole plan is checked synchronously.
export const fastSimulationNext=(index:number,length:number)=>Math.min(length-1,index+Math.max(1,Math.ceil(length/60)));
