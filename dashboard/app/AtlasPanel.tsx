"use client";
import {useEffect,useRef,useState,type ReactNode} from "react";
import {MapTool,MapMore} from "./MapTools";
import {createPortal} from "react-dom";
import dynamic from "next/dynamic";
import {waypointKind,visibleWaypointProgress} from "@/lib/cavebotModel";
import {authenticatedFetch} from "@/lib/auth";
import {readAtlasWhenReady,createAtlasRegionReader} from "@/lib/atlasLoading";
import type {BotState,PassiveMapCell} from "@/lib/api";
import {atlasCenter,visibleAtlasCells,routeLines,simulationStep,simulationReport,fastSimulationNext,atlasViewport,panCenter,routeSegments,routeColor,directionMarkers,transitionChoices,transitionDestination,transitionStages,segmentLane,stepFloor,atlasWheel,moveAnchor,isRouteClosed,canCloseNearby,draftFingerprint,hasDraftChanges,closeRoute,finishAnchorEdit,insertAnchor,removeAnchor,appendTransition,rememberEdit,remapLabels,unresolvedLegs,undoShortcut,remapLinks,selectTransition,type RouteLink,type RouteTransition,type RouteEdit} from "@/lib/atlasModel";
import AtlasRouteOverview from "./AtlasRouteOverview";
import DraftTags from "./DraftTags";
import type {DraftTag} from "@/lib/cavebotModel";
import {tileStyles,navigationStyles,petStyle} from "@/lib/mapVisuals";
import {NavigationDetails,TileContextMenu,copyCellText,type TileMenuPosition} from "./PassiveMapPanel";
import {transitionActions} from "./TransitionLearningPanel";
import {transitionMarkers,transitionApproaches,endpointMarkers,routeExecutionIssues,MAX_ATLAS_RADIUS} from "@/lib/atlasModel";
import type {TransitionAction} from "@/lib/api";
type Pos=[number,number,number];
type Draft={navigation_revision?:string;id:string;name:string;points:Pos[];labels?:string[];tags?:DraftTag[];links?:RouteLink[];loop?:boolean;updated:number};
type Atlas={route_transitions?:RouteTransition[];scope:string;center:Pos;radius:number;cells:PassiveMapCell[];floors:number[];total:number;limit:number|null;error?:string};
type Plan={avoid_tiles?:Pos[];return_mode?:string;return_error?:string|null;loop_closed:boolean;links:RouteLink[];scope:string;path:Pos[];points:Pos[];original:Pos[];complete:boolean;preview_only:boolean;warning:string;requires_action_review?:boolean;requires_path_review?:boolean;ignored_recorded_actions?:boolean;unmatched_recorded_actions?:number;recording_source?:string;
  adaptive_evidence?:{areas:number;samples:number;total_samples:number;xp:number};
  transitions:{origin:Pos;entry:Pos;exit:Pos;action:string;item_id?:number;tool_id?:number;prepared_item_id?:number}[];
  legs?:{from_index:number;to_index:number;start:number;end:number}[];
  recorded_actions?:{type:string;item_id?:number;player_pos?:Pos;x?:number;y?:number;z?:number;to_x?:number;to_y?:number;to_z?:number;matched_transition?:number|null}[];
  gaps:{segment:number;from:Pos;to:Pos;reason:string}[]};
async function request(path:string,body?:unknown) {
  const response=await authenticatedFetch(`http://127.0.0.1:8089${path}`,body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : undefined);
  const data=await response.json();if(!response.ok)throw Object.assign(new Error(data.error ?? `HTTP ${response.status}`),{status:response.status});return data;
}
const pointText=(p:Pos)=>`(${p.join(", ")})`;
const ObjectMap=dynamic(()=>import("./PassiveMapPanel"),{ssr:false});
function lines(points:Pos[],z:number) {const result:Pos[][]=[];let line:Pos[]=[];for(const p of points){if(p[2]===z)line.push(p);else if(line.length){result.push(line);line=[];}}if(line.length)result.push(line);return result;}
export default function AtlasPanel({state,active,onRefresh,initialDraftId="",selectionVersion=0,embedded=false,browseOnly=false,profileScope,routePicker,routeEnabledControl,moreActions,onDraftSaved,onEditingChange,onLoopChange}:{state:BotState|null;active:boolean;onRefresh:()=>void;initialDraftId?:string;selectionVersion?:number;embedded?:boolean;browseOnly?:boolean;profileScope?:string;routePicker?:ReactNode;routeEnabledControl?:ReactNode;moreActions?:ReactNode;onDraftSaved?:(id:string)=>void;onEditingChange?:(dirty:boolean)=>void;onLoopChange?:(value:boolean)=>void}) {
  const [manual,setManual]=useState<Pos>([100,100,7]),[follow,setFollow]=useState(true),[radius,setRadius]=useState(12),[atlas,setAtlas]=useState<Atlas|null>(null);
  const [selected,setSelected]=useState<Pos|null>(null),[drafts,setDrafts]=useState<Draft[]>([]),[draftId,setDraftId]=useState(""),[draftName,setDraftName]=useState(""),[savedVersion,setSavedVersion]=useState("");
  const [tileMenu,setTileMenu]=useState<TileMenuPosition|null>(null);
  const [simIndex,setSimIndex]=useState<number|null>(null),[simPlaying,setSimPlaying]=useState(false);
  const [loop,setLoop]=useState(true);
  const [newTags,setNewTags]=useState<DraftTag[]>([]);
  const [mapOpen,setMapOpen]=useState(!embedded),[optimization,setOptimization]=useState<{before:Plan;candidate:Plan}|null>(null),[simulation,setSimulation]=useState<ReturnType<typeof simulationReport>|null>(null);

  const [viewport,setViewport]=useState({width:700,height:450}),[segmentIndex,setSegmentIndex]=useState(0),[showTrace,setShowTrace]=useState(false),[sidebar,setSidebar]=useState(false);
  const pan=useRef<{x:number;y:number;origin:Pos;pixel:number}|null>(null);
  const [panMode,setPanMode]=useState(false);
  const [drawMode,setDrawMode]=useState<"points"|"freehand">("points"),[overview,setOverview]=useState(false);
  const anchorDrag=useRef<number|null>(null),anchorMoved=useRef(false);
  const [points,setPoints]=useState<Pos[]>([]),[plan,setPlan]=useState<Plan|null>(null),[error,setError]=useState(""),[busy,setBusy]=useState(false);
  const [links,setLinks]=useState<RouteLink[]>([]);
  const [labels,setLabels]=useState<string[]>([]),[lastOptimization,setLastOptimization]=useState<{plan:Plan;labels:string[]}|null>(null);
  const dirty=hasDraftChanges(draftId,savedVersion,draftName,points,labels,links,loop);
  const saved=!!draftId && !dirty;
  const [objectView,setObjectView]=useState(false),[rename,setRename]=useState(false);
  const [loadError,setLoadError]=useState("");
  const [mapWaiting,setMapWaiting]=useState(false),[draftWaiting,setDraftWaiting]=useState(false),[draftLoadError,setDraftLoadError]=useState("");
  const workingPlan=optimization?.candidate??plan;
  const [history,setHistory]=useState<RouteEdit[]>([]),[transitionSpot,setTransitionSpot]=useState<Pos|null>(null);
  const [editSegment,setEditSegment]=useState<number|undefined>(undefined),[transitionNotice,setTransitionNotice]=useState("");
  const [transitionExit,setTransitionExit]=useState<Pos|null>(null);
  const atlasReader=useRef<ReturnType<typeof createAtlasRegionReader<Atlas>>|null>(null);
  if(!atlasReader.current)atlasReader.current=createAtlasRegionReader<Atlas>((p,r)=>request(`/api/map/atlas?x=${p[0]}&y=${p[1]}&z=${p[2]}&radius=${r}`));
  const editOrigin=useRef<RouteEdit|null>(null);
  const [recording,setRecording]=useState(""),[draw,setDraw]=useState(false),[copied,setCopied]=useState(false);
  const stroke=useRef<Pos[]>([]),svg=useRef<SVGSVGElement>(null),generation=useRef(0);
  const live=state?.passive_map?.center;
  const center=atlasCenter(follow&&!simPlaying,manual,live),scope=profileScope||state?.passive_map?.annotation_scope||atlas?.scope,liveScope=state?.passive_map?.annotation_scope;
  const grid=atlasViewport(viewport.width,viewport.height,radius),ox=grid.offsetX,oy=grid.offsetY;
  const compatible=!liveScope || liveScope===scope;
  const cells=active&&mapOpen&&!objectView?visibleAtlasCells(compatible?atlas?.cells ?? []:[],state?.passive_map,scope,center,grid.visibleRadius):[];
  const recorder=state?.cavebot?.recording;
  const canRecord=!!state?.connected && !["running","paused","ready"].includes(state?.adaptive_cavebot?.status??"");
  const adaptiveSource=state?.cavebot?.recordings?.some(r=>r.name===recording && r.mode==="adaptive");
  const repairSource=state?.cavebot?.recordings.find(r=>r.name===(recording||draftName))?.name;
  const displayPath=!active||!mapOpen||objectView?[]:recorder?.active?recorder.observed_path?.map(p=>p.pos) ?? []:workingPlan?workingPlan.path:points;
  const segments=routeSegments(displayPath);
  const [hiddenSegments,setHiddenSegments]=useState<number[]>([]);
  const pointStages=routeSegments(points).flatMap((s,i)=>s.points.map(()=>i));
  const transferStages=plan?transitionStages(plan.path,plan.transitions):[];
  const executionIssues=workingPlan?routeExecutionIssues(workingPlan):[];
  const transfers=transitionMarkers(displayPath,workingPlan?.transitions??[],center[2],hiddenSegments);
  const approaches=transitionApproaches(displayPath,workingPlan?.transitions??[],center[2],hiddenSegments);
  const endpoints=endpointMarkers(displayPath,center[2],hiddenSegments);
  function focusIssue(p:Pos){setMapOpen(true);setFollow(false);setCenter(p);setSelected(p);setSidebar(true);setObjectView(false);setOverview(false);setSimPlaying(false);setSimIndex(null);}
  function showStage(index:number,route:Pos[]){setSegmentIndex(index);setFollow(false);frameRoute(route,true);}
  function toggleStage(index:number){setHiddenSegments(old=>old.includes(index)?old.filter(i=>i!==index):[...old,index]);}
  const viewCenter=useRef(center);viewCenter.current=center;
  const transitionOptions=transitionSpot?transitionChoices(compatible?atlas?.route_transitions??[]:[],transitionSpot,editSegment!==undefined?points[editSegment]:points.find((p,i)=>points[i+1]?.join(',')===transitionSpot.join(','))??points.at(-1)):[];
  function transitionChoice(edge:RouteTransition){try{const edit=selectTransition(points,links,edge,editSegment);return {label:edit.mode==='repair'?`Przypisz do odcinka ${edit.from_index+1} → ${edit.from_index+2}`:`Dodaj na końcu · punkty ${edit.from_index+1} → ${edit.from_index+2}`,error:''};}catch(e){return {label:'Wybierz właściwy odcinek',error:(e as Error).message};}}
  const transitionCell=transitionSpot && cells.find(c=>c.x===transitionSpot[0] && c.y===transitionSpot[1] && c.z===transitionSpot[2]);
  const selectedCell=selected && cells.find(c=>c.x===selected[0] && c.y===selected[1] && c.z===selected[2]);
  const menuPosition=tileMenu?.key.split(',').map(Number);
  const menuCell=tileMenu && (cells.find(c=>[c.x,c.y,c.z].join(',')===tileMenu.key) ?? {x:menuPosition![0],y:menuPosition![1],z:menuPosition![2],kind:'unknown' as const,items:[],stairs:null,occupants:0,live:false});
  const canAnnotate=!!menuCell?.live && compatible && !!state?.passive_map?.annotation_editable && !state?.passive_map?.stale;
  useEffect(()=>{setTileMenu(null);},[scope,mapOpen,objectView]);
  function openTileMenu(p:Pos,x:number,y:number){
    const cell=cells.find(c=>c.live && c.x===p[0] && c.y===p[1] && c.z===p[2]);
    setSelected(p);setTileMenu({key:p.join(','),x,y,scope,petRef:cell?.pet_ref});
  }
  async function copyMenuCell(){if(!menuCell)return;try{if(!window.dbvMap)throw Error('Brak schowka');await window.dbvMap.copyCell(copyCellText(menuCell));}catch{setError('Nie udało się skopiować danych pola.');}}
  const sim=plan && simIndex!==null?simulationStep(plan.path,simIndex,plan.transitions):null;
  function setCenter(p:Pos){viewCenter.current=p;setManual([...p]);setSimPlaying(false);}
  function changeFloor(delta:number){setFollow(false);const p=viewCenter.current;setCenter([p[0],p[1],stepFloor(p[2],delta)]);}
  function clearPlan(){generation.current++;setEditSegment(undefined);setTransitionNotice("");setOptimization(null);setSimulation(null);setPlan(null);setSimPlaying(false);setSimIndex(null);setCopied(false);}
  useEffect(()=>{const element=svg.current;if(!element)return;const observer=new ResizeObserver(([entry])=>{if(entry.contentRect.width && entry.contentRect.height)setViewport({width:entry.contentRect.width,height:entry.contentRect.height});});observer.observe(element);return()=>observer.disconnect();},[mapOpen,active,objectView]);
  useEffect(()=>{const element=svg.current;if(!element)return;const wheel=(e:WheelEvent)=>{
    const command=atlasWheel(e.deltaY,e.ctrlKey,e.shiftKey,e.deltaX);if(command.kind==="none")return;
    e.preventDefault();e.stopPropagation();if(pan.current || anchorDrag.current!==null || stroke.current.length)return;
    if(command.kind==="floor")changeFloor(command.delta);else setRadius(r=>Math.max(2,Math.min(MAX_ATLAS_RADIUS,r+command.delta*(r>=32?8:2))));
  };element.addEventListener("wheel",wheel,{passive:false});return()=>element.removeEventListener("wheel",wheel);},[mapOpen,active,objectView,follow]);
  function frameRoute(route:Pos[],force=false){if((follow&&!force)||!route.length)return;const z=route[0][2],floor=route.filter(p=>p[2]===z),xs=floor.map(p=>p[0]),ys=floor.map(p=>p[1]);
    const x1=Math.min(...xs),x2=Math.max(...xs),y1=Math.min(...ys),y2=Math.max(...ys);setCenter([Math.round((x1+x2)/2),Math.round((y1+y2)/2),z]);setRadius(Math.min(MAX_ATLAS_RADIUS,Math.max(8,Math.ceil(Math.max(x2-x1,y2-y1)/2)+2)));}
  useEffect(()=>{if(!active||!mapOpen||objectView){atlasReader.current=null;return;}let cancelled=false;let timer:ReturnType<typeof setTimeout>;const reader=atlasReader.current!;
    async function refresh(){try{const data=await readAtlasWhenReady(()=>reader(center,grid.columns,grid.rows,scope,()=>cancelled),setMapWaiting,()=>cancelled);if(!cancelled&&data){setAtlas(data);setLoadError("");}}catch(e){if(!cancelled)setLoadError(e instanceof Error?e.message:String(e));}finally{if(!cancelled)timer=setTimeout(refresh,2000);}}
    refresh();return()=>{cancelled=true;clearTimeout(timer);};
  },[active,mapOpen,objectView,center[0],center[1],center[2],grid.columns,grid.rows,scope,liveScope]);
  useEffect(()=>{setHistory([]);setTransitionSpot(null);clearPlan();setPoints([]);setLabels([]);setLinks([]);setLastOptimization(null);setBusy(false);setNewTags([]);setDraftId("");setDraftName("");setSelected(null);setDrafts([]);setError("");setDraftLoadError("");},[scope]);
  useEffect(()=>{if(!active)setSimPlaying(false);},[active]);
  useEffect(()=>{if(!embedded||!mapOpen)return;const key=(e:KeyboardEvent)=>{if(e.key==="Escape"){setMapOpen(false);setSimPlaying(false);}};window.addEventListener("keydown",key);return()=>window.removeEventListener("keydown",key);},[embedded,mapOpen]);
  useEffect(()=>{if(recorder?.active && follow)setSegmentIndex(Math.max(0,segments.length-1));},[recorder?.active,follow,segments.length]);
  useEffect(()=>{if(simIndex!==null && plan)setSegmentIndex(routeSegments(plan.path).findIndex(s=>s.start<=simIndex && s.end>=simIndex));},[simIndex,plan]);
  useEffect(()=>{if(!simPlaying || !plan || simIndex===null)return;if(simIndex>=plan.path.length-1){setSimPlaying(false);return;}
    const timer=setTimeout(()=>{const next=fastSimulationNext(simIndex,plan.path.length);setSimIndex(next);setManual([...plan.path[next]]);},35);return()=>clearTimeout(timer);
  },[simPlaying,simIndex,plan,sim?.transition]);
  async function toggleLoop(){if(busy)return;const next=!loop;setBusy(true);setError("");
    try{if(draftId){const data=await request("/api/map/drafts",{scope,id:draftId,operation:"set_loop",loop:next});setDrafts(data.drafts);}
      setLoop(next);onLoopChange?.(next);setSimulation(null);await onRefresh();
      if(points.length>1)await propose(points,undefined,next);
    }catch(e){setError(String(e));}finally{setBusy(false);}}
  async function saveDraft(){if(!scope||busy||!dirty||points.length<2||recorder?.active)return;setBusy(true);setError("");generation.current++;
    try{let expectedUpdated=drafts.find(d=>d.id===draftId)?.updated;let savedLoop=loop;
      if(draftId){
        // Combat can be saved independently while this editor has unsaved points.
        // Refresh only the revision, and only if the persisted route did not change.
        const current=await request("/api/map/drafts"),row:Draft|undefined=current.drafts.find((d:Draft)=>d.id===draftId);
        if(current.scope!==scope||!row||draftFingerprint(row.name,row.points,row.labels??[],row.links??[],row.loop??true)!==savedVersion)throw Error("Szkic zmienił się w innym oknie. Twoje zmiany pozostają w edytorze — porównaj je przed zapisem.");
        expectedUpdated=row.updated;savedLoop=row.loop??true;
      }
      const data=await request("/api/map/drafts",{scope,name:draftName.trim()||"Nowa trasa",points,labels,links,loop:savedLoop,...(draftId?{id:draftId,expected_updated:expectedUpdated}:{tags:newTags})});
      const row:Draft=data.drafts.find((d:Draft)=>d.id===data.saved_id);if(!row)throw Error("Brak potwierdzenia zapisu szkicu.");
      setDrafts(data.drafts);setDraftId(row.id);setDraftName(row.name);setPoints(row.points);setLabels(row.labels??[]);setLinks(row.links??[]);setLoop(row.loop??true);
      setOptimization(null);setSavedVersion(draftFingerprint(row.name,row.points,row.labels??[],row.links??[],row.loop??true));onEditingChange?.(false);onDraftSaved?.(row.id);
    }catch(e){setError(String(e));}finally{setBusy(false);}}

  useEffect(()=>{if(!scope){setDraftWaiting(false);return;}let cancelled=false;setDraftLoadError("");
    readAtlasWhenReady(()=>request("/api/map/drafts"),setDraftWaiting,()=>cancelled).then(data=>{if(!cancelled&&data&&data.scope===scope){setDraftLoadError("");setDrafts(data.drafts);loadDraft(initialDraftId,data.drafts);}}).catch(e=>{if(!cancelled)setDraftLoadError(e instanceof Error?e.message:String(e));});
    return()=>{cancelled=true;};
  },[scope,initialDraftId,selectionVersion]);
  useEffect(()=>{onEditingChange?.(dirty||!!optimization||!!recorder?.active);},[dirty,optimization,recorder?.active,onEditingChange]);
  function loadDraft(id:string,rows=drafts){const changedRoute=id!==draftId;setHiddenSegments([]);setRecording("");setHistory([]);setTransitionSpot(null);clearPlan();setNewTags([]);setDraftId(id);const draft=rows.find(d=>d.id===id);setPoints(draft?.points ?? []);setLinks(draft?.links??[]);setLabels(draft?.labels??draft?.points.map(()=>"")??[]);setLastOptimization(null);setRename(false);setDraftName(draft?.name ?? "");setLoop(draft?.loop ?? true);if(draft){setSavedVersion(draftFingerprint(draft.name,draft.points,draft.labels??[],draft.links??[],draft.loop??true));if(changedRoute)frameRoute(draft.points);void propose(draft.points,undefined,draft.loop ?? true,draft.links??[]);}}
  async function propose(next:Pos[],record?:string,repeat=loop,chosen=links,fullTrace=false){if(!scope || !compatible)return;const id=++generation.current;setBusy(true);setError("");setCopied(false);setSimPlaying(false);setSimIndex(null);
    try {const result:Plan=await request("/api/map/plan",{scope,loop:repeat,...(record?{recording:record,optimize:false,...(fullTrace?{full_trace:true}:{})}:{points:next,links:chosen})});if(id!==generation.current)return;setPlan(result);setPoints(result.points);setLinks(result.links??[]);setSegmentIndex(0);if(record){setHiddenSegments([]);setLabels(result.points.map(()=>""));setLastOptimization(null);frameRoute(result.original);setNewTags([]);setDraftId("");setDraftName(record);}}
    catch(e){if(id===generation.current)setError(e instanceof Error?e.message:String(e));}finally{if(id===generation.current)setBusy(false);}
  }
  function locate(event:React.PointerEvent):Pos|null {const rect=svg.current?.getBoundingClientRect();if(!rect)return null;
    if(event.clientX<rect.left || event.clientX>=rect.right || event.clientY<rect.top || event.clientY>=rect.bottom)return null;
    const x=Math.floor((event.clientX-rect.left)/rect.width*grid.columns)+center[0]-ox;
    const y=Math.floor((event.clientY-rect.top)/rect.height*grid.rows)+center[1]-oy;
    return x>=0 && y>=0 && x<65535 && y<65535 ? [x,y,center[2]]:null;
  }
  function append(p:Pos){const old=stroke.current.at(-1);if(stroke.current.length<2048 && (!old || old[2]!==p[2] || Math.max(Math.abs(old[0]-p[0]),Math.abs(old[1]-p[1]))>=4)){stroke.current.push(p);setPoints([...stroke.current]);}}
  function updatePoints(next:Pos[],before:RouteEdit={points,center,labels,links}){setRecording("");setSimulation(null);if(next.length>2048){setError("Limit 2048 punktów. Usuń jeden punkt przed domknięciem.");return;}
    if(JSON.stringify(next)!==JSON.stringify(before.points))setHistory(h=>rememberEdit(h,before.points,before.center,before.labels,before.links??links));
    setTransitionSpot(null);setLastOptimization(null);setLabels(remapLabels(before.points,next,before.labels??labels));clearPlan();setPoints(next);const chosen=remapLinks(before.links??links,before.points,next);setLinks(chosen);if(next.length>1)void propose(next,undefined,loop,chosen);}
  function undoEdit(){const previous=history.at(-1);if(!previous)return;setHistory(h=>h.slice(0,-1));setTransitionSpot(null);clearPlan();setLastOptimization(null);setLabels(previous.labels??previous.points.map(()=>""));setPoints(previous.points);setLinks(previous.links??[]);setCenter(previous.center);if(previous.points.length>1)void propose(previous.points,undefined,loop,previous.links??[]);}
  useEffect(()=>{if(!active)return;const key=(e:KeyboardEvent)=>{const target=e.target as HTMLElement;
    if(undoShortcut(e.key,e.ctrlKey,e.shiftKey,target?.tagName ?? "",!!target?.isContentEditable) && history.length){e.preventDefault();e.stopPropagation();undoEdit();}
  };window.addEventListener("keydown",key);return()=>window.removeEventListener("keydown",key);},[active,history,atlas,loop]);
  function openTransition(p:Pos){const cell=cells.find(c=>c.x===p[0] && c.y===p[1] && c.z===p[2]);
    if(cell?.navigation?.length || atlas?.route_transitions?.some(t=>t.entry.every((n,i)=>n===p[i]))){setTransitionSpot(p);return true;}return false;}
  async function useTransition(edge:RouteTransition){if(!scope || !compatible||busy)return;
    const id=++generation.current;setBusy(true);setError("");
    try {const {points:next,links:chosen,sourceIndices}=selectTransition(points,links,edge,editSegment);
      if(next.length>2048)throw Error("Limit 2048 punktów — usuń punkt przed dodaniem przejścia.");
      const result:Plan=await request("/api/map/plan",{scope,points:next,links:chosen,loop});if(id!==generation.current)return;
      if(!result.complete)setError("Pozostały odcinek do sprawdzenia: "+(result.gaps[0]?.reason??"sprawdź mapę"));
      const nextLabels=sourceIndices.map(i=>i===null?"":labels[i]??"");
      setRecording("");setSimulation(null);setHistory(h=>rememberEdit(h,points,center,labels,links));setLabels(remapLabels(next,result.points,nextLabels));setLastOptimization(null);setPoints(result.points);setLinks(result.links??[]);setPlan(result);setCopied(false);setSimPlaying(false);setSimIndex(null);setTransitionSpot(null);setDraw(true);setPanMode(false);setSelected(edge.entry);setEditSegment(undefined);
      setTransitionExit(edge.exit);setTransitionNotice(`Przejście przypisane w szkicu: ${edge.entry[2]} → ${edge.exit[2]}. Widok pozostaje bez zmian.`);
    }catch(e){if(id===generation.current)setError(e instanceof Error?e.message:String(e));}finally{if(id===generation.current)setBusy(false);}
  }
  function cancelPointer(){const before=editOrigin.current;editOrigin.current=null;stroke.current=[];pan.current=null;anchorDrag.current=null;
    if(before){clearPlan();setLabels(before.labels??labels);setPoints(before.points);setLinks(before.links??links);if(before.points.length>1)void propose(before.points,undefined,loop,before.links??links);}}

  function pointerDown(e:React.PointerEvent<SVGSVGElement>) {
    if(e.button!==0 && e.button!==1)return;
    if(e.ctrlKey || e.button===1 || panMode){setFollow(false);const rect=e.currentTarget.getBoundingClientRect();pan.current={x:e.clientX,y:e.clientY,origin:[...center],pixel:rect.width/grid.columns};setCenter(center);e.currentTarget.setPointerCapture(e.pointerId);return;}
    const p=locate(e);if(!p)return;if(!draw){setSelected(p);setSidebar(true);return;}if(busy||optimization||recorder?.active)return;
    if(drawMode==="points") {
      if(openTransition(p))return;
      const index=points.findIndex(old=>old.every((n,i)=>n===p[i]));
      if(index>=0){editOrigin.current={points:points.map(p=>[...p] as Pos),center:[...center],labels:[...labels],links:[...links]};anchorDrag.current=index;anchorMoved.current=false;stroke.current=[...points];e.currentTarget.setPointerCapture(e.pointerId);return;}
      if(points.length>=2048){setError("Limit 2048 punktów szkicu. Przesuń lub usuń istniejący punkt.");return;}
      updatePoints([...points,p]);return;
    }
    editOrigin.current={points:points.map(p=>[...p] as Pos),center:[...center],labels:[...labels],links:[...links]};e.currentTarget.setPointerCapture(e.pointerId);stroke.current=[...points];append(p);clearPlan();
  }
  function pointerMove(e:React.PointerEvent<SVGSVGElement>) {
    if(pan.current){const p=pan.current;setManual(panCenter(p.origin,e.clientX-p.x,e.clientY-p.y,p.pixel));return;}
    if(!draw || !e.currentTarget.hasPointerCapture(e.pointerId))return;const p=locate(e);if(!p)return;
    if(anchorDrag.current!==null){if(stroke.current[anchorDrag.current].some((n,i)=>n!==p[i]))anchorMoved.current=true;stroke.current=moveAnchor(stroke.current,anchorDrag.current,p);clearPlan();setPoints([...stroke.current]);return;}
    if(drawMode==="freehand")append(p);
  }
  function pointerUp(e:React.PointerEvent<SVGSVGElement>) {
    if(!e.currentTarget.hasPointerCapture(e.pointerId))return;e.currentTarget.releasePointerCapture(e.pointerId);
    if(pan.current){pan.current=null;return;}
    const p=locate(e);
    if(anchorDrag.current!==null){if(p)stroke.current=finishAnchorEdit(stroke.current,anchorDrag.current,p,anchorMoved.current);anchorDrag.current=null;}
    else if(p && stroke.current.length<2048 && JSON.stringify(p)!==JSON.stringify(stroke.current.at(-1)))stroke.current.push(p);
    const next=[...stroke.current],before=editOrigin.current;editOrigin.current=null;stroke.current=[];updatePoints(next,before ?? undefined);
  }
  async function recordToggle(mode:"manual"|"adaptive"="manual"){setBusy(true);setError("");try{
    if(state?.cavebot?.recording.active){const name=state.cavebot.recording.name;await request("/api/cavebot/record/stop",{discard:false});await onRefresh();setRecording(name);await propose([],name);}
    else {const stamp=new Date().toISOString().replace(/[^0-9]/g,"").slice(0,14);
      const name=mode==="adaptive"?`exp_${stamp}_${crypto.randomUUID().slice(0,8)}`:`atlas_${stamp}`;
      await request("/api/cavebot/record/start",{name,mode});clearPlan();setHistory([]);setTransitionSpot(null);setPoints([]);setLabels([]);setLinks([]);setLastOptimization(null);setNewTags([]);setDraftId("");setDraftName("");setRecording(name);setDraw(false);setFollow(true);await onRefresh();}
  }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}}
  function editHere(){setObjectView(false);setMapOpen(true);setCenter(center);setDraw(true);setPanMode(false);}
  async function optimizeRoute(){if(!plan||!scope)return;const generationId=++generation.current;setBusy(true);setError("");setSimulation(null);setDraw(false);try{
    const candidate:Plan=await request("/api/map/plan",{scope,points,labels,links,loop,optimize:true,...(recording&&!adaptiveSource?{recording}:{})});
    if(generationId===generation.current){setOptimization({before:plan,candidate});frameRoute(candidate.path);}
  }catch(e){if(generationId===generation.current)setError(String(e));}finally{if(generationId===generation.current)setBusy(false);}}
  function acceptOptimization(){if(!optimization?.candidate.complete)return;setLastOptimization({plan:optimization.before,labels:[...labels]});setLabels(remapLabels(points,optimization.candidate.points,labels));setHistory(h=>rememberEdit(h,points,center,labels,links));setPlan(optimization.candidate);setPoints(optimization.candidate.points);setLinks(optimization.candidate.links??[]);setRecording("");setOptimization(null);}
  function undoOptimization(){if(!lastOptimization)return;generation.current++;setPlan(lastOptimization.plan);setPoints(lastOptimization.plan.points);setLinks(lastOptimization.plan.links??[]);setLabels(lastOptimization.labels);setLastOptimization(null);setSimulation(null);setRecording("");setHistory([]);}
  function labelPoint(index:number,value:string){setLabels(points.map((_,i)=>i===index?value:labels[i]??""));setRecording("");}
  function focusGap(g:{from:Pos;segment?:number}){
    if(busy||recorder?.active)return;
    // Edit the original, never an unaccepted optimization candidate.
    if(optimization)setPlan(optimization.before);
    setOptimization(null);setSimulation(null);setSimPlaying(false);setSimIndex(null);
    setObjectView(false);setOverview(false);setDraw(!browseOnly);setPanMode(false);
    setEditSegment(optimization?undefined:g.segment);setTransitionNotice("");setTransitionSpot(null);setHiddenSegments([]);setFollow(false);setMapOpen(true);setCenter(g.from);setSelected(g.from);setSidebar(true);
  }
  async function restoreRecordedTransitions(){
    if(!repairSource||!scope||!compatible||busy||recorder?.active||browseOnly)return;
    const id=++generation.current;setBusy(true);setError("");
    try {
      const source:Plan=await request("/api/map/plan",{scope,recording:repairSource,optimize:false,loop});
      if(id!==generation.current)return;
      if(source.scope!==scope||JSON.stringify(source.points)!==JSON.stringify(points))throw Error("Ten szkic ma już inny przebieg niż nagranie. Zachowałem Twoje punkty; nagranie możesz wczytać osobno w szczegółach mapy.");
      const chosen=[...links,...(source.links??[]).filter(link=>!links.some(old=>old.from_index===link.from_index))].sort((a,b)=>a.from_index-b.from_index);
      const result:Plan=await request("/api/map/plan",{scope,points,links:chosen,loop});
      if(id!==generation.current)return;
      if(JSON.stringify(chosen)!==JSON.stringify(links))setHistory(h=>rememberEdit(h,points,center,labels,links));
      setLinks(result.links??chosen);setPlan(result);setOptimization(null);setRecording("");setLastOptimization(null);
      setSimulation(null);setSimPlaying(false);setSimIndex(null);setDraw(!browseOnly);setObjectView(false);setOverview(false);
      if(!result.complete)setError("Przejścia z nagrania zostały uzupełnione. "+(result.gaps[0]?.reason??"Sprawdź pozostały odcinek."));
    } catch(e){if(id===generation.current)setError(e instanceof Error?e.message:String(e));}
    finally{if(id===generation.current)setBusy(false);}
  }
  function simulate(){if(!plan)return;setSimulation(simulationReport(plan,loop,points));setDraw(false);setCenter(plan.path[0]);setSimIndex(0);setSimPlaying(true);}
  const xy=(p:Pos)=>`${p[0]-center[0]+ox+.5},${p[1]-center[1]+oy+.5}`;
  const radii=Array.from(new Set([2,4,8,12,20,32,48,64,96,radius])).sort((a,b)=>a-b);
  const floors=Array.from({length:16},(_,z)=>z);
  const objectPositions=new Set(cells.filter(c=>c.navigation?.length).map(c=>[c.x,c.y,c.z].join(",")));
  const onFloor=segments.map((s,i)=>({...s,index:i})).filter(s=>s.floor===center[2]&&!hiddenSegments.includes(s.index));
  const progress=visibleWaypointProgress(state?.adaptive_cavebot,draftId,drafts.find(d=>d.id===draftId)?.updated,dirty,drafts.find(d=>d.id===draftId)?.navigation_revision);
  const waypointList=<div className="v2-point-scroll" tabIndex={0} role="region" aria-label="Przewijana lista waypointów"><table><thead><tr><th>#</th><th>Współrzędne</th><th>Etykieta</th><th/></tr></thead><tbody>{points.map((p,i)=><tr key={i} aria-current={progress?.current===i?"step":undefined} className={progress?.current===i?"waypoint-current":progress?.last_reached===i?"waypoint-reached":""}><td title={progress?.current===i?"Bieżący cel trasy":progress?.last_reached===i?"Ostatnio zaliczony":undefined}>{progress?.current===i?"▸ ":""}{i+1}</td><td><button className="waypoint-location" title={waypointKind(points,i)} onClick={()=>{setMapOpen(true);setCenter(p);setSelected(p);}}>{p.join(", ")}</button></td><td><input aria-label={`Etykieta waypointu ${i+1}`} placeholder={waypointKind(points,i)} maxLength={60} value={labels[i]??""} disabled={busy||!!optimization||recorder?.active} onChange={e=>labelPoint(i,e.target.value)}/></td><td><button aria-label={`Usuń waypoint ${i+1}`} disabled={busy||!!optimization||recorder?.active} onClick={()=>updatePoints(removeAnchor(points,i))}>×</button></td></tr>)}</tbody></table>{!points.length&&<p className="v2-empty-points">Nagraj ruch lub dodaj punkty na mapie.</p>}</div>;
  const review=<>{draftWaiting&&<p role="status" className="v2-inline-note">Wczytywanie atlasu…</p>}{draftLoadError&&<p role="alert" className="v2-error">{draftLoadError}</p>}{optimization&&<div className="optimization-review" role="region" aria-label="Porównanie optymalizacji"><strong>{optimization.candidate.complete?`${optimization.before.points.length} → ${optimization.candidate.points.length} waypointów` : "Optymalizacja wymaga sprawdzenia"}{optimization.before.complete&&optimization.candidate.complete?` · ${optimization.before.path.length-1} → ${optimization.candidate.path.length-1} kroków`:""}</strong><button disabled={!optimization.candidate.complete} onClick={acceptOptimization}>Przyjmij</button><button onClick={()=>{setOptimization(null);frameRoute(plan?.path??points);}}>Odrzuć</button>{!optimization.candidate.complete&&optimization.candidate.gaps.map((g,i)=><button key={i} className="atlas-gap" onClick={()=>focusGap(g)}>Sprawdź odcinek {g.segment+1} → {g.segment+2}: {pointText(g.from)} → {pointText(g.to)}. {g.reason}</button>)}</div>}
    {!browseOnly&&repairSource&&(plan?.gaps.length||optimization?.candidate.gaps.length)?<button className="restore-recorded-transitions" title={`Uzupełnij brakujące przejścia z ${repairSource}; punkty i etykiety zostają bez zmian.`} disabled={busy||recorder?.active} onClick={restoreRecordedTransitions}>Odtwórz przejścia z nagrania</button>:null}
    {plan?.complete&&plan.requires_action_review&&!plan.requires_path_review&&!!plan.unmatched_recorded_actions&&<div className="recorded-action-review" role="status"><span>{plan.unmatched_recorded_actions} dodatkowe użycie poza przejściami — nie zostanie odtworzone.</span><button disabled={busy} onClick={()=>{setPlan({...plan,requires_action_review:false,ignored_recorded_actions:true});setSimulation(null);}}>Pomiń dodatkowe użycia</button></div>}
    {plan?.ignored_recorded_actions&&<small>Pominięto dodatkowe użycia; nagranie źródłowe pozostało bez zmian.</small>}
    {simulation&&<div className={`simulation-result ${simulation.ok?"ok":"review"}`} role="status"><strong>{simulation.ok?"✓ Symulacja zakończona":"Trasa wymaga poprawy"}</strong><span>{simulation.steps} kroków · piętra {simulation.floors.join(" → ")} · {simulation.transitions} przejść</span>{simulation.issues.map((issue,i)=><p key={i}>{issue}</p>)}</div>}
    {plan?.loop_closed&&plan.return_mode&&plan.return_mode!=="closed"&&<small>Powrót: {plan.return_mode==="reverse"?"waypointami w odwrotnej kolejności":"znaną drogą do początku"}.</small>}
    {error&&<p role="alert" className="v2-error">{error}</p>}</>;
  const saveButton=<MapTool icon={saved?"check":"save"} label={optimization?"Zapisz oryginał bez optymalizacji":saved?"Zapisano szkic":"Zapisz szkic"} disabled={busy||!dirty||!compatible||points.length<2||recorder?.active} onClick={saveDraft}/>;
  const tagEditor = !browseOnly && (draftId || points.length>0) && <DraftTags key={`${scope}/${draftId || recording}`}
    tags={draftId ? drafts.find(d=>d.id===draftId)?.tags ?? [] : newTags}
    onSave={async(tags,previous)=>{
      if (!draftId) { setNewTags(tags); return; }
      try {
        const data=await request("/api/map/drafts",{scope,id:draftId,operation:"set_tags",tags,previous});
        if(data.scope===scope)setDrafts(data.drafts);
      } catch(e) {
        const latest=await request("/api/map/drafts");
        if(latest.scope===scope)setDrafts(latest.drafts);
        throw e;
      }
    }}/>;
  const draftControls=!browseOnly&&<div className="map-draft-controls" role="group" aria-label="Zapis szkicu">
    <input aria-label="Nazwa edytowanego szkicu" maxLength={80} placeholder="Nowa trasa" value={draftName} disabled={busy||recorder?.active} onChange={e=>setDraftName(e.target.value)}/>
    <span role="status" className={dirty?"is-dirty":""}>{dirty?"Niezapisane zmiany":saved?"Zapisano":"Nowy szkic"}</span>
    <button className="map-save-draft" disabled={busy||!dirty||!compatible||points.length<2||recorder?.active} onClick={saveDraft}>{optimization?"Zapisz oryginał":"Zapisz"}</button>
    <button disabled={busy||(!dirty&&!optimization)||recorder?.active} onClick={()=>loadDraft(draftId)}>Odrzuć zmiany</button>
  </div>;
  const mapBody=active&&mapOpen?(
    <div className="atlas-editing-surface">
    {draftControls}
    {tagEditor}
    <div className={`atlas-map-workspace ${optimization?"atlas-review-lock":""}`}>

    <div className="atlas-toolbar map-integrated-toolbar" role="group" aria-label="Narzędzia mapy">
      <strong>{pointText(center)}</strong>
      <div className="map-tool-group"><MapTool icon="up" label="Piętro wyżej, minus 1" disabled={center[2]===0} onClick={()=>changeFloor(-1)}/><select aria-label="Piętro atlasu" title="Piętro · Ctrl + scroll" value={center[2]} onChange={e=>{setFollow(false);setCenter([center[0],center[1],Number(e.target.value)]);}}>{floors.map(z=><option key={z} value={z}>{z}</option>)}</select><MapTool icon="down" label="Piętro niżej, plus 1" disabled={center[2]===15} onClick={()=>changeFloor(1)}/></div>
      <div className="map-tool-group"><MapTool icon="plus" label="Przybliż mapę · Shift + scroll" disabled={radius<=2} onClick={()=>setRadius(r=>Math.max(2,r-(r>32?8:2)))}/><MapTool icon="minus" label="Oddal mapę · Shift + scroll" disabled={radius>=MAX_ATLAS_RADIUS} onClick={()=>setRadius(r=>Math.min(MAX_ATLAS_RADIUS,r+(r>=32?8:2)))}/><MapTool icon="focus" label="Śledź postać" disabled={simPlaying} aria-pressed={follow} onClick={()=>{setManual([...center]);setFollow(!follow);setDraw(false);}}/></div>
      <div className="map-tool-group"><MapTool icon="pan" label="Przesuwaj mapę · Ctrl + przeciągnij" aria-pressed={panMode} onClick={()=>{setPanMode(!panMode);setDraw(false);}}/><MapTool icon="edit" label="Edytuj waypointy" disabled={browseOnly||simPlaying||recorder?.active||busy} aria-pressed={draw} onClick={()=>{if(!draw)setCenter(center);setDraw(!draw);setPanMode(false);}}/><MapTool icon="undo" label="Cofnij zmianę · Ctrl + Z" disabled={!history.length||simPlaying} onClick={undoEdit}/></div>
      <MapTool icon="play" label="Szybka symulacja" disabled={busy||!plan||!!optimization||recorder?.active} onClick={simulate}/>
      <MapMore><button onClick={()=>{setOverview(draw||!overview);setDraw(false);}}>Cała trasa na wszystkich piętrach</button><button onClick={()=>setSidebar(!sidebar)}>{sidebar?"Ukryj szczegóły pola":"Szczegóły pola i przejść"}</button><button disabled={busy||!plan||!!optimization||recorder?.active} onClick={optimizeRoute}>Optymalizuj trasę</button>{lastOptimization&&<button onClick={undoOptimization}>Cofnij optymalizację</button>}<button disabled={browseOnly||recorder?.active} onClick={()=>{setDraw(true);setPanMode(false);setDrawMode(drawMode==="points"?"freehand":"points");}}>{drawMode==="points"?"Rysuj odręcznie":"Rysuj punktami"}</button></MapMore>
    </div>
    {draw&&<p className="atlas-draw-hint">Kliknij przejście, aby przypisać je do trasy. PPM: obiekt / waypoint.</p>}
    <details className="atlas-shortcuts"><summary>Skróty i legenda</summary><p className="atlas-help">Ctrl + przeciągnij: przesuwanie · Shift + scroll: zoom · Ctrl + scroll: piętro (w górę = −1) · {follow?"śledzenie postaci":"widok swobodny"}</p>
    {draw && <p className="atlas-help">{drawMode==="points"?"Klikaj puste pole, by dodać punkt na końcu, albo linię, by wstawić go między punktami. Przeciągnij punkt, by go przesunąć; PPM otwiera menu pola z usuwaniem waypointu i oznaczaniem obiektów. Kliknij pierwszy punkt ponownie, aby domknąć pętlę. Schody, dziury i teleporty dodawaj jako przejścia. Kliknij schody i wybierz wyjście; Ctrl + Z cofa zmianę.":"Przeciągnij po mapie, aby narysować szkic."} Planer połączy punkty przez znane pola i przejścia.</p>}
    </details>
    {!!segments.length && <div className="atlas-segments atlas-stage-toggles" aria-label="Etapy trasy i widoczność"><strong>Etapy</strong>{segments.map((s,i)=><div key={i} className={hiddenSegments.includes(i)?"stage-chip is-hidden":"stage-chip"} style={{borderColor:routeColor(i)}}>
      <input type="checkbox" aria-label={`Widoczność etapu ${i+1}`} checked={!hiddenSegments.includes(i)} onChange={()=>toggleStage(i)} style={{accentColor:routeColor(i)}}/>
      <button style={{color:routeColor(i)}} aria-pressed={i===segmentIndex} onClick={()=>showStage(i,s.points)} title={`Pokaż etap ${i+1} · piętro ${s.floor}`}>{i+1} · {s.floor}</button>
    </div>)}{hiddenSegments.length>0&&<button className="show-all-stages" onClick={()=>setHiddenSegments([])}>Pokaż wszystkie</button>}<label><input type="checkbox" checked={showTrace} onChange={e=>setShowTrace(e.target.checked)}/> Ślad oryginalny</label></div>}
    {overview && !draw && <AtlasRouteOverview segments={segments} hidden={hiddenSegments} floor={center[2]} onFloor={route=>{setFollow(false);frameRoute(route,true);}}/>}
    <div className={`atlas-layout ${sidebar?"":"map-expanded"}`}><div className="atlas-map-column">

      {transitionSpot && <div className="atlas-transition-backdrop" onClick={e=>{if(e.target===e.currentTarget&&!busy)setTransitionSpot(null);}}><div className="atlas-transition-picker" role="dialog" aria-modal="true" aria-label="Dodaj przejście między poziomami" onKeyDown={e=>{if(e.key==='Escape'&&!busy){e.stopPropagation();setTransitionSpot(null);}}}><header><strong>Przejście {pointText(transitionSpot)}</strong><button autoFocus disabled={busy} onClick={()=>setTransitionSpot(null)} aria-label="Zamknij wybór przejścia">×</button></header>
        
        {transitionOptions.length?transitionOptions.map(t=>{const choice=transitionChoice(t);return <button key={t.id} data-transition-choice={t.id} disabled={busy||!!choice.error} title={choice.error||undefined} onClick={()=>useTransition(t)}>{choice.label} · {t.exit[2]<t.entry[2]?"↑":t.exit[2]>t.entry[2]?"↓":"→"} {t.exit[2]}<small>{choice.error||(t.action==="use_with_then_step"?`Odkop, jeśli zasypana, i wejdź · narzędzia ${(t.tool_ids??[t.tool_id]).map(id=>id===3457?"łopata":`AiO (${id})`).join(" / ")}`:transitionActions[t.action as TransitionAction])} · wyjście {pointText(t.exit)}</small></button>;}):<p role="status">Brak przejścia pasującego do zapisanej mapy. Podejdź do obiektu, aby odświeżyć pole. Jeśli wyjście nie było jeszcze obserwowane, przejdź ręcznie i zatrzymaj się na chwilę po drugiej stronie.</p>}
        {!!transitionOptions.length&&<small>Zmienia tylko szkic. Piętro i zoom pozostają bez zmian.</small>}
        {transitionCell?.kind==="clear" && transitionCell.navigation?.every(f=>["rope_spot","ladder"].includes(f.kind)) && <button disabled={busy} onClick={()=>updatePoints([...points,transitionSpot])}>Dodaj zwykły punkt na tym polu</button>}
        {error && <p role="alert">{error}</p>}
      </div></div>}
      {transitionNotice&&<p className="atlas-transition-notice" role="status">{transitionNotice}{transitionExit&&<button onClick={()=>focusIssue(transitionExit)}>Pokaż wyjście · {transitionExit[2]}</button>}<button aria-label="Zamknij informację o przejściu" onClick={()=>setTransitionNotice("")}>×</button></p>}
      {!!executionIssues.length&&<details open className="atlas-execution-issues"><summary>Wykonanie trasy: {executionIssues.length} {executionIssues.length===1?'miejsce do poprawy':'miejsc do poprawy'}</summary><div>{executionIssues.map(issue=><button key={issue.key} onClick={()=>focusIssue(issue.pos)}>{issue.message}</button>)}</div></details>}
      {plan && !plan.complete && plan.gaps.map((g,i)=><button key={i} className="atlas-route-gap" onClick={()=>focusGap(g)}>Sprawdź odcinek {g.segment+1} → {g.segment+2} · {pointText(g.from)} → {pointText(g.to)}: {g.reason}</button>)}
      <svg ref={svg} className={`atlas-canvas ${draw?"drawing":""}`} viewBox={`0 0 ${grid.columns} ${grid.rows}`} preserveAspectRatio="none" aria-label="Zapisana mapa, Ctrl i przeciąganie przesuwa widok" role="img"
        onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
        onContextMenu={e=>{e.preventDefault();const p=locate(e as unknown as React.PointerEvent);if(!p)return;openTileMenu(p,e.clientX,e.clientY);}}
        onPointerCancel={cancelPointer} onLostPointerCapture={cancelPointer}>
        <rect width={grid.columns} height={grid.rows} fill="#14111c"/>
        {cells.map(c=>{const f=c.navigation?.[0],style=f?navigationStyles[f.kind]:null,destination=transitionDestination([c.x,c.y,c.z],[...(atlas?.route_transitions??[]),...(workingPlan?.transitions??[])]),names:Record<string,string>={stairs:"Schody",ladder:"Drabina",hole:"Dziura",descent:"Zejście",covered_hole:"Odkop",door:"Drzwi",level_gate:"Bramka",teleport:"Teleport",rope_spot:"Rope",passage:destination.startsWith('↓')?'Zejście':destination.startsWith('↑')?'Wejście':'Przejście',floor_change:'Przejście'},detailed=viewport.width/grid.columns>=43,suffix=f?.door_state_hint==="open"?"−":f?.door_state_hint==="closed"?"+":f?.source==="client_hint"?"?":f?.direction_hint==="up"?"↑":f?.direction_hint==="down"?"↓":"";
          return <g key={`${c.x},${c.y}`} opacity={c.live?1:.68}><rect x={c.x-center[0]+ox+.04} y={c.y-center[1]+oy+.04} width={.92} height={.92} fill={tileStyles[c.kind]?.fill ?? "#252331"}/>
            {style && <g pointerEvents="none"><rect x={c.x-center[0]+ox+.08} y={c.y-center[1]+oy+.08} width={.84} height={.84} rx={.10} fill="#131b24" fillOpacity={.68} stroke={style.color} strokeOpacity={.7} strokeWidth={.035}/>
              <text x={c.x-center[0]+ox+.5} y={c.y-center[1]+oy+(detailed ? .45 : .66)} textAnchor="middle" fontSize={detailed ? .23 : .45} fontWeight="700" fill={style.color}>{detailed?(names[f!.kind]??style.label):style.symbol}{!detailed&&suffix}</text>
              {detailed&&<text x={c.x-center[0]+ox+.5} y={c.y-center[1]+oy+.78} textAnchor="middle" fontSize={.22} fill={style.color}>{destination|| (f?.door_state_hint==="open"?"otw.":f?.door_state_hint==="closed"?"zamk.":suffix)}</text>}
            </g>}
            {c.live && !!c.occupant_types?.pet && <g pointerEvents="none" transform={`translate(${c.x-center[0]+ox+.8} ${c.y-center[1]+oy+.23})`}>
              <circle r={.19} fill="#132d2c" stroke={petStyle.color} strokeWidth={.035}/><text textAnchor="middle" dominantBaseline="central" fontSize={.26} fontWeight="700" fill={petStyle.color}>{petStyle.symbol}</text>
            </g>}
            <title>{`${pointText([c.x,c.y,c.z])} · ${c.live?"na żywo":"zapis historyczny"} · ${c.navigation?.map(f=>navigationStyles[f.kind].label).join(", ")} · ID ${c.items.map(i=>i.id).join(", ")}${c.live && c.pet_names?.length ? " · Pet: "+c.pet_names.join(", ") : ""}`}</title></g>;})}
        {showTrace && (routeSegments(plan?.original??points).filter((_,i)=>!hiddenSegments.includes(i)).flatMap(s=>plan?.recording_source?routeLines(s.points,center[2]):lines(s.points,center[2]))).map((line,i)=><polyline key={`o${i}`} points={line.map(xy).join(" ")} fill="none" stroke="#e8bc77" strokeWidth={.14} strokeDasharray=".3 .2"/>)}
        {onFloor.map(s=>{const lane=segmentLane(segments,s.index),color=routeColor(s.index),ends=[s.points[0],s.points.at(-1)!];
          const closed=ends[0].every((n,i)=>n===ends[1][i]);
          return <g key={`segment${s.index}`} transform={`translate(${lane} ${lane})`} aria-label={`Odcinek ${s.index+1} na ${s.floor}`}>
            {(plan || recorder?.active?routeLines(s.points,center[2]):lines(s.points,center[2])).map((line,i)=><g key={i}>
              <polyline points={line.map(xy).join(" ")} fill="none" stroke="#13111a" strokeWidth={.18} strokeOpacity={.4}/>
              <polyline points={line.map(xy).join(" ")} fill="none" stroke={color} strokeWidth={s.index===segmentIndex ? .11 : .09} strokeOpacity={.72} strokeDasharray={undefined}/>
              {directionMarkers(line).map((arrow,j)=><path key={j} d="M-.17-.13 L.04 0 L-.17 .13" transform={`translate(${arrow.x-center[0]+ox+.5} ${arrow.y-center[1]+oy+.5}) rotate(${arrow.angle})`} fill="none" stroke={color} strokeWidth={.065} opacity={.9} pointerEvents="none" aria-label="Kierunek trasy"/>)}
            </g>)}
          </g>;
        })}
        {approaches.map(a=><g key={`approach-${a.index}`} pointerEvents="none" aria-label={`Dojście do T${a.index+1}`}>
          <line x1={a.from[0]-center[0]+ox+.5} y1={a.from[1]-center[1]+oy+.5} x2={a.to[0]-center[0]+ox+.5} y2={a.to[1]-center[1]+oy+.5} stroke={routeColor(a.stage)} strokeWidth={.09} strokeDasharray=".12 .10" opacity={.8}/>
          <path d="M-.13-.10 L.02 0 L-.13 .10" transform={`translate(${(a.from[0]+a.to[0])/2-center[0]+ox+.5} ${(a.from[1]+a.to[1])/2-center[1]+oy+.5}) rotate(${a.angle})`} stroke={routeColor(a.stage)} strokeWidth={.055} fill="none"/>
          <title>{`T${a.index+1}: krok ${pointText(a.from)} → ${pointText(a.to)}, potem zmiana piętra`}</title>
        </g>)}
        {!draw&&endpoints.map(m=>{const color=routeColor(m.stage),label=m.labels.join(' · '),width=Math.max(.65,label.length*.16+.2);return <g key={`endpoint-${m.pos.join(',')}`} transform={`translate(${m.pos[0]-center[0]+ox+.5} ${m.pos[1]-center[1]+oy+.5})`} pointerEvents="none">
          <circle r={.10} fill={color}/><rect x={-.3} y={.27} width={width} height={.34} rx={.07} fill="#1b1722" fillOpacity={.92} stroke={color} strokeWidth={.025}/>
          <text x={-.2} y={.52} fontSize={.24} fill={color}>{label}</text><title>{`${label} · ${pointText(m.pos)} · A: początek etapu, B: koniec`}</title>
        </g>;})}
        {plan && unresolvedLegs(plan).flatMap((leg,i)=>hiddenSegments.includes(pointStages[(plan.gaps[0]?.segment??0)+i])?[]:lines(leg,center[2]).filter(line=>line.length>1).map((line,j)=><polyline key={`unresolved${i}-${j}`} points={line.map(xy).join(" ")} fill="none" stroke="#e2b174" strokeWidth={.1} strokeDasharray=".2 .2" opacity={.65}><title>Odcinek bez wyznaczonej drogi</title></polyline>))}
        {draw && drawMode==="points" && plan?.legs?.filter(leg=>!hiddenSegments.includes(pointStages[leg.from_index])).flatMap(leg=>routeLines(plan.path.slice(leg.start,leg.end+1),center[2]).map((line,i)=><polyline key={`insert${leg.from_index}-${i}`} points={line.map(xy).join(" ")} fill="none" stroke="transparent" strokeWidth={.6} pointerEvents="stroke" style={{cursor:"copy"}}
          onPointerDown={e=>{if(e.button!==0 || e.ctrlKey || panMode || busy)return;const p=locate(e);if(!p || points.some(a=>a.every((n,i)=>n===p[i])))return;e.stopPropagation();if(openTransition(p))return;updatePoints(insertAnchor(points,leg.from_index,p));}}>
          <title>{`Kliknij, aby wstawić punkt między ${leg.from_index+1} i ${leg.to_index+1}`}</title>
        </polyline>))}
        {!draw&&transfers.map((m,i)=>{const ids=m.indices.map(n=>`T${n+1}`).join('/'),direction=m.out?(m.target[2]<m.pos[2]?'↑':m.target[2]>m.pos[2]?'↓':'→'):'z ',label=`${ids} ${direction}${m.target[2]}`;
          const row=transfers.slice(0,i).filter(other=>other.pos.join(',')===m.pos.join(',')).length;
          return <g key={`transfer-${ids}-${m.out}`} role="button" tabIndex={0} aria-label={`${ids}: ${m.out?'wyjście na':'przyjście z'} piętro ${m.target[2]}`} style={{cursor:'pointer'}}
            onPointerDown={e=>{e.stopPropagation();setFollow(false);setSelected(m.target);setCenter([...m.target]);}} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setFollow(false);setSelected(m.target);setCenter([...m.target]);}}}>
            <rect x={m.pos[0]-center[0]+ox+.1} y={m.pos[1]-center[1]+oy+.1} width={.8} height={.8} rx={.08} fill="none" stroke="#fff0b3" strokeWidth={.025}/>
            <rect x={m.pos[0]-center[0]+ox+.97} y={m.pos[1]-center[1]+oy+.04+row*.34} width={label.length*.15+.18} height={.31} rx={.05} fill="#17131f" fillOpacity={.9}/>
            <text x={m.pos[0]-center[0]+ox+1.05} y={m.pos[1]-center[1]+oy+.27+row*.34} fontSize={.23} fill="#fff0b3">{label}</text>
            <title>{`${ids}: ${m.out?'wejście':'wyjście'} ${pointText(m.pos)} · ${m.out?'prowadzi do':'przyjście z'} ${pointText(m.target)} · ${transitionActions[m.action as TransitionAction]}. Kliknij, aby pokazać drugą stronę.`}</title>
          </g>;
        })}
        {draw && points.map((p,i)=>p[2]===center[2] && !hiddenSegments.includes(pointStages[i]) && !(isRouteClosed(points) && i===points.length-1) && <g key={`anchor${i}`}><title>{`${labels[i]||`Waypoint ${i+1}`} · ${pointText(p)}`}</title><circle cx={p[0]-center[0]+ox+(objectPositions.has(p.join(","))?.82:.5)} cy={p[1]-center[1]+oy+(objectPositions.has(p.join(","))?.16:.5)} r={objectPositions.has(p.join(","))?.20:.29} fill="#f3d496" fillOpacity={.78} stroke="#17131f" strokeOpacity={.7} strokeWidth={.045}/><text x={p[0]-center[0]+ox+(objectPositions.has(p.join(","))?.82:.5)} y={p[1]-center[1]+oy+(objectPositions.has(p.join(","))?.23:.60)} textAnchor="middle" fill="#17131f" fontSize={objectPositions.has(p.join(","))?.21:.29}>{i+1}</text></g>)}
        {selected?.[2]===center[2] && <rect x={selected[0]-center[0]+ox} y={selected[1]-center[1]+oy} width={1} height={1} fill="none" stroke="#f7db98" strokeWidth={.1}/>}
        {live?.[2]===center[2] && <circle cx={live[0]-center[0]+ox+.5} cy={live[1]-center[1]+oy+.5} r={.28} fill="white"/>}
        {sim?.current?.[2]===center[2] && <circle cx={sim.current[0]-center[0]+ox+.5} cy={sim.current[1]-center[1]+oy+.5} r={.4} fill="#45edc1" stroke="#fff" strokeWidth={.08}/>}
      </svg>
    </div><aside className="atlas-plan" hidden={!sidebar}>
      {!embedded&&waypointList}
      {selected && <details open className="atlas-inspector"><summary>Pole {pointText(selected)}</summary>{selectedCell?<>
        <small>{selectedCell.live?"Odczyt na żywo":`Ostatnio widziane: ${selectedCell.seen_at?new Date(selectedCell.seen_at*1000).toLocaleString():"czas nieznany"}`}</small>
        <p>{tileStyles[selectedCell.kind]?.label} · ID {selectedCell.items.map(i=>i.id).join(", ")}</p>
        {selectedCell.live && !!selectedCell.occupant_types?.pet && <p style={{color:petStyle.color}}>{petStyle.label} · {selectedCell.pet_names?.join(", ") ?? selectedCell.occupant_types.pet}</p>}
        <button onClick={e=>{const box=e.currentTarget.getBoundingClientRect();openTileMenu(selected,box.left,box.bottom);}}>Opcje pola</button>
        {!selectedCell.live && <small>Stan drzwi i przeszkód może już być inny.</small>}
        {selectedCell.navigation?.map((feature,i)=><NavigationDetails key={i} feature={feature} historical={!selectedCell.live}/>)}
        {selectedCell.transitions?.map(t=><button key={t.id} onClick={()=>setCenter(t.exit)}>Wyjście {pointText(t.exit)}<small>{transitionActions[t.action]} · {t.count}×</small></button>)}
        {!selectedCell.navigation?.length && <small>Brak rozpoznanego przejścia na tym polu.</small>}
      </>:<p>Brak zapisanych danych pola w tym widoku.</p>}</details>}
      {!embedded&&<><h3>Szkice</h3><select aria-label="Zapisany szkic" disabled={busy} value={draftId} onChange={e=>loadDraft(e.target.value)}><option value="">Nowy szkic</option>{drafts.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></>}
      <details className="v2-options"><summary>Wczytaj wcześniejsze nagranie</summary>
      <select aria-label="Nagranie do planowania" value={recording} onChange={e=>setRecording(e.target.value)}><option value="">Wybierz zapisane nagranie</option>{state?.cavebot?.recordings.map(r=><option key={r.name} value={r.name}>{r.name}</option>)}</select>
      <button disabled={!recording || busy || recorder?.active} onClick={()=>propose([],recording)}>Wczytaj nagranie</button>
      {adaptiveSource&&<button disabled={busy||recorder?.active} onClick={()=>propose([],recording,loop,[],true)}>Wczytaj pełny ślad</button>}
      </details>
      {busy && <p role="status">Przygotowuję podgląd…</p>}
      {mapWaiting&&<p role="status">Wczytywanie atlasu…</p>}{loadError && <p role="alert" className="text-amber-200">{loadError}</p>}{error && <p role="alert" className="text-red-300">{error}</p>}{atlas?.error && <p role="alert">{atlas.error}</p>}
      {atlas && <small>Zapamiętane pola: {atlas.total.toLocaleString("pl-PL")}</small>}
      {plan && <><h3>{plan.complete && !plan.requires_action_review&&!executionIssues.length?"Propozycja gotowa":"Plan do sprawdzenia"}</h3><p>{plan.path.length} pól · {plan.transitions.length} przejść · {plan.points.length} punktów szkicu</p><p className="atlas-help">{plan.warning}</p>
        {simIndex!==null && <p role="status" className="atlas-simulation">Symulacja · {simIndex+1}/{plan.path.length} · {sim?.current && pointText(sim.current)}<small>{sim?.transition?`${transitionActions[sim.transition.action as TransitionAction]} → ${pointText(sim.transition.exit)}`:sim?.next?"Krok po znanym polu":plan.complete?"Koniec propozycji":"Zatrzymano przed luką w trasie"}</small></p>}
        <small>Symulacja porusza wyłącznie znacznikiem. Nie sprawdza działania serwera ani dostępności bramki dla postaci.</small>
        {plan.transitions.map((t,i)=><button key={i} className="atlas-transition" onClick={()=>setCenter([...t.entry])}>T{i+1} · {pointText(t.entry)} → {pointText(t.exit)}<small>{transitionActions[t.action as TransitionAction]}{t.tool_id ? ` · narzędzie ${t.tool_id}`:""}{t.item_id?` · obiekt ${t.item_id}`:""}</small></button>)}
        {plan.gaps.map((g,i)=><button key={i} className="atlas-gap" onClick={()=>focusGap(g)}>{g.reason}: {pointText(g.from)} → {pointText(g.to)}</button>)}
        {!!plan.recorded_actions?.length && <details><summary>Czynności z nagrania ({plan.recorded_actions.length})</summary>{plan.recorded_actions.map((a,i)=><p key={i}>{a.type} · ID {a.item_id ?? "?"} · {a.player_pos?.join(", ")} · {a.matched_transition!=null?`Przejście T${a.matched_transition+1} — potwierdzone`:"Do sprawdzenia"}</p>)}</details>}
        <details><summary>Dane planu</summary><button onClick={async()=>{try{if(!window.dbvMap)throw new Error();await window.dbvMap.copyPlan(JSON.stringify(plan,null,2));setCopied(true);}catch{setError("Nie udało się skopiować planu.");}}}>{copied?"Skopiowano plan JSON":"Kopiuj plan JSON"}</button></details>
      </>}
    </aside></div>
    </div></div>
  ):null;
  return <section className={`atlas-panel ${embedded?"atlas-embedded":""} ${browseOnly?"atlas-browse":""}`} aria-label="Waypointy i edytor trasy">
    {tileMenu && menuCell && <TileContextMenu key={`${tileMenu.key}:${menuCell.items.map(i=>i.id).join(',')}`} position={tileMenu} cell={menuCell} scope={scope} editable={canAnnotate} onClose={()=>setTileMenu(null)} onCopy={copyMenuCell}
      waypoints={browseOnly?[]:points.flatMap((p,i)=>p.join(',')===tileMenu.key?[i]:[])}
      onRemoveWaypoint={browseOnly?undefined:index=>updatePoints(removeAnchor(points,index))}
      onTransition={!browseOnly&&(menuCell.navigation?.length||atlas?.route_transitions?.some(t=>t.entry.join(',')===tileMenu.key))?()=>openTransition([menuCell.x,menuCell.y,menuCell.z]):undefined}
      routeLocked={busy||!!optimization||!!recorder?.active||simPlaying}/>}
    <div className="route-workflow route-icon-toolbar" role="group" aria-label="Narzędzia waypointów"><div className="route-toolbar-heading">{routeEnabledControl??<strong>Waypointy</strong>}{points.length>0&&<small>{points.length} pkt</small>}</div>
      {embedded&&<MapTool icon="map" label="Otwórz mapę" disabled={recorder?.active} onClick={editHere}/>}
      <MapTool icon={recorder?.active?"stop":"record"} label={recorder?.active?"Zakończ nagranie":"Nagrywaj waypointy"} className={recorder?.active?"recording-button":"record-button"} disabled={busy||(!recorder?.active&&!canRecord)} onClick={()=>recordToggle()}/>
      {saveButton}<MapTool icon="play" label="Szybka symulacja" disabled={busy||!plan?.path.length||recorder?.active||!!optimization} onClick={simulate}/>
      <MapTool icon="loop" label={loop?"Powtarzanie trasy włączone":"Powtarzanie trasy wyłączone"} aria-pressed={loop} disabled={busy||!!optimization||recorder?.active||!!state?.adaptive_cavebot?.preparing||(state?.adaptive_cavebot?.draft_id===draftId&&["running","paused","ready"].includes(state?.adaptive_cavebot?.status??""))} onClick={()=>void toggleLoop()}/>
      <MapMore>{moreActions}
        {!browseOnly&&<button disabled={busy||!scope||!canRecord||dirty||!!optimization||recorder?.active} title={dirty?"Najpierw zapisz lub odrzuć bieżące zmiany szkicu.":"Zaproponuj trasę na podstawie ruchu i przyrostów XP."} onClick={()=>recordToggle("adaptive")}>Nagrywanie adaptacyjne</button>}
        <button onClick={()=>setRename(!rename)}>Zmień nazwę szkicu</button><button disabled={busy||!plan||recorder?.active||!!optimization} onClick={optimizeRoute}>Optymalizuj trasę</button>{lastOptimization&&<button disabled={busy||!!optimization} onClick={undoOptimization}>Cofnij optymalizację</button>}</MapMore>
    </div>
    {routePicker}
    {embedded && !mapOpen && tagEditor}
    {(rename||(!draftId&&(points.length>0||recorder?.active)))&&<div className="route-save-row"><input aria-label="Nazwa szkicu" maxLength={80} placeholder="Nazwa trasy" value={draftName} onChange={e=>{setDraftName(e.target.value);}}/></div>}
    {recorder?.active&&<p role="status" className="recording-status">● {recorder.mode==="adaptive"?"Nagrywanie adaptacyjne":"Nagrywanie"} · {recorder.position_count??0} pozycji</p>}
    {plan?.adaptive_evidence&&<p className="v2-inline-note">Obszary z XP: {plan.adaptive_evidence.areas} · Do szkicu wybrano {plan.adaptive_evidence.samples} z {plan.adaptive_evidence.total_samples} pozycji.</p>}
    {embedded&&progress&&progress.current!==null&&<div className="waypoint-progress" role="status">{state?.adaptive_cavebot?.status==='running'?'Do waypointu':state?.adaptive_cavebot?.status==='complete'?'Zakończono na waypointcie':'Zapamiętany waypoint'} <strong>{progress.current+1}/{progress.total}</strong>{progress.last_reached!==null&&<small> · zaliczony {progress.last_reached+1}</small>}</div>}
    {embedded&&waypointList}

    {review}
    {embedded?mapBody&&createPortal(<div className="route-map-overlay" role="dialog" aria-modal="true" aria-label="Mapa i edycja trasy"><header><strong>{draftName||"Nowa trasa"}</strong><span>{pointText(center)}</span><MapTool icon="layers" label={objectView?"Pokaż trasę":"Obiekty na żywo"} aria-pressed={objectView} onClick={()=>setObjectView(!objectView)}/><MapTool icon="close" label="Zamknij mapę" autoFocus onClick={()=>{setMapOpen(false);setSimPlaying(false);}}/></header>{objectView?<div className="route-object-view"><ObjectMap map={state?.passive_map}/></div>:mapBody}<footer>{review}</footer></div>,document.body):mapBody}
  </section>;
}
