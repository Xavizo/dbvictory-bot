"use client";

import { t, labels } from "@/lib/i18n";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { LiveMovementPreview, PassiveMapState, PassiveMapCell, MapNavigationFeature } from "@/lib/api";
import { labelVisiblePet, saveMapAnnotation } from "@/lib/api";
import {type Activation,activationLabel,activations,defaultActivation} from "@/lib/mapInteractions";
import { transitionActions } from "./TransitionLearningPanel";

import {tileStyles as styles,navigationStyles,petStyle,cutWebStyle,petDescription,displayOccupants,unidentifiedStyle,named} from "@/lib/mapVisuals";
const flags: Readonly<Record<string, string>> = labels("map.flag", ["ground", "on_top", "container", "force_use", "multi_use", "not_walkable", "not_moveable", "blocks_projectiles", "not_pathable", "pickupable", "floor_change"] as const);

const creatureStyles = {
  player: named("map.creature.player",{symbol:"G",color:"#38bdf8"}),
  npc: named("map.creature.npc",{symbol:"N",color:"#facc15"}),
  monster: named("map.creature.monster",{symbol:"M",color:"#fb7185"}),
  pet: petStyle,
  unknown: unidentifiedStyle,
};
const creatureKinds = ["player","npc","pet","monster","unknown"] as const;
function creatureCounts(cell: PassiveMapCell) {
  const counts = displayOccupants(cell);
  return creatureKinds.flatMap(k => {
    const count = counts[k] ?? 0;
    return count ? [{kind:k, count, ...creatureStyles[k]}] : [];
  });
}
export function copyCellText(cell: PassiveMapCell) {
  return [
    t("map.copy.tile",{place:`${cell.x}, ${cell.y}, ${cell.z}`,kind:styles[cell.kind]?.label ?? cell.kind}),
    ...(cell.web_cut?[cutWebStyle.label+" · ID 189"]:[]),
    `${t("map.serverBlocking")}: ${cell.server_blocking === undefined ? t("map.undetermined") : cell.server_blocking ? `${t("map.yes")} (${cell.server_blocking})` : t("map.no")}`,
    ...cell.items.map(i => `ID ${i.id} · ${t("map.layer",{n:i.stack})} · ${i.flags.map(f=>flags[f] ?? f).join(", ") || t("map.noFlags")}${i.lens_help !== undefined ? ` · ${t("map.helpCode",{n:i.lens_help})}` : ""}${i.minimap_color !== undefined ? ` · ${t("map.minimapColor",{n:i.minimap_color})}` : ""}`),
    ...(cell.navigation ?? []).map(f=>`${navigationStyles[f.kind].label} · ID ${f.item_id} · ${f.source === "observed" ? t("map.observedTimes",{n:f.observation_count ?? 1}) : f.source === "client_hint" ? t("map.clientHint") : f.source === "user_annotation" ? t("map.ownNote") : t("map.confirmed")}${f.required_level ? ` · ${t("map.requiredLevelCopy",{n:f.required_level})}` : ""}${f.activation_hint ? ` · ${activationLabel(f.activation_hint)}` : ""}${f.door_state_hint ? ` · ${t("map.state")}: ${t(f.door_state_hint === "open" ? "map.open" : f.door_state_hint === "closed" ? "map.closed" : "map.unknownState")}` : ""}${f.note ? ` · ${f.note}` : ""}`),
    ...creatureCounts(cell).map(c=>`${c.label}: ${c.count}${c.kind==="pet" && cell.pet_names?.length ? " · "+petDescription(cell) : ""}`),
    ...(cell.transitions ?? []).map(link=>t("map.observedTransition",{exit:link.exit.join(", "),action:transitionActions[link.action],n:link.count})),
  ].join("\n");
}

export function NavigationDetails({ feature, historical=false }: { feature: MapNavigationFeature; historical?:boolean }) {
  return <div className="mt-2 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs">
    <strong style={{ color: navigationStyles[feature.kind].color }}>{navigationStyles[feature.kind].label}</strong>
    <span className="text-gray-400"> · ID {feature.item_id} · {feature.source === "observed" ? t("map.observedTimes",{n:feature.observation_count ?? 1}) : t(feature.source === "client_hint" ? "map.clientHintCheck" : feature.source === "user_annotation" ? "map.yourNote" : feature.source === "confirmed_location" ? "map.confirmedHere" : "map.typeConfirmed")}</span>
    {feature.note && <p className="mt-1 text-gray-300">{feature.note}</p>}
    {feature.required_level && <p className="mt-1 text-amber-200">{t("map.requiredLevel",{n:feature.required_level})}</p>}
    {(feature.activation_hint || feature.direction_hint) && <p className="mt-1 text-gray-300">{t(feature.source === "user_annotation" ? "map.byYourNote" : "map.byMetadata")}
      {feature.activation_hint ? ` ${activationLabel(feature.activation_hint)}` : ""}
      {feature.direction_hint ? ` · ${t(feature.direction_hint === "up" ? "map.up" : "map.down")}` : ""}. {t("map.serverCheck")}
    </p>}
    {(feature.kind === "door" || feature.kind === "level_gate" || feature.door_state_hint) && <p className="mt-1 text-gray-300">
      {t(historical ? "map.lastState" : "map.currentState")} {t(feature.door_state_hint === "closed" ? "map.closed" : feature.door_state_hint === "open" ? "map.open" : "map.unknownState")}.
      {feature.lock_hint ? " "+t("map.lock") : ""}
      {" "+t(feature.closing_policy === "automatic" || feature.closing_policy === "after_exit" ? "map.closesAfter" : feature.closing_policy === "observed_open" ? "map.staysOpen" : feature.closing_policy === "manual" ? "map.closedByHand" : "map.closingUnknown")}
    </p>}
    {feature.behavior_hint && feature.behavior_hint!=="unknown" && <p className="mt-1 text-teal-200">{t("map.observedAction")}: {t(feature.behavior_hint==="use_enters" ? "map.useEnters" : "map.useOpens")}. {feature.observation_count ?? 1}×</p>}
    {feature.kind === "web" && <p className="mt-1 text-violet-200">{t("map.webHint")}</p>}
    {feature.kind === "rope_spot" && <p className="mt-1 text-sky-200">{t("map.ropeHint")}</p>}
    {feature.kind === "covered_hole" && <p className="mt-1 text-amber-200">{t("map.holeHint")}</p>}
    {feature.kind === "teleport" && <p className="mt-1 text-gray-300">{t("map.teleportHint")}</p>}
    {feature.kind === "passage" && feature.source!=="observed" && <p className="mt-1 text-gray-300">{t("map.passageHint")}</p>}
  </div>;
}

function CellDetails({ cell }: { cell: PassiveMapCell | undefined }) {
  if (!cell) return <div className="inspector-empty"><span aria-hidden="true">⌖</span><h3>{t("map.chooseTile")}</h3><p>{t("map.chooseTileNote")}</p></div>;
  return <>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <strong className="font-mono">({cell.x}, {cell.y}, {cell.z})</strong>
      <span>{styles[cell.kind]?.label ?? t("map.tile.unknown")}</span>
      {creatureCounts(cell).map(c=><span key={c.kind} style={{color:c.color}}>{c.label}: {c.count}{c.kind==="pet" && cell.pet_names?.length ? " · "+petDescription(cell) : ""}</span>)}
      {!!cell.occupant_types?.self && <span>{t("worldMap.you")}</span>}
    </div>
    {cell.reason && <p className="mt-1 text-xs text-gray-400">{cell.reason}</p>}
    {cell.web_cut&&<p>{cutWebStyle.label} · ID 189</p>}
    {cell.navigation?.map((feature) => <NavigationDetails key={`${feature.stack}-${feature.item_id}`} feature={feature} />)}
    {cell.transitions?.map(link => <div key={link.id} className="mt-2 rounded border border-teal-700 bg-gray-800 p-2 text-xs text-teal-200">
      {t("map.observedTransition",{exit:link.exit.join(", "),action:transitionActions[link.action],n:link.count})}
    </div>)}
    {!cell.navigation?.length && cell.stairs && <p className="mt-1 text-xs text-sky-300">{cell.stairs === "confirmed_id"
      ? t("map.stairsConfirmed")
      : t("map.floorFlag")}</p>}
    {cell.server_blocking !== undefined && <p className="mt-1 text-xs text-gray-400">
      {t("map.serverBlocking")}: {cell.server_blocking ? `${t("map.yes")} (${cell.server_blocking})` : t("map.no")}
    </p>}
    <div className="mt-2 max-h-28 overflow-y-auto space-y-1">
      {cell.items.map((item) => <div key={`${item.stack}-${item.id}`} className="text-xs text-gray-300">
        <span className="font-mono text-white">ID {item.id}</span>
        <span className="text-gray-500"> · {t("map.layer",{n:item.stack})} · </span>
        {item.flags.map((f) => flags[f] ?? f).join(", ") || t("map.noNavFlags")}
        {item.lens_help !== undefined && <span className="text-gray-500"> · {t("map.helpCode",{n:item.lens_help})}</span>}
      </div>)}
    </div>
  </>;
}

const annotationKinds = ["stairs","ladder","hole","descent","covered_hole","rope_spot","door","level_gate","teleport"] as const;
type AnnotationKind = typeof annotationKinds[number] | "";
function AnnotationEditor({cell,scope,enabled,initialKind,onClose}:{cell:PassiveMapCell;scope?:string|null;enabled:boolean;initialKind?:AnnotationKind;onClose:()=>void}) {
  const initial=cell.navigation?.find(f=>f.source==="user_annotation") ?? cell.navigation?.[0];
  const [itemId,setItemId]=useState(initial?.item_id ?? cell.items.at(-1)?.id ?? 0);
  const feature=cell.navigation?.find(f=>f.item_id===itemId);
  const [kind,setKind]=useState<string>(initialKind || (initial && annotationKinds.some(k=>k===initial.kind) ? initial.kind : ""));
  const [level,setLevel]=useState(initial?.required_level?.toString() ?? "");
  const [activation,setActivation]=useState<string>(defaultActivation(initialKind || initial?.kind || "",initial?.activation_hint));
  const [note,setNote]=useState(initial?.note ?? "");
  const [busy,setBusy]=useState(false), [message,setMessage]=useState("");
  const fieldClass="mt-1 w-full rounded border border-gray-600 bg-gray-950 p-2 text-xs";
  async function save(operation:"save"|"remove") {
    if(!scope || !kind) return;
    setBusy(true);setMessage("");
    try {
      await saveMapAnnotation({scope,operation,position:[cell.x,cell.y,cell.z],item_id:itemId,kind,
        required_level:kind==="level_gate" && level ? Number(level) : null,activation_hint:activation || null,note});
      setMessage(t(operation==="save" ? "map.noteSaved" : "map.noteRemoved"));
    } catch(e) {setMessage(e instanceof Error ? e.message : t("map.noteFailed"));}
    finally {setBusy(false);}
  }
  return <div className="map-annotation-form text-xs">
    <h3>{initialKind ? navigationStyles[initialKind].label : t("map.objectNote")}</h3>
    <p className="mt-2 text-gray-400">{t("map.noteScope")}</p>
    {!enabled && <p className="mt-2 text-amber-300">{t("map.needsLive")}</p>}
    <fieldset disabled={!enabled || busy} className="mt-3 grid grid-cols-1 gap-3 disabled:opacity-50 sm:grid-cols-2">
      <label>{t("map.objectLayer")}<select className={fieldClass} value={itemId} onChange={e=>{
        const id=Number(e.target.value), f=cell.navigation?.find(v=>v.item_id===id);setItemId(id);
        setKind(initialKind || (f && annotationKinds.some(k=>k===f.kind) ? f.kind : ""));setLevel(f?.required_level?.toString() ?? "");
        setActivation(defaultActivation(initialKind || f?.kind || "",f?.activation_hint));setNote(f?.note ?? "");setMessage("");
      }}>{cell.items.filter((v,i,a)=>a.findIndex(o=>o.id===v.id)===i).map(i=><option key={i.id} value={i.id}>ID {i.id} · {t("map.layer",{n:i.stack})}</option>)}</select></label>
      <label>{t("map.objectType")}<select className={fieldClass} value={kind} onChange={e=>{setKind(e.target.value);setActivation(defaultActivation(e.target.value));}}>
        <option value="">{t("map.chooseType")}</option>{annotationKinds.map(k=><option key={k} value={k}>{navigationStyles[k].label}</option>)}
      </select></label>
      {kind && <label>{t("transition.col.action")}
        {(activations[kind]?.length ?? 0)>1 ? <select className={fieldClass} value={activation} onChange={e=>setActivation(e.target.value)}>
          {(activations[kind] ?? []).map(k=><option key={k} value={k}>{activationLabel(k)}</option>)}
        </select> : <p className="annotation-default">{activationLabel(defaultActivation(kind) as Activation)}</p>}
      </label>}
      {["door","level_gate"].includes(kind) && <p className="annotation-default">{t("map.stateNow")}: {t(feature?.door_state_hint==="open" ? "map.openWalk" : feature?.door_state_hint==="closed" ? "map.closedOpen" : "map.toCheck")}. {t("map.recheckState")}</p>}
      {kind==="level_gate" && <label>{t("map.levelOptional")}<input className={fieldClass} type="number" min={1} max={100000} step={1} value={level} onChange={e=>setLevel(e.target.value)}/></label>}
      <label className="sm:col-span-2">{t("map.noteOptional")}<input className={fieldClass} maxLength={240} value={note} onChange={e=>setNote(e.target.value)}/></label>
      <div className="flex flex-wrap gap-3 sm:col-span-2">
        <button disabled={!kind || !itemId} onClick={()=>save("save")} className="rounded bg-sky-800 px-3 py-2 disabled:opacity-40">{t("map.saveNote")}</button>
        <button onClick={onClose} className="secondary-button">{t("common.close")}</button>
        {feature?.source==="user_annotation" && <button onClick={()=>save("remove")} className="rounded border border-gray-600 px-3 py-2">{t("map.removeNote")}</button>}
      </div>
    </fieldset>
    <p role="status" className="mt-2 text-gray-300">{message}</p>
  </div>;
}

export type TileMenuPosition = {key:string;x:number;y:number;scope:string|null|undefined;petRef?:string};
export function TileContextMenu({position,cell,scope,editable,onClose,onCopy,waypoints=[],onRemoveWaypoint,onTransition,onInsertWaypoint,routeLocked=false}:{position:TileMenuPosition;cell:PassiveMapCell;scope?:string|null;editable:boolean;onClose:()=>void;onCopy:()=>void;waypoints?:number[];onRemoveWaypoint?:(index:number)=>void;onTransition?:()=>void;onInsertWaypoint?:()=>void;routeLocked?:boolean}) {
  const ref=useRef<HTMLDivElement>(null);
  const [editing,setEditing]=useState<AnnotationKind|"pet"|null>(null);
  const [petName,setPetName]=useState("");
  const [petError,setPetError]=useState("");
  const [savingPet,setSavingPet]=useState(false);
  const sameCreature=editable && !!position.petRef && position.petRef===cell.pet_ref;
  async function savePet(name:string|null) {
    if(!sameCreature || !position.petRef || savingPet)return;
    setSavingPet(true);setPetError("");
    try {
      await labelVisiblePet([cell.x,cell.y,cell.z],position.petRef,name);
      onClose();
    } catch(error) {
      setPetError(error instanceof Error ? error.message : t("map.petFailed"));
    } finally {setSavingPet(false);}
  }
  const [point,setPoint]=useState({x:position.x,y:position.y});
  useLayoutEffect(()=>{
    const box=ref.current?.getBoundingClientRect();
    if(box) setPoint({x:Math.max(8,Math.min(position.x,window.innerWidth-box.width-8)),y:Math.max(8,Math.min(position.y,window.innerHeight-box.height-8))});
    ref.current?.querySelector<HTMLElement>(editing===null ? '[role="menuitem"]' : editing==="pet" ? 'input' : 'select')?.focus();
  },[position.x,position.y,editing]);
  useEffect(()=>{
    const fit=()=>{
      const box=ref.current?.getBoundingClientRect();if(!box) return;
      const x=Math.max(8,Math.min(position.x,window.innerWidth-box.width-8));
      const y=Math.max(8,Math.min(position.y,window.innerHeight-box.height-8));
      setPoint(old=>old.x===x && old.y===y ? old : {x,y});
    };
    const observer=new ResizeObserver(fit);if(ref.current) observer.observe(ref.current);
    window.addEventListener("resize",fit);
    return ()=>{observer.disconnect();window.removeEventListener("resize",fit);};
  },[position.x,position.y]);
  useEffect(()=>{
    const outside=(event:PointerEvent)=>{if(!ref.current?.contains(event.target as Node)) onClose();};
    document.addEventListener("pointerdown",outside);
    return ()=>document.removeEventListener("pointerdown",outside);
  },[onClose]);
  return createPortal(<div ref={ref} className={`tile-context-menu ${editing!==null ? "is-editor" : ""}`} style={{left:point.x,top:point.y}}
    role={editing===null ? "menu" : "dialog"} aria-label={`Pole (${cell.x}, ${cell.y}, ${cell.z})`} onContextMenu={e=>e.preventDefault()}
    onKeyDown={e=>{
      if(e.key==="Escape") {e.preventDefault();e.stopPropagation();onClose();return;}
      const nodes=Array.from(ref.current?.querySelectorAll<HTMLElement>(editing===null ? '[role="menuitem"]:not(:disabled)' : 'button:not(:disabled), select:not(:disabled), input:not(:disabled)') ?? []);
      const current=nodes.indexOf(document.activeElement as HTMLElement);
      if(e.key==="Tab" || (editing===null && ["ArrowDown","ArrowUp","Home","End"].includes(e.key))) {
        e.preventDefault();const next=e.key==="Home" ? 0 : e.key==="End" ? nodes.length-1 : (current+(e.key==="ArrowUp" || e.shiftKey ? -1 : 1)+nodes.length)%nodes.length;
        nodes[next]?.focus();
      }
    }}>
    <div className="tile-menu-heading"><strong>({cell.x}, {cell.y}, {cell.z})</strong><button aria-label={t("map.closeMenu")} onClick={onClose}>×</button></div>
    {editing===null ? <>
      {onTransition && <button role="menuitem" disabled={routeLocked} onClick={()=>{onTransition();onClose();}}>{t("map.addTransition")}</button>}
      {!onTransition && onInsertWaypoint && <button role="menuitem" disabled={routeLocked} onClick={()=>{onInsertWaypoint();onClose();}}>{t("map.insertWaypoint")}</button>}
      {onRemoveWaypoint && waypoints.map(index=><button role="menuitem" className="tile-menu-delete" key={index} disabled={routeLocked} onClick={()=>{onRemoveWaypoint(index);onClose();}}>{t("map.removeWaypoint",{n:index+1})}</button>)}
      <button role="menuitem" onClick={()=>{onCopy();onClose();}}>{t("map.copyTile")}</button>
      {sameCreature && (cell.pet_manual ?
        <button role="menuitem" disabled={savingPet} onClick={()=>savePet(null)}>{t("map.unmarkPet")}</button> :
        <button role="menuitem" onClick={()=>{setPetName(cell.pet_candidate_name??"");setEditing("pet");}}>{t("map.markPet")}</button>)}
      <div className="tile-menu-caption">OZNACZ OBIEKT NA TYM POLU</div>
      {annotationKinds.map(kind=><button role="menuitem" key={kind} disabled={!editable || !cell.items.length} onClick={()=>setEditing(kind)}><span style={{color:navigationStyles[kind].color}}>{navigationStyles[kind].symbol}</span>{navigationStyles[kind].label}<span className="tile-menu-chevron">›</span></button>)}
      <button role="menuitem" disabled={!editable || !cell.items.length} onClick={()=>setEditing("")}>{t("map.editNote")}</button>
      {!editable && <p className="tile-menu-help">{t("map.markNeedsLive")}</p>}
    </> : editing==="pet" ? <form className="map-annotation-form" onSubmit={e=>{e.preventDefault();savePet(petName.trim() || "Pet");}}>
      <label>{t("map.petName")}<input className="mt-1 w-full rounded border border-gray-600 p-2" value={petName} maxLength={80} onChange={e=>setPetName(e.target.value)}/></label>
      <button className="secondary-button mt-2" disabled={!sameCreature || savingPet}>{t("map.markAsPet")}</button>
      {!sameCreature && <p role="status">{t("map.reselect")}</p>}
    </form> : <AnnotationEditor cell={cell} scope={scope} enabled={editable} initialKind={editing} onClose={onClose}/>}
    {petError && <p role="alert">{petError}</p>}
  </div>,document.body);
}

export default function PassiveMapPanel({ map, preview, focusPosition }: { map?: PassiveMapState; preview?:LiveMovementPreview|null; focusPosition?:[number,number,number]|null }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [frozen, setFrozen] = useState<PassiveMapState | null>(null);
  const [copyResult, setCopyResult] = useState<{key:string; message:string} | null>(null);
  const [legendOpen,setLegendOpen]=useState(false);
  const [showPlan,setShowPlan]=useState(true);
  const [menu,setMenu]=useState<TileMenuPosition|null>(null);
  const sectionRef=useRef<HTMLElement>(null);
  useEffect(()=>{
    if(focusPosition) { setFrozen(null); setSelected(focusPosition.join(",")); sectionRef.current?.scrollIntoView({behavior:"smooth",block:"start"}); }
  },[focusPosition]);
  const shown = frozen ?? map;
  const keyOf = (c: PassiveMapCell) => `${c.x},${c.y},${c.z}`;
  const cell = shown?.cells.find((c) => keyOf(c) === (selected ?? hovered));
  const menuCell=shown?.cells.find(c=>keyOf(c)===menu?.key);
  const editable=!!map?.annotation_editable && !frozen && !map?.stale && map?.status==="ready";
  useEffect(()=>{setMenu(null);},[map?.annotation_scope,frozen,focusPosition]);
  useEffect(()=>{if(menu && !menuCell) setMenu(null);},[menu,menuCell]);
  const closeMenu=()=>{
    const key=menu?.key;setMenu(null);
    sectionRef.current?.querySelectorAll<SVGGElement>('g[data-cell]').forEach(node=>{if(node.dataset.cell===key) node.focus();});
  };
  const openMenu=(target:PassiveMapCell,x:number,y:number)=>{
    setSelected(keyOf(target));setMenu({key:keyOf(target),x,y,scope:map?.annotation_scope,petRef:target.pet_ref});
  };
  async function copyCell(target:PassiveMapCell) {
    const key=keyOf(target);
    try {
      if(!window.dbvMap) throw new Error("Clipboard bridge unavailable");
      await window.dbvMap.copyCell(copyCellText(target));setCopyResult({key,message:t("map.copied")});
    } catch {setCopyResult({key,message:t("map.copyFailed")});}
  }
  const ready = !!shown?.cells.length && shown.center && shown.origin;
  const plan = showPlan && !frozen && !shown?.stale && shown?.status === "ready" &&
    preview?.path[0]?.every((v,i)=>v===shown.center?.[i]) ? preview : null;
  const axis = (value:number, i:number) => (value - shown!.origin![i]) * 22 + 11;
  return <section ref={sectionRef} aria-label={t("map.passive")} className="map-panel">
    <div className="map-panel-header">
      <div>
        <h2>{t("map.title")} <span>{t("map.passiveBadge")}</span></h2>
        <p>{t("map.lead")}</p>
      </div>
      <div className="flex items-center gap-2">
      <button className="secondary-button" aria-pressed={showPlan} onClick={()=>setShowPlan(!showPlan)}>{t("map.plan")} {showPlan?"✓":""}</button>
      <button className="secondary-button" aria-expanded={legendOpen} onClick={()=>setLegendOpen(!legendOpen)}>{t("map.legend")}</button>
      <button disabled={!ready} onClick={() => { setFrozen(frozen ? null : structuredClone(map!)); setSelected(null); }}
        className="secondary-button">
        {t(frozen ? "map.resume" : "map.freeze")}
      </button>
      </div>
    </div>
    <div hidden={!legendOpen} className="map-legend">
    <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-gray-300">
      {Object.entries(styles).map(([kind, style]) => <span key={kind} className="inline-flex items-center gap-1.5">
        <span className="inline-block h-3 w-3 rounded-sm border border-gray-500" style={{ backgroundColor: style.fill }} />{style.label}
      </span>)}
      {creatureKinds.map(k=><span key={k} style={{color:creatureStyles[k].color}}>{creatureStyles[k].symbol} {creatureStyles[k].label}</span>)}<span>◎ {t("map.you")}</span>
    </div>
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {(["stairs", "ladder", "hole", "rope_spot", "web", "door", "level_gate", "teleport"] as const).map((kind) =>
        <span key={kind} style={{ color: navigationStyles[kind].color }}><b>{navigationStyles[kind].symbol}</b> {navigationStyles[kind].label}</span>)}
      <span className="text-gray-400">? = {t("map.clientHint")}</span>
      <span className="text-teal-300">↗ = {t("map.observedLower")}</span>
    </div>
    </div>
    {ready ? <div className="map-grid-layout"><div className="map-canvas-column">
      <div className="map-coordinates">
        <span>({shown.center![0]}, {shown.center![1]}, {shown.center![2]}) · {t("map.north")} ↑</span>
        <span className={frozen || shown.stale ? "text-amber-300" : "text-emerald-300"}>
          {frozen ? t("map.frozen") : shown.stale ? t("map.updating") : "● "+t("map.live")}
        </span>
      </div>
      <div className="map-stage"><svg role="group" aria-label={t("map.gridAria")} viewBox={`0 0 ${shown.width! * 22} ${shown.height! * 22}`}
        className={`map-canvas ${shown.stale && !frozen ? "opacity-90" : ""}`}>
        {shown.cells.map((c) => {
          const key = keyOf(c), px = (c.x - shown.origin![0]) * 22, py = (c.y - shown.origin![1]) * 22;
          const player = c.x === shown.center![0] && c.y === shown.center![1];
          const feature = c.navigation?.find((f) => f.source === "user_annotation") ?? c.navigation?.find((f) => f.source === "confirmed_location") ?? c.navigation?.find((f) => f.source === "confirmed_id") ?? c.navigation?.[0];
          const baseNav = feature ? navigationStyles[feature.kind] : c.web_cut ? cutWebStyle : c.transitions?.length ? named("map.observed",{symbol:"↗",color:"#5eead4"}) : undefined;
          const nav = baseNav && feature?.door_state_hint ? {...baseNav,
            label:`${baseNav.label} · ${t(feature.door_state_hint==="open" ? "map.open" : feature.door_state_hint==="closed" ? "map.closed" : "map.stateUnknown")}`,
            color:feature.door_state_hint==="open" ? "#6ee7b7" : feature.door_state_hint==="closed" ? "#fda4af" : baseNav.color} : baseNav;
          const occupants = creatureCounts(c);
          const label = `${c.x}, ${c.y}, ${c.z}: ${nav ? `${nav.label}${feature?.source === "client_hint" ? "?" : ""}; ` : ""}${styles[c.kind]?.label ?? t("map.tile.unknown")}${occupants.length ? `; ${occupants.map(o=>`${o.label}: ${o.count}`).join(", ")}` : ""}`;
          return <g key={key} data-cell={key} role="button" aria-label={label} aria-haspopup="menu" tabIndex={0}
            onMouseEnter={() => setHovered(key)} onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(key)} onClick={() => setSelected(selected === key ? null : key)}
            onContextMenu={e=>{e.preventDefault();openMenu(c,e.clientX,e.clientY);}}
            onKeyDown={e=>{
              if(e.key==="ContextMenu" || (e.shiftKey && e.key==="F10")) {e.preventDefault();const rect=e.currentTarget.getBoundingClientRect();openMenu(c,rect.right,rect.bottom);}
              else if(e.key==="Enter" || e.key===" ") {e.preventDefault();setSelected(selected===key ? null : key);}
            }}
            className="cursor-help focus:outline-none">
            <title>{label} · ID {c.items.map((i) => i.id).join(", ") || "—"}</title>
            <rect x={px + 1} y={py + 1} width={20} height={20} rx={2} fill={styles[c.kind]?.fill ?? styles.unknown.fill}
              stroke={selected === key ? "#e4fff8" : nav?.color ?? "transparent"} strokeWidth={selected===key ? 2 : 1.2}
              strokeDasharray={selected !== key && feature?.source === "client_hint" ? "3 2" : undefined} />
            {nav && <>
              <rect x={px + 3} y={py + 3} width={16} height={16} rx={3} fill="#111827" />
              <text x={px + 11} y={py + 15} textAnchor="middle" fill={nav.color} fontSize={12} fontWeight="bold">{nav.symbol}</text>
              {feature?.door_state_hint && <text x={px+19} y={py+19} textAnchor="end" fill={nav.color} fontSize={7}>{feature.door_state_hint==="open" ? "○" : feature.door_state_hint==="closed" ? "×" : "?"}</text>}
              {!!feature?.observation_count && <text x={px+19} y={py+7} textAnchor="end" fill="#6ee7b7" fontSize={6}>✓</text>}
              {feature?.source === "client_hint" && <text x={px + 19} y={py + 8} textAnchor="end" fill="#fff" fontSize={7}>?</text>}
            </>}
            {occupants.map((o,index)=>{
              const dx = index%2 ? 6 : 16, dy = index<2 ? 6 : 16;
              return <g key={o.kind}><circle cx={px+dx} cy={py+dy} r={4.5} fill={o.color} stroke="#111827" strokeWidth={0.7}/>
                <text x={px+dx} y={py+dy+2.2} textAnchor="middle" fill="#111827" fontSize={6.5} fontWeight="bold">{o.symbol}</text></g>;
            })}
            {player && <><circle cx={px + (nav ? 18 : 11)} cy={py + (nav ? 18 : 11)} r={nav ? 3 : 6} fill="#fff" stroke="#111827" strokeWidth={1.5} />{!nav && <circle cx={px + 11} cy={py + 11} r={2} fill="#111827" />}</>}
          </g>;
        })}
        {plan && <g pointerEvents="none" aria-label={t("map.planAria")}>
          {plan.path.length>1 && <polyline points={plan.path.map(([x,y])=>[axis(x,0),axis(y,1)].join(",")).join(" ")}
            fill="none" stroke="#78ecd3" strokeWidth="1.4" strokeDasharray="3 2" strokeLinecap="round"/>}
          {plan.stop && plan.stop[2]===shown.center![2] && <rect x={axis(plan.stop[0],0)-8} y={axis(plan.stop[1],1)-8}
            width="16" height="16" rx="4" fill="none" stroke="#ffd375" strokeWidth="1.6"/>}
        </g>}
      </svg></div>
      <div className="map-quick-legend"><span>◎ {t("worldMap.you")}</span>{creatureKinds.map(k=><span key={k} style={{color:creatureStyles[k].color}}><b>{creatureStyles[k].symbol}</b> {creatureStyles[k].label}</span>)}
        {showPlan && <><span style={{color:"#78ecd3"}}>┄ {t("map.plan")}</span>
          <span style={{color:"#ffd375"}}>{"▢ "+t("map.stop")+" "+(plan?.stop ? plan.stop.join(", ") : "—")}</span></>}
      </div>
      <p className="map-caption">{t("map.caption")}</p>
      </div><aside className="map-inspector" aria-label={t("map.inspectorAria")}>
        <div className="inspector-heading">{t("map.inspector")} {selected && <span>{t("map.pinned")}</span>}</div>
        <CellDetails cell={cell} />
        {cell && <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="copy-cell-button" onClick={()=>copyCell(cell)}>{t("map.copyTile")}</button>
          <button className="secondary-button" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();openMenu(cell,rect.left,rect.bottom);}}>{t("map.menu")}</button>
          <span role="status" className="text-xs text-gray-400">{copyResult?.key === keyOf(cell) ? copyResult.message : ""}</span>
        </div>}
        <p className="mt-3 text-xs text-gray-400">{t("map.menuHint")}</p>
        {selected && <button className="mt-2 text-xs text-sky-300 underline" onClick={() => setSelected(null)}>{t("map.release")}</button>}
      </aside></div> : <div className="empty-panel map-empty"><span aria-hidden="true">⌖</span><h2>{t(shown?.status==="waiting" ? "map.waiting" : "map.unavailable")}</h2><p>{shown?.message ?? t("map.afterLogin")}</p></div>}
    {menu && menuCell && <TileContextMenu key={`${menu.key}:${menuCell.items.map(i=>i.id).join(",")}`} position={menu} cell={menuCell} scope={menu.scope}
      editable={!!editable && menu.scope===map?.annotation_scope} onClose={closeMenu} onCopy={()=>copyCell(menuCell)}/>}
    {map?.annotation_error && <p className="mt-2 text-xs text-amber-300">{map.annotation_error}</p>}
    <details className="map-source"><summary>{t("map.about")}</summary>
      <p className="mt-2">{t("map.aboutRed")}</p>
      <p className="mt-1">{shown?.asset?.message ?? t("map.aboutAssets")}</p>
      {shown?.catalogue_items && <p>{t("map.catalogue",{n:shown.catalogue_items,width:shown.width ?? 0,height:shown.height ?? 0})}</p>}
    </details>
  </section>;
}
