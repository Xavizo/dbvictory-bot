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
    {info && <small className="barrier-band-help">Osobisty Loot {info.personal_loot} · Obrobione {info.processed}
      {info.unclassified_loot > 0 && ` · Wpisy bez potwierdzonego kanału ${info.unclassified_loot}`}</small>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
