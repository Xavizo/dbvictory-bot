"use client";
import { useEffect, useRef, useState } from "react";
import { updateActionConfig, type KnifeMonster, type KnifeSuggestion } from "@/lib/api";

const SPENT = 8141;

function parseId(text: string) {
  const value = Number(text.trim());
  return Number.isInteger(value) && value >= 1 && value <= 65535 ? value : null;
}
const clean = (name: string) => name.trim().replace(/\s+/g, " ").toLowerCase();
const copy = (monsters: KnifeMonster[]) => monsters.map(m => ({ name: m.name, corpses: m.corpses.map(c => ({ ...c })) }));

function SuggestionRow({ row, busy, onAdd, onIgnore }: {
  row: KnifeSuggestion; busy: boolean; onAdd: (name: string) => void; onIgnore: () => void;
}) {
  const [name, setName] = useState(row.name ?? "");
  return <li>
    <span>Ciało <b>{row.id}</b> → {row.skinned}{row.seen > 1 && ` · ${row.seen}×`}</span>
    <input aria-label={`Potwór dla ciała ${row.id}`} placeholder="Nazwa potwora" value={name} maxLength={64} disabled={busy}
      onChange={e => setName(e.target.value)}/>
    <button type="button" className="primary-button" disabled={busy || !clean(name)} onClick={() => onAdd(clean(name))}>Dodaj</button>
    <button type="button" className="secondary-button" disabled={busy} onClick={onIgnore}>Ignoruj</button>
  </li>;
}

/** Edit the monsters, and their corpse IDs, the Hunter Knife may be used on. */
export default function HunterKnifeConfig({ monsters, defaults, suggestions, ignored, onClose, onSaved }: {
  monsters: KnifeMonster[];
  defaults: KnifeMonster[];
  suggestions: KnifeSuggestion[];
  ignored: number[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [list, setList] = useState(() => copy(monsters));
  const [ignore, setIgnore] = useState(ignored);
  const [handled, setHandled] = useState<number[]>([]);
  const [newName, setNewName] = useState("");
  const [drafts, setDrafts] = useState<Record<string, { id: string; skinned: string }>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  const open = suggestions.filter(s => !handled.includes(s.id));

  function addCorpse(name: string, id: number, skinned: number) {
    const known = list.flatMap(m => m.corpses).find(c => c.id === id);
    if (known && known.skinned !== skinned) return setError(`Ciało ${id} ma już ID po oskórowaniu ${known.skinned}.`);
    const exists = list.some(m => m.name === name);
    setList(exists
      ? list.map(m => m.name !== name || m.corpses.some(c => c.id === id) ? m : { ...m, corpses: [...m.corpses, { id, skinned }] })
      : [...list, { name, corpses: [{ id, skinned }] }]);
    setError("");
    return true;
  }
  function addFromDraft(name: string) {
    const draft = drafts[name] ?? { id: "", skinned: String(SPENT) };
    const id = parseId(draft.id), skinned = parseId(draft.skinned || String(SPENT));
    if (id === null || skinned === null) return setError("ID musi być liczbą od 1 do 65535.");
    if (addCorpse(name, id, skinned)) setDrafts({ ...drafts, [name]: { id: "", skinned: String(SPENT) } });
  }
  function addMonster() {
    const name = clean(newName);
    if (!name) return;
    if (list.some(m => m.name === name)) return setError(`Potwór ${name} jest już na liście.`);
    setList([...list, { name, corpses: [] }]);
    setNewName("");
    setError("");
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      await updateActionConfig("auto_hunter_knife", { monsters: list, ignored_suggestions: ignore });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className="spell-picker-dialog knife-config-dialog" aria-label="Potwory dla Hunter Knife"
    onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}
    onClick={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="spell-picker-content">
      <header><strong>Hunter Knife · potwory do skórowania</strong>
        <button type="button" className="spell-remove" aria-label="Zamknij konfigurację Hunter Knife" disabled={busy} onClick={onClose}>×</button></header>
      <p className="knife-config-help">Nóż jest używany tylko na ciałach potworów z tej listy. Gdy sam oskórujesz nowe ciało nożem, pojawi się tu propozycja do dodania. ID możesz też sprawdzić w zakładce <b>Diagnostyka</b> → <b>Identyfikacja przedmiotów</b> (Look na ciele przed i po oskórowaniu).</p>
      {!!open.length && <section aria-label="Propozycje" className="knife-config-suggestions">
        <h4>Ręcznie oskórowane ciała <small>{open.length}</small></h4>
        <ul>{open.map(row => <SuggestionRow key={row.id} row={row} busy={busy}
          onAdd={name => { if (addCorpse(name, row.id, row.skinned)) setHandled([...handled, row.id]); }}
          onIgnore={() => { setIgnore([...ignore, row.id]); setHandled([...handled, row.id]); }}/>)}</ul>
      </section>}
      <section aria-label="Potwory">
        <h4>Potwory <small>{list.length}</small></h4>
        <ul className="knife-config-monsters">{list.map(m => {
          const draft = drafts[m.name] ?? { id: "", skinned: String(SPENT) };
          return <li key={m.name}>
            <div className="knife-config-monster-head"><strong>{m.name}</strong>
              {!m.corpses.length && <small>bez ID ciała · nóż go pominie</small>}
              <button type="button" className="spell-remove" aria-label={`Usuń potwora ${m.name}`} disabled={busy || list.length <= 1}
                onClick={() => setList(list.filter(x => x.name !== m.name))}>×</button></div>
            <div className="knife-config-names">{m.corpses.map(c => <span key={c.id}>{c.id} → {c.skinned}
              <button type="button" className="spell-remove" aria-label={`Usuń ciało ${c.id} potwora ${m.name}`} disabled={busy}
                onClick={() => setList(list.map(x => x.name === m.name ? { ...x, corpses: x.corpses.filter(y => y.id !== c.id) } : x))}>×</button></span>)}
              <input inputMode="numeric" aria-label={`Nowe ID ciała potwora ${m.name}`} placeholder="ID ciała" value={draft.id} disabled={busy}
                onChange={e => setDrafts({ ...drafts, [m.name]: { ...draft, id: e.target.value } })}
                onKeyDown={e => { if (e.key === "Enter") addFromDraft(m.name); }}/>
              <input inputMode="numeric" aria-label={`ID po oskórowaniu dla potwora ${m.name}`} title="ID po oskórowaniu" value={draft.skinned} disabled={busy}
                onChange={e => setDrafts({ ...drafts, [m.name]: { ...draft, skinned: e.target.value } })}
                onKeyDown={e => { if (e.key === "Enter") addFromDraft(m.name); }}/>
              <button type="button" className="secondary-button" disabled={busy || !draft.id.trim()} onClick={() => addFromDraft(m.name)}>Dodaj ID</button>
            </div>
          </li>;
        })}</ul>
        <div className="knife-config-add"><input aria-label="Nazwa nowego potwora" placeholder="np. Fury Roco" value={newName} maxLength={64} disabled={busy}
          onChange={e => setNewName(e.target.value)} onKeyDown={e => { if (e.key === "Enter") addMonster(); }}/>
          <button type="button" className="secondary-button" disabled={busy || !newName.trim()} onClick={addMonster}>Dodaj potwora</button></div>
      </section>
      {error && <p role="alert">{error}</p>}
      <footer className="knife-config-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={() => { setList(copy(defaults)); setError(""); }}>Przywróć domyślne</button>
        <button type="button" className="primary-button" disabled={busy} onClick={save}>Zapisz</button>
      </footer>
    </div>
  </dialog>;
}
