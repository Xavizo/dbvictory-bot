"use client";

import { useEffect, useRef, useState } from "react";
import { fetchNearbyItems, moveNearbyItem, type NearbyItemList } from "@/lib/api";

type Props = { online: boolean; paused: boolean };

export default function NearbyItemsPanel({ online, paused }: Props) {
  const [list, setList] = useState<NearbyItemList | null>(null);
  const [readError, setReadError] = useState("");
  const [result, setResult] = useState("");
  const [moveError, setMoveError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const pending = useRef(false);

  useEffect(() => {
    if (!online) { setList(null); setReadError(""); setResult(""); setMoveError(""); return; }
    if (busy) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const latest = await fetchNearbyItems(controller.signal);
        if (!controller.signal.aborted) { setList(latest); setReadError(""); }
      } catch (err) {
        if (!controller.signal.aborted) { setList(null); setReadError((err as Error).message); }
      }
      if (!controller.signal.aborted) timer = setTimeout(poll, 750);
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [online, busy, refresh]);

  async function move(token: string, destination: "feet" | "backpack") {
    if (pending.current || !online || paused) return;
    pending.current = true;
    setBusy(true); setResult(""); setMoveError("");
    try {
      setResult(await moveNearbyItem(token, destination));
    } catch (err) {
      setMoveError((err as Error).message);
    } finally {
      setList(null); setBusy(false); pending.current = false; setRefresh(n => n + 1);
    }
  }

  return <section className="diagnostic-section nearby-items">
    <div className="section-caption"><h2>Przedmioty w zasięgu</h2>
      {list && <span>Pozycja {list.center.join(", ")}</span>}</div>
    <p className="nearby-note">Twoje pole i osiem pól obok. Jedno kliknięcie przenosi jedną sztukę.</p>
    {readError && <p role="status">{readError}</p>}
    {moveError && <p className="text-red-300" role="alert">{moveError}</p>}
    {result && <p role="status">{result}</p>}
    {!online ? <p>Oczekiwanie na połączenie z grą.</p> :
      !list && !readError ? <p role="status">{busy ? "Wysyłanie ruchu…" : "Odczytywanie pól…"}</p> :
      list?.rows.length === 0 ? <p>W zasięgu nie ma ruchomych przedmiotów.</p> : null}
    {list && list.rows.length > 0 && <ul className="nearby-list">
      {list.rows.map(row => {
        const underfoot = row.position.every((v, i) => v === list.center[i]);
        return <li key={row.token}>
          <div><strong>ID {row.item_id}</strong>
            <small>{underfoot ? "Pod postacią" : row.position.join(", ")} · warstwa {row.stack}
              {row.covered ? " · przykryty" : !row.pickup ? " · tylko przesuwanie" : ""}</small></div>
          <div className="nearby-buttons">
            <button className="secondary-button" disabled={!online || paused || busy || row.covered || !row.pickup}
              onClick={() => move(row.token, "backpack")}>Do plecaka</button>
            <button className="secondary-button" disabled={!online || paused || busy || row.covered || underfoot}
              onClick={() => move(row.token, "feet")}>Pod postać</button>
          </div>
        </li>;
      })}
    </ul>}
  </section>;
}
