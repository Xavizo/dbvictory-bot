"use client";

import { useState } from "react";
import type { TransitionAction, PassiveMapState, TransitionObservation } from "@/lib/api";

export const transitionActions: Record<TransitionAction, string> = {
  clear_web: "Przetnij pajęczynę AiO i przejdź", step: "Wejście na pole", use: "Użycie obiektu", use_with: "Użycie narzędzia na polu",
  use_then_step: "Użycie obiektu → przejście", use_with_then_step: "Narzędzie → wejście na pole", fly_up: "Czar fly up", unknown: "Nieustalona",
};
const objectBehaviors = {open_then_step:"Otwarcie bez ruchu → potem krok", use_enters:"Użycie przenosi na pole", manual_close:"Ręczne zamknięcie", closes_after_exit:"Zamknięcie po wyjściu", stays_open:"Otwarte przez ≥2 s po wyjściu"};
const statuses = { waiting: "Czeka na grę", learning: "Obserwuje Twoją grę", suspended: "Wstrzymane podczas automatycznego ruchu lub śmierci", error: "Odczyt chwilowo niedostępny" };
const reasons: Record<string,string> = {
  matched_command: "Polecenie zgodne ze zmianą pozycji", door_crossing: "Zaobserwowane przejście przez drzwi",
  no_command: "Brak pasującego polecenia", multiple_commands: "Kilka poleceń między odczytami",
  incomplete_entry: "Brak pełnych danych pola wejścia", multiple_steps: "Nieznana lub wieloetapowa trasa",
  sample_gap: "Postać ruszyła przed potwierdzeniem wyjścia", long_jump: "Skok pozycji",
};
const coords = (p: number[] | null) => p ? `(${p.join(", ")})` : "Nieustalone";
const tiles=(n:number)=>`${n} ${n===1 ? "pole" : n%10>=2 && n%10<=4 && !(n%100>=12 && n%100<=14) ? "pola" : "pól"}`;

export default function TransitionLearningPanel({ map, onInspect }: { map?: PassiveMapState; onInspect?:(pos:[number,number,number])=>void }) {
  const learning=map?.learning, center=map?.center;
  const [order,setOrder]=useState<"nearby"|"recent">("nearby");
  const distance=(r:TransitionObservation)=>r.entry && center && r.entry[2]===center[2] ? Math.max(Math.abs(r.entry[0]-center[0]),Math.abs(r.entry[1]-center[1])) : Infinity;
  const fallback=[...(learning?.records ?? [])].sort((a,b)=>{
    if(!a.entry || !b.entry) return Number(!a.entry)-Number(!b.entry);
    return distance(a)-distance(b) || b.last_seen-a.last_seen;
  });
  const rows=order==="nearby" ? learning?.nearby_records ?? fallback : learning?.records ?? [];
  return <section aria-label="Uczenie przejść" className="transition-panel">
    <div className="transition-summary">
      <div><span>ZAPISANE WARIANTY</span><strong>{learning?.total ?? 0}</strong><small>Wszystkie obserwacje w tym profilu</small></div>
      <div><span>POWIĄZANE</span><strong className="text-emerald-300">{learning?.observed ?? 0}</strong><small>Czynność zgodna ze zmianą obiektu lub pozycji</small></div>
      <div><span>DO SPRAWDZENIA</span><strong className="text-amber-300">{(learning?.total ?? 0)-(learning?.observed ?? 0)}</strong><small>Niepełne lub niejednoznaczne dane</small></div>
    </div>
    <div className="transition-toolbar">
      <div><h2>Baza przejść</h2><span className={learning?.status === "learning" ? "text-emerald-300" : "text-amber-300"}>
        {statuses[learning?.status ?? "waiting"]}
      </span></div>
    <label>Sortowanie
      <select value={order} onChange={e=>setOrder(e.target.value as "nearby"|"recent")}>
        <option value="nearby">Najbliższe wejścia</option><option value="recent">Ostatnio widziane</option>
      </select>
    </label>
    </div>
    {order==="nearby" && <p className="transition-context">Najpierw bieżące piętro · odległość bez uwzględnienia przeszkód · nieustalone wejścia na końcu</p>}
    {learning?.storage_error && <p role="alert" className="mt-2 rounded border border-amber-700 p-2 text-sm text-amber-200">{learning.storage_error}</p>}
    {learning?.limit_reached && <p className="mt-2 text-xs text-amber-300">Osiągnięto limit 2000 wariantów; nowe miejsca nie są dopisywane.</p>}
    {!learning?.records.length ? <div className="empty-panel"><h2>Twoja baza zacznie rosnąć podczas gry</h2><p>Przejdź ręcznie schodami, drabiną lub teleportem. Krótki postój po przejściu ułatwi potwierdzenie miejsca wyjścia.</p></div> :
      <div className="transition-table-wrap">
        <table className="transition-table">
          <thead><tr>
            <th>Odległość</th><th>Wejście → wyjście</th><th>Czynność</th><th>Ocena</th><th>Powt.</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}>
            <td><span className="distance-label">{!row.entry ? "—" : !center ? "—" : row.entry[2]!==center[2] ? `Z = ${row.entry[2]}` : distance(row)===0 ? "Tutaj" : tiles(distance(row))}</span>
              <small className="distance-caption">{!row.entry ? "Nieustalone wejście" : !center ? "Brak pozycji" : row.entry[2]!==center[2] ? "Inne piętro" : "Bieżące piętro"}</small>
            </td>
            <td className="font-mono whitespace-nowrap">
              <strong className="transition-entry">{coords(row.entry)}</strong> <span className="transition-exit">{row.object_change ? `· ${row.object_change.closed_id} ⇄ ${row.object_change.open_id}` : `→ ${coords(row.exit)}`}</span>
              <p className="mt-1 text-gray-400">#{row.id.slice(0,6)} · ID: {row.entry_items.join(", ") || "?"}</p>
              <div className="transition-row-tools">
              {row.entry && map?.cells.some(c=>c.x===row.entry![0] && c.y===row.entry![1] && c.z===row.entry![2]) && onInspect &&
                <button className="text-sky-300" aria-label={`Pokaż wejście ${coords(row.entry)} na mapie`} onClick={()=>onInspect([...row.entry!] as [number,number,number])}>Mapa ↗</button>}
              <details className="font-sans text-gray-400"><summary className="cursor-pointer">Szczegóły</summary>
                <p>Pozycja przed: {coords(row.origin)}</p>
                <p>{row.direction === "up" ? "W górę" : row.direction === "down" ? "W dół" : "To samo piętro"}</p>
                <p>ID warstw wejścia: {row.entry_items.join(", ") || "nieustalone"}</p>
                <p>Ostatnio: {new Date(row.last_seen*1000).toLocaleString("pl-PL")}</p>
                <p>{row.object_change ? "Zmiana stanu obiektu po ręcznej czynności" : reasons[row.reason] ?? "Wymaga sprawdzenia"}</p>
              </details>
              </div>
            </td>
            <td>{row.object_change ? objectBehaviors[row.object_change.behavior] : transitionActions[row.action]}
              {row.item_id !== null && <p className="mt-1 text-gray-400">Obiekt: {row.item_id}</p>}
              {row.prepared_item_id && <p className="text-gray-400">Po otwarciu: {row.prepared_item_id}</p>}
              {row.tool_id !== null && <p className="text-gray-400">Narzędzie: {({3003:"Rope",3457:"Shovel",8899:"AiO"} as Record<number,string>)[row.tool_id] ?? "ID"} · {row.tool_id}</p>}
            </td>
            <td><span className={`evidence-badge ${row.evidence}`}>{row.evidence === "observed" ? "Zaobserwowane" : "Do sprawdzenia"}</span>
              {row.variable_exit && <p className="mt-1 text-amber-300">Różne cele dla tego wejścia</p>}
            </td>
            <td><span className="repeat-count">× {row.count}</span><small className="distance-caption">{new Date(row.last_seen*1000).toLocaleTimeString("pl-PL")}</small></td>
          </tr>)}</tbody>
        </table>
      </div>}
    <div className="transition-footer"><span>{order==="recent" ? "Ostatnie" : "Najbliższe"} {rows.length} z {learning?.total ?? 0} wariantów{order==="nearby" && learning && !learning.nearby_records ? " (wybór spośród ostatnich 20)" : ""}</span><span>Pasywny zapis · bez sterowania postacią</span></div>
    <details className="map-source"><summary>Jak interpretować obserwacje?</summary><p className="mt-2">Powiązanie opisuje udaną obserwację, nie dowodzi jedynej wymaganej czynności. Kierunek powrotny, wymagany poziom i samoczynne zamykanie drzwi wymagają osobnego potwierdzenia.</p>{learning?.file && <p className="mt-1 break-all">Zapis lokalny: local_data/{learning.file}</p>}</details>
  </section>;
}
