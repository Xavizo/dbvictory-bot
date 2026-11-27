import { t, type TextKey } from "@/lib/i18n";
import type {MapNavigationFeature,PassiveMapCell} from "./api";
// A style whose label is read in the current language each time it is shown.
const named=<T extends object>(key:TextKey,rest:T)=>Object.defineProperty({...rest},"label",{get:()=>t(key),enumerable:true}) as T&{label:string};
export const petStyle = named("map.pet",{symbol:"P",color:"#5eead4"});
export const unidentifiedStyle = named("map.unidentified",{symbol:"?",color:"#c084fc"});
export function displayOccupants(cell: PassiveMapCell) {
  const counts = {...(cell.occupant_types ?? {unknown:cell.occupants})};
  const anonymous = Math.min(counts.monster ?? 0, cell.unidentified_monsters ?? 0);
  if (anonymous) {
    counts.monster = (counts.monster ?? 0) - anonymous;
    counts.unknown = (counts.unknown ?? 0) + anonymous;
  }
  return counts;
}

export function petDescription(cell: PassiveMapCell) {
  return cell.pet_names?.map((name, index) => {
    const owner = cell.pet_owners?.[index];
    return owner ? t("map.petOwner",{name,owner}) : name;
  }).join(", ");
}
export const cutWebStyle = named("map.cutWeb",{symbol:"W−",color:"#d6c5dc"});
export const tileStyles: Record<string, { fill: string; label: string }> = {
  clear: named("map.tile.clear", { fill: "#334946" }),
  blocked: named("map.tile.blocked", { fill: "#57404b" }),
  avoid: named("map.tile.avoid", { fill: "#66512d" }),
  no_ground: named("map.tile.no_ground", { fill: "#0b141e" }),
  unknown: named("map.tile.unknown", { fill: "#29384b" }),
};
export const navigationStyles: Record<MapNavigationFeature["kind"], { label: string; symbol: string; color: string }> = {
  stairs: named("map.nav.stairs", { symbol: "S", color: "#7dd3fc" }),
  ladder: named("map.nav.ladder", { symbol: "L", color: "#7dd3fc" }),
  descent: named("map.nav.descent", { symbol: "↓", color: "#fbbf24" }),
  hole: named("map.nav.hole", { symbol: "↓", color: "#fbbf24" }),
  covered_hole: named("map.nav.covered_hole", { symbol: "↓", color: "#fbbf24" }),
  web: named("map.nav.web", { symbol: "W", color: "#c4b5fd" }),
  rope_spot: named("map.nav.rope_spot", { symbol: "R", color: "#7dd3fc" }),
  door: named("map.nav.door", { symbol: "D", color: "#fde68a" }),
  level_gate: named("map.nav.level_gate", { symbol: "G", color: "#fde68a" }),
  teleport: named("map.nav.teleport", { symbol: "T", color: "#d8b4fe" }),
  floor_change: named("map.nav.floor_change", { symbol: "↕", color: "#7dd3fc" }),
  passage: named("map.nav.passage", { symbol: "↕", color: "#7dd3fc" }),
};
