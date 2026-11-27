"use client";
import {useState} from "react";
import {toggleAction, updateActionConfig, type BotState} from "@/lib/api";
import {LocalSpellIcon} from "./SpellPicker";

const fields = [
  {key: "duration", label: "Czas bariery (s)", fallback: 200},
  {key: "min_before", label: "Min. wyprzedzenie (s)", fallback: 5},
  {key: "max_before", label: "Maks. wyprzedzenie (s)", fallback: 30},
] as const;

export default function BarrierSpellPanel({state, onRefresh}: {state: BotState | null; onRefresh: () => void}) {
  const action = state?.actions.find(a => a.name === "barrier_spell");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!action) return null;
  const {enabled, config} = action;
  const saved = (f: typeof fields[number]) => String(config?.[f.key] ?? f.fallback);
  const dirty = fields.some(f => (draft[f.key] ?? saved(f)) !== saved(f));
  async function change(save: boolean) {
    setBusy(true);
    setError("");
    try {
      if (save) {
        const values = fields.map(f => [f.key, draft[f.key] ?? saved(f)] as const);
        if (values.some(([, value]) => value.trim() === "")) throw new Error("Uzupełnij wszystkie czasy.");
        await updateActionConfig("barrier_spell", Object.fromEntries(values.map(([key, value]) => [key, Number(value)])));
      } else {
        await toggleAction("barrier_spell", !enabled);
      }
      await onRefresh();
      setDraft({});
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return <section className="speed-up-panel" aria-label="Automatyczny czar Barrier">
    <div className="speed-heading">
      <LocalSpellIcon src="/local-spells/icons/11740.png" size={28}/>
      <strong>Barrier</strong><span>{enabled ? state?.barrier_spell?.status ?? "Czekam na postać" : "Wyłączony"}</span>
      <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
        aria-label="Automatyczny czar Barrier" disabled={busy || dirty || !!state?.automation_paused} onClick={() => void change(false)}><span/></button>
    </div>
    <div className="speed-times">{fields.map(f => <label key={f.key}>{f.label}
      <input type="number" min={f.key === "duration" ? 2 : 0} max={3600} step="0.1" value={draft[f.key] ?? saved(f)}
        disabled={busy || enabled} onChange={e => setDraft({...draft, [f.key]: e.target.value})}/>
    </label>)}{dirty && <button className="secondary-button" disabled={busy || enabled} onClick={() => void change(true)}>Zapisz</button>}</div>
    <small>Termin odnowienia jest losowany raz na cykl. Po utracie bariery czar jest rzucany ponownie.</small>
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
