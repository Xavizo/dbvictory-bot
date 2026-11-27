"use client";
import {useState} from "react";
import {setItemIdentification, type BotState} from "@/lib/api";

export default function ItemIdentificationPanel({state, online, onRefresh}: {state: BotState | null; online: boolean; onRefresh: () => void}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const ident = state?.item_identification;
  if (!ident) return null;
  const {enabled, recent} = ident;
  async function toggle() {
    setBusy(true);
    setError("");
    try {
      await setItemIdentification(!enabled);
      await onRefresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return <section className="speed-up-panel" aria-label="Identyfikacja przedmiotów">
    <div className="speed-heading">
      <strong>Identyfikacja przedmiotów</strong><span>{enabled ? "Włączona" : "Wyłączona"}</span>
      <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
        aria-label="Identyfikacja przedmiotów" disabled={busy || !online} onClick={() => void toggle()}><span/></button>
    </div>
    <small>Kliknij Look na przedmiocie w grze, a jego ID pojawi się na pasku statusu gry („Item Id: …”) i na liście poniżej. Przydaje się np. do ustawienia ID ciał dla Hunter Knife.</small>
    {enabled && (recent.length ? <table className="w-full text-xs mt-2">
      <thead><tr className="text-left text-gray-400"><th className="py-1">ID</th><th className="py-1">Miejsce</th><th className="py-1">Czas</th></tr></thead>
      <tbody>{recent.map((item, i) => <tr key={`${item.time}-${i}`} className="border-t border-gray-700">
        <td className="py-1"><strong>{item.item_id}</strong></td>
        <td className="py-1">{item.where === "inventory" ? "Ekwipunek / plecak" : item.position ? item.position.join(", ") : "Mapa"}</td>
        <td className="py-1">{new Date(item.time * 1000).toLocaleTimeString("pl-PL")}</td>
      </tr>)}</tbody>
    </table> : <p className="text-xs text-gray-400 mt-2">Jeszcze nic nie sprawdzono w tej sesji.</p>)}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
