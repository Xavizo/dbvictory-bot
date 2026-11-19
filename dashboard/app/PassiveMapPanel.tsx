"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { LiveMovementPreview, PassiveMapState, PassiveMapCell, MapNavigationFeature } from "@/lib/api";
import { labelVisiblePet, saveMapAnnotation } from "@/lib/api";
import {activationLabels,activations,defaultActivation} from "@/lib/mapInteractions";
import { transitionActions } from "./TransitionLearningPanel";

import {tileStyles as styles,navigationStyles,petStyle} from "@/lib/mapVisuals";
const flags: Record<string, string> = {
  ground: "podłoże", on_top: "górna warstwa", container: "kontener", force_use: "używalny", multi_use: "użycie na celu",
  not_walkable: "blokuje chodzenie", not_moveable: "nieruchomy", blocks_projectiles: "blokuje pociski",
  not_pathable: "omijany w trasie", pickupable: "do podniesienia", floor_change: "flaga zmiany piętra",
};

const creatureStyles = {
  player: {label:"Gracz",symbol:"G",color:"#38bdf8"},
  npc: {label:"NPC",symbol:"N",color:"#facc15"},
  monster: {label:"Potwór",symbol:"M",color:"#fb7185"},
  pet: petStyle,
  unknown: {label:"Typ nieustalony",symbol:"?",color:"#c084fc"},
};
const creatureKinds = ["player","npc","pet","monster","unknown"] as const;
function creatureCounts(cell: PassiveMapCell) {
  return creatureKinds.filter(k => (cell.occupant_types?.[k] ?? (k === "unknown" && !cell.occupant_types ? cell.occupants : 0)) > 0)
    .map(k => ({kind:k, count:cell.occupant_types?.[k] ?? cell.occupants, ...creatureStyles[k]}));
}
export function copyCellText(cell: PassiveMapCell) {
  return [
    `Pole (${cell.x}, ${cell.y}, ${cell.z}) — ${styles[cell.kind]?.label ?? cell.kind}`,
    `Blokada przekazana przez serwer: ${cell.server_blocking === undefined ? "nieustalona" : cell.server_blocking ? `tak (${cell.server_blocking})` : "nie"}`,
    ...cell.items.map(i => `ID ${i.id} · warstwa ${i.stack} · ${i.flags.map(f=>flags[f] ?? f).join(", ") || "brak flag"}${i.lens_help !== undefined ? ` · kod pomocy ${i.lens_help}` : ""}${i.minimap_color !== undefined ? ` · kolor minimapy ${i.minimap_color}` : ""}`),
    ...(cell.navigation ?? []).map(f=>`${navigationStyles[f.kind].label} · ID ${f.item_id} · ${f.source === "observed" ? `zaobserwowano ${f.observation_count ?? 1}×` : f.source === "client_hint" ? "wskazówka klienta" : f.source === "user_annotation" ? "własny opis pola" : "potwierdzone"}${f.required_level ? ` · wymagany poziom ${f.required_level} (zgłoszenie użytkownika dla tego pola)` : ""}${f.activation_hint ? ` · ${activationLabels[f.activation_hint]}` : ""}${f.door_state_hint ? ` · stan: ${f.door_state_hint === "open" ? "otwarte" : f.door_state_hint === "closed" ? "zamknięte" : "nieznany"}` : ""}${f.note ? ` · ${f.note}` : ""}`),
    ...creatureCounts(cell).map(c=>`${c.label}: ${c.count}${c.kind==="pet" && cell.pet_names?.length ? " · "+cell.pet_names.join(", ") : ""}`),
    ...(cell.transitions ?? []).map(t=>`Zaobserwowane przejście → (${t.exit.join(", ")}) · ${transitionActions[t.action]} · ${t.count} razy`),
  ].join("\n");
}

export function NavigationDetails({ feature, historical=false }: { feature: MapNavigationFeature; historical?:boolean }) {
  return <div className="mt-2 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-xs">
    <strong style={{ color: navigationStyles[feature.kind].color }}>{navigationStyles[feature.kind].label}</strong>
    <span className="text-gray-400"> · ID {feature.item_id} · {feature.source === "observed" ? `zaobserwowano ${feature.observation_count ?? 1}×` : feature.source === "client_hint" ? "wskazówka klienta — do sprawdzenia" : feature.source === "user_annotation" ? "Twój opis tego pola" : feature.source === "confirmed_location" ? "potwierdzone dla tego pola" : "typ potwierdzony"}</span>
    {feature.note && <p className="mt-1 text-gray-300">{feature.note}</p>}
    {feature.required_level && <p className="mt-1 text-amber-200">Wymagany poziom: {feature.required_level}. Źródło: zgłoszony przez Ciebie opis tej bramki.</p>}
    {(feature.activation_hint || feature.direction_hint) && <p className="mt-1 text-gray-300">{feature.source === "user_annotation" ? "Według Twojego opisu:" : "Według metadanych:"}
      {feature.activation_hint ? ` ${activationLabels[feature.activation_hint]}` : ""}
      {feature.direction_hint ? ` · ${feature.direction_hint === "up" ? "w górę" : "w dół"}` : ""}. Działanie na serwerze wymaga sprawdzenia.
    </p>}
    {(feature.kind === "door" || feature.kind === "level_gate" || feature.door_state_hint) && <p className="mt-1 text-gray-300">
      {historical ? "Ostatnio widziany stan obiektu:" : "Bieżący stan obiektu:"} {feature.door_state_hint === "closed" ? "zamknięte" : feature.door_state_hint === "open" ? "otwarte" : "nieznany"}.
      {feature.lock_hint ? " Metadane sugerują zamek; nie określają wymaganego levelu." : ""}
      {feature.closing_policy === "automatic" || feature.closing_policy === "after_exit" ? " Zaobserwowano zamknięcie po wyjściu." : feature.closing_policy === "observed_open" ? " Pozostały otwarte przez co najmniej 2 s po przejściu." : feature.closing_policy === "manual" ? " Zaobserwowano ręczne zamknięcie." : " Sposób zamykania: nieustalony."}
    </p>}
    {feature.behavior_hint && feature.behavior_hint!=="unknown" && <p className="mt-1 text-teal-200">Zaobserwowana czynność: {feature.behavior_hint==="use_enters" ? "użycie przenosi postać na pole obiektu" : "użycie otwiera przejście; otwarcie bez przemieszczenia postaci"}. {feature.observation_count ?? 1}×</p>}
    {feature.kind === "web" && <p className="mt-1 text-violet-200">AiO 8899 lub 8900 przecina pajęczynę. Ruch po zwolnieniu pola.</p>}
    {feature.kind === "rope_spot" && <p className="mt-1 text-sky-200">Rope 3003 · AiO 8899 · czar fly up. Cel i skuteczną metodę zapisze ręczne przejście.</p>}
    {feature.kind === "covered_hole" && <p className="mt-1 text-amber-200">Shovel 3457 lub AiO 8899 → otwarcie dziury → wejście na pole.</p>}
    {feature.kind === "teleport" && <p className="mt-1 text-gray-300">Cel wymaga obserwacji przejścia. Blokada pola nie wyklucza teleportu.</p>}
    {feature.kind === "passage" && feature.source!=="observed" && <p className="mt-1 text-gray-300">Oznaczenie minimapy i flagi sugerują przejście. Rodzaj, kierunek i działanie niepotwierdzone.</p>}
  </div>;
}

function CellDetails({ cell }: { cell: PassiveMapCell | undefined }) {
  if (!cell) return <div className="inspector-empty"><span aria-hidden="true">⌖</span><h3>Wybierz pole na mapie</h3><p>Kliknij kafelek, aby przypiąć jego szczegóły, skopiować dane lub oznaczyć obiekt.</p></div>;
  return <>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
      <strong className="font-mono">({cell.x}, {cell.y}, {cell.z})</strong>
      <span>{styles[cell.kind]?.label ?? "Nieznane"}</span>
      {creatureCounts(cell).map(c=><span key={c.kind} style={{color:c.color}}>{c.label}: {c.count}{c.kind==="pet" && cell.pet_names?.length ? " · "+cell.pet_names.join(", ") : ""}</span>)}
      {!!cell.occupant_types?.self && <span>Twoja postać</span>}
    </div>
    {cell.reason && <p className="mt-1 text-xs text-gray-400">{cell.reason}</p>}
    {cell.navigation?.map((feature) => <NavigationDetails key={`${feature.stack}-${feature.item_id}`} feature={feature} />)}
    {cell.transitions?.map(t => <div key={t.id} className="mt-2 rounded border border-teal-700 bg-gray-800 p-2 text-xs text-teal-200">
      Zaobserwowane przejście → ({t.exit.join(", ")}) · {transitionActions[t.action]} · {t.count} razy
    </div>)}
    {!cell.navigation?.length && cell.stairs && <p className="mt-1 text-xs text-sky-300">{cell.stairs === "confirmed_id"
      ? "Schody: ID 4560 potwierdzone ręcznie. Kierunek i cel przejścia nie są jeszcze znane."
      : "Definicja zawiera flagę zmiany piętra; przejście wymaga sprawdzenia."}</p>}
    {cell.server_blocking !== undefined && <p className="mt-1 text-xs text-gray-400">
      Blokada przekazana przez serwer: {cell.server_blocking ? `tak (${cell.server_blocking})` : "nie"}
    </p>}
    <div className="mt-2 max-h-28 overflow-y-auto space-y-1">
      {cell.items.map((item) => <div key={`${item.stack}-${item.id}`} className="text-xs text-gray-300">
        <span className="font-mono text-white">ID {item.id}</span>
        <span className="text-gray-500"> · warstwa {item.stack} · </span>
        {item.flags.map((f) => flags[f] ?? f).join(", ") || "brak flag nawigacyjnych"}
        {item.lens_help !== undefined && <span className="text-gray-500"> · kod pomocy {item.lens_help}</span>}
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
      setMessage(operation==="save" ? "Zapisano Twój opis. Mapa zaktualizuje oznaczenie." : "Usunięto własny opis. Pozostają metadane i obserwacje.");
    } catch(e) {setMessage(e instanceof Error ? e.message : "Nie udało się zapisać opisu.");}
    finally {setBusy(false);}
  }
  return <div className="map-annotation-form text-xs">
    <h3>{initialKind ? navigationStyles[initialKind].label : "Opis obiektu"}</h3>
    <p className="mt-2 text-gray-400">Opis dotyczy tego pola i wybranego ID. Nie potwierdza udanego przejścia i nie steruje postacią.</p>
    {!enabled && <p className="mt-2 text-amber-300">Zapisywanie wymaga bieżącej mapy na żywo.</p>}
    <fieldset disabled={!enabled || busy} className="mt-3 grid grid-cols-1 gap-3 disabled:opacity-50 sm:grid-cols-2">
      <label>Obiekt / warstwa<select className={fieldClass} value={itemId} onChange={e=>{
        const id=Number(e.target.value), f=cell.navigation?.find(v=>v.item_id===id);setItemId(id);
        setKind(initialKind || (f && annotationKinds.some(k=>k===f.kind) ? f.kind : ""));setLevel(f?.required_level?.toString() ?? "");
        setActivation(defaultActivation(initialKind || f?.kind || "",f?.activation_hint));setNote(f?.note ?? "");setMessage("");
      }}>{cell.items.filter((v,i,a)=>a.findIndex(o=>o.id===v.id)===i).map(i=><option key={i.id} value={i.id}>ID {i.id} · warstwa {i.stack}</option>)}</select></label>
      <label>Typ obiektu<select className={fieldClass} value={kind} onChange={e=>{setKind(e.target.value);setActivation(defaultActivation(e.target.value));}}>
        <option value="">Wybierz typ…</option>{annotationKinds.map(k=><option key={k} value={k}>{navigationStyles[k].label}</option>)}
      </select></label>
      {kind && <label>Czynność
        {(activations[kind]?.length ?? 0)>1 ? <select className={fieldClass} value={activation} onChange={e=>setActivation(e.target.value)}>
          {(activations[kind] ?? []).map(k=><option key={k} value={k}>{activationLabels[k]}</option>)}
        </select> : <p className="annotation-default">{activationLabels[defaultActivation(kind) as keyof typeof activationLabels]}</p>}
      </label>}
      {["door","level_gate"].includes(kind) && <p className="annotation-default">Stan teraz: {feature?.door_state_hint==="open" ? "otwarte — wystarczy wejść" : feature?.door_state_hint==="closed" ? "zamknięte — najpierw otwarcie" : "do sprawdzenia"}. Stan trzeba sprawdzić ponownie przed przejściem.</p>}
      {kind==="level_gate" && <label>Wymagany poziom (opcjonalnie)<input className={fieldClass} type="number" min={1} max={100000} step={1} value={level} onChange={e=>setLevel(e.target.value)}/></label>}
      <label className="sm:col-span-2">Notatka (opcjonalnie)<input className={fieldClass} maxLength={240} value={note} onChange={e=>setNote(e.target.value)}/></label>
      <div className="flex flex-wrap gap-3 sm:col-span-2">
        <button disabled={!kind || !itemId} onClick={()=>save("save")} className="rounded bg-sky-800 px-3 py-2 disabled:opacity-40">Zapisz opis pola</button>
        <button onClick={onClose} className="secondary-button">Zamknij</button>
        {feature?.source==="user_annotation" && <button onClick={()=>save("remove")} className="rounded border border-gray-600 px-3 py-2">Usuń własny opis</button>}
      </div>
    </fieldset>
    <p role="status" className="mt-2 text-gray-300">{message}</p>
  </div>;
}

export type TileMenuPosition = {key:string;x:number;y:number;scope:string|null|undefined;petRef?:string};
export function TileContextMenu({position,cell,scope,editable,onClose,onCopy,waypoints=[],onRemoveWaypoint,onTransition,routeLocked=false}:{position:TileMenuPosition;cell:PassiveMapCell;scope?:string|null;editable:boolean;onClose:()=>void;onCopy:()=>void;waypoints?:number[];onRemoveWaypoint?:(index:number)=>void;onTransition?:()=>void;routeLocked?:boolean}) {
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
      setPetError(error instanceof Error ? error.message : "Nie udało się zmienić oznaczenia peta.");
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
    <div className="tile-menu-heading"><strong>({cell.x}, {cell.y}, {cell.z})</strong><button aria-label="Zamknij menu pola" onClick={onClose}>×</button></div>
    {editing===null ? <>
      {onTransition && <button role="menuitem" disabled={routeLocked} onClick={()=>{onTransition();onClose();}}>Dodaj przejście do trasy…</button>}
      {onRemoveWaypoint && waypoints.map(index=><button role="menuitem" className="tile-menu-delete" key={index} disabled={routeLocked} onClick={()=>{onRemoveWaypoint(index);onClose();}}>Usuń waypoint {index+1}</button>)}
      <button role="menuitem" onClick={()=>{onCopy();onClose();}}>Kopiuj dane pola</button>
      {sameCreature && (cell.pet_manual ?
        <button role="menuitem" disabled={savingPet} onClick={()=>savePet(null)}>Usuń oznaczenie peta</button> :
        <button role="menuitem" onClick={()=>setEditing("pet")}>Oznacz jako peta…</button>)}
      <div className="tile-menu-caption">OZNACZ OBIEKT NA TYM POLU</div>
      {annotationKinds.map(kind=><button role="menuitem" key={kind} disabled={!editable || !cell.items.length} onClick={()=>setEditing(kind)}><span style={{color:navigationStyles[kind].color}}>{navigationStyles[kind].symbol}</span>{navigationStyles[kind].label}<span className="tile-menu-chevron">›</span></button>)}
      <button role="menuitem" disabled={!editable || !cell.items.length} onClick={()=>setEditing("")}>Edytuj opis / warstwę…</button>
      {!editable && <p className="tile-menu-help">Oznaczanie wymaga bieżącej mapy na żywo.</p>}
    </> : editing==="pet" ? <form className="map-annotation-form" onSubmit={e=>{e.preventDefault();savePet(petName.trim());}}>
      <label>Nazwa peta<input className="mt-1 w-full rounded border border-gray-600 p-2" value={petName} maxLength={80} onChange={e=>setPetName(e.target.value)}/></label>
      <button className="secondary-button mt-2" disabled={!sameCreature || !petName.trim() || savingPet}>Oznacz jako peta</button>
      {!sameCreature && <p role="status">Wybierz ponownie pole stworzenia na mapie na żywo.</p>}
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
      await window.dbvMap.copyCell(copyCellText(target));setCopyResult({key,message:"Skopiowano dane pola"});
    } catch {setCopyResult({key,message:"Nie udało się skopiować. Zaznacz tekst i użyj Ctrl+C."});}
  }
  const ready = !!shown?.cells.length && shown.center && shown.origin;
  const plan = showPlan && !frozen && !shown?.stale && shown?.status === "ready" &&
    preview?.path[0]?.every((v,i)=>v===shown.center?.[i]) ? preview : null;
  const axis = (value:number, i:number) => (value - shown!.origin![i]) * 22 + 11;
  return <section ref={sectionRef} aria-label="Pasywna mapa" className="map-panel">
    <div className="map-panel-header">
      <div>
        <h2>Mapa otoczenia <span>Pasywna</span></h2>
        <p>Kliknięcie wybiera pole. Postać pozostaje pod Twoją kontrolą.</p>
      </div>
      <div className="flex items-center gap-2">
      <button className="secondary-button" aria-pressed={showPlan} onClick={()=>setShowPlan(!showPlan)}>Plan ruchu {showPlan?"✓":""}</button>
      <button className="secondary-button" aria-expanded={legendOpen} onClick={()=>setLegendOpen(!legendOpen)}>Legenda</button>
      <button disabled={!ready} onClick={() => { setFrozen(frozen ? null : structuredClone(map!)); setSelected(null); }}
        className="secondary-button">
        {frozen ? "Wznów podgląd" : "Zatrzymaj obraz"}
      </button>
      </div>
    </div>
    <div hidden={!legendOpen} className="map-legend">
    <div className="flex flex-wrap gap-x-4 gap-y-2 text-xs text-gray-300">
      {Object.entries(styles).map(([kind, style]) => <span key={kind} className="inline-flex items-center gap-1.5">
        <span className="inline-block h-3 w-3 rounded-sm border border-gray-500" style={{ backgroundColor: style.fill }} />{style.label}
      </span>)}
      {creatureKinds.map(k=><span key={k} style={{color:creatureStyles[k].color}}>{creatureStyles[k].symbol} {creatureStyles[k].label}</span>)}<span>◎ Ty</span>
    </div>
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs">
      {(["stairs", "ladder", "hole", "rope_spot", "web", "door", "level_gate", "teleport"] as const).map((kind) =>
        <span key={kind} style={{ color: navigationStyles[kind].color }}><b>{navigationStyles[kind].symbol}</b> {navigationStyles[kind].label}</span>)}
      <span className="text-gray-400">? = wskazówka klienta</span>
      <span className="text-teal-300">↗ = zaobserwowane przejście</span>
    </div>
    </div>
    {ready ? <div className="map-grid-layout"><div className="map-canvas-column">
      <div className="map-coordinates">
        <span>({shown.center![0]}, {shown.center![1]}, {shown.center![2]}) · północ ↑</span>
        <span className={frozen || shown.stale ? "text-amber-300" : "text-emerald-300"}>
          {frozen ? "OBRAZ ZATRZYMANY" : shown.stale ? "AKTUALIZACJA · OSTATNI OBRAZ" : "● NA ŻYWO"}
        </span>
      </div>
      <div className="map-stage"><svg role="group" aria-label="Siatka pól mapy, północ u góry" viewBox={`0 0 ${shown.width! * 22} ${shown.height! * 22}`}
        className={`map-canvas ${shown.stale && !frozen ? "opacity-90" : ""}`}>
        {shown.cells.map((c) => {
          const key = keyOf(c), px = (c.x - shown.origin![0]) * 22, py = (c.y - shown.origin![1]) * 22;
          const player = c.x === shown.center![0] && c.y === shown.center![1];
          const feature = c.navigation?.find((f) => f.source === "user_annotation") ?? c.navigation?.find((f) => f.source === "confirmed_location") ?? c.navigation?.find((f) => f.source === "confirmed_id") ?? c.navigation?.[0];
          const baseNav = feature ? navigationStyles[feature.kind] : c.transitions?.length ? {label:"Zaobserwowane przejście",symbol:"↗",color:"#5eead4"} : undefined;
          const nav = baseNav && feature?.door_state_hint ? {...baseNav,
            label:`${baseNav.label} · ${feature.door_state_hint==="open" ? "otwarte" : feature.door_state_hint==="closed" ? "zamknięte" : "stan nieustalony"}`,
            color:feature.door_state_hint==="open" ? "#6ee7b7" : feature.door_state_hint==="closed" ? "#fda4af" : baseNav.color} : baseNav;
          const occupants = creatureCounts(c);
          const label = `${c.x}, ${c.y}, ${c.z}: ${nav ? `${nav.label}${feature?.source === "client_hint" ? "?" : ""}; ` : ""}${styles[c.kind]?.label ?? "Nieznane"}${occupants.length ? `; ${occupants.map(o=>`${o.label}: ${o.count}`).join(", ")}` : ""}`;
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
        {plan && <g pointerEvents="none" aria-label="Plan Cavebota">
          {plan.path.length>1 && <polyline points={plan.path.map(([x,y])=>[axis(x,0),axis(y,1)].join(",")).join(" ")}
            fill="none" stroke="#78ecd3" strokeWidth="1.4" strokeDasharray="3 2" strokeLinecap="round"/>}
          {plan.stop && plan.stop[2]===shown.center![2] && <rect x={axis(plan.stop[0],0)-8} y={axis(plan.stop[1],1)-8}
            width="16" height="16" rx="4" fill="none" stroke="#ffd375" strokeWidth="1.6"/>}
        </g>}
      </svg></div>
      <div className="map-quick-legend"><span>◎ Twoja postać</span>{creatureKinds.map(k=><span key={k} style={{color:creatureStyles[k].color}}><b>{creatureStyles[k].symbol}</b> {creatureStyles[k].label}</span>)}
        {showPlan && <><span style={{color:"#78ecd3"}}>┄ Plan ruchu</span>
          <span style={{color:"#ffd375"}}>{plan?.stop ? "▢ Postój "+plan.stop.join(", ") : "▢ Postój —"}</span></>}
      </div>
      <p className="map-caption">Kolor pola: blokada. Symbol: typ obiektu. Zielone pole nie gwarantuje przejścia.</p>
      </div><aside className="map-inspector" aria-label="Szczegóły wybranego pola">
        <div className="inspector-heading">INSPEKTOR POLA {selected && <span>PRZYPIĘTE</span>}</div>
        <CellDetails cell={cell} />
        {cell && <div className="mt-3 flex flex-wrap items-center gap-3">
          <button className="copy-cell-button" onClick={()=>copyCell(cell)}>Kopiuj dane pola</button>
          <button className="secondary-button" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();openMenu(cell,rect.left,rect.bottom);}}>Menu pola…</button>
          <span role="status" className="text-xs text-gray-400">{copyResult?.key === keyOf(cell) ? copyResult.message : ""}</span>
        </div>}
        <p className="mt-3 text-xs text-gray-400">PPM na polu → wybierz typ obiektu. Klawiatura: Shift+F10.</p>
        {selected && <button className="mt-2 text-xs text-sky-300 underline" onClick={() => setSelected(null)}>Zwolnij wybór pola</button>}
      </aside></div> : <div className="empty-panel map-empty"><span aria-hidden="true">⌖</span><h2>{shown?.status==="waiting" ? "Mapa czeka na połączenie" : "Mapa chwilowo niedostępna"}</h2><p>{shown?.message ?? "Pola klienta pojawią się tutaj po zalogowaniu do gry."}</p></div>}
    {menu && menuCell && <TileContextMenu key={`${menu.key}:${menuCell.items.map(i=>i.id).join(",")}`} position={menu} cell={menuCell} scope={menu.scope}
      editable={!!editable && menu.scope===map?.annotation_scope} onClose={closeMenu} onCopy={()=>copyCell(menuCell)}/>}
    {map?.annotation_error && <p className="mt-2 text-xs text-amber-300">{map.annotation_error}</p>}
    <details className="map-source"><summary>O danych i oznaczeniach</summary>
      <p className="mt-2">Przejście może mieć czerwone tło. Cele, warunki wejścia i zamykanie drzwi wymagają osobnej obserwacji.</p>
      <p className="mt-1">{shown?.asset?.message ?? "Definicje obiektów i pola odczytywane z pamięci klienta."}</p>
      {shown?.catalogue_items && <p>Katalog: {shown.catalogue_items} wpisów. Siatka: {shown.width} × {shown.height}, bieżące piętro.</p>}
    </details>
  </section>;
}
