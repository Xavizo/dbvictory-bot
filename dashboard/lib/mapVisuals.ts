import type {MapNavigationFeature} from "./api";
export const petStyle = {label:"Pet",symbol:"P",color:"#5eead4"};
export const tileStyles: Record<string, { fill: string; label: string }> = {
  clear: { fill: "#334946", label: "Brak blokady" },
  blocked: { fill: "#57404b", label: "Blokada" },
  avoid: { fill: "#66512d", label: "Omijane w trasie" },
  no_ground: { fill: "#0b141e", label: "Brak podłoża" },
  unknown: { fill: "#29384b", label: "Nieznane" },
};
export const navigationStyles: Record<MapNavigationFeature["kind"], { label: string; symbol: string; color: string }> = {
  stairs: { label: "Schody", symbol: "S", color: "#7dd3fc" },
  ladder: { label: "Drabina", symbol: "L", color: "#7dd3fc" },
  descent: { label: "Zejście / klapa", symbol: "↓", color: "#fbbf24" },
  hole: { label: "Dziura", symbol: "↓", color: "#fbbf24" },
  covered_hole: { label: "Shovel spot · zasypana dziura", symbol: "↓", color: "#fbbf24" },
  rope_spot: { label: "Rope spot", symbol: "R", color: "#7dd3fc" },
  door: { label: "Drzwi", symbol: "D", color: "#fde68a" },
  level_gate: { label: "Bramka levelowa", symbol: "G", color: "#fde68a" },
  teleport: { label: "Teleport", symbol: "T", color: "#d8b4fe" },
  floor_change: { label: "Zmiana piętra", symbol: "↕", color: "#7dd3fc" },
  passage: { label: "Możliwe przejście", symbol: "↕", color: "#7dd3fc" },
};
