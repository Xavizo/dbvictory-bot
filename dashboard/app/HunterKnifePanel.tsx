"use client";
import { useState } from "react";
import { toggleAction, type BotState } from "@/lib/api";

export default function HunterKnifePanel({ state, onRefresh }: {
  state: BotState | null;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const action = state?.actions.find(a => a.name === "auto_hunter_knife");
  if (!action) return null;
  const enabled = action.enabled;
  const info = state?.hunter_knife;
  async function toggle() {
    setBusy(true);
    setError("");
    try {
      await toggleAction("auto_hunter_knife", !enabled);
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return <section className="barrier-band-panel" aria-label="Hunter Knife">
    <div><strong>Hunter Knife</strong><small>5908 · własne ciała elementali</small></div>
    <span className="barrier-band-status">{enabled ? info?.status ?? "Oczekiwanie" : "Wyłączony"}</span>
    <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
      aria-label="Automatyczny Hunter Knife" disabled={busy || !!state?.automation_paused} onClick={toggle}><span/></button>
    {info && <small className="barrier-band-help">Z walki i XP {info.from_combat ?? 0} · Wpisy Loot {info.personal_loot} · Obrobione {info.processed} · Odmowy właściciela {info.denied??0}
      {info.unclassified_loot > 0 && ` · Wpisy bez potwierdzonego kanału ${info.unclassified_loot}`}</small>}
    {info?.last_timing_ms && <small className="barrier-band-help">Ostatnie pole · rozpoznanie {info.last_timing_ms.recognition} ms · do próby {info.last_timing_ms.wait_for_use} ms · potwierdzenie {info.last_timing_ms.confirmation} ms</small>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
