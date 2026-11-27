"use client";
import { useEffect, useRef, useState } from "react";
import { updateActionConfig, type KnifeCorpse } from "@/lib/api";

const SPENT = 8141;

function parseId(text: string) {
  const value = Number(text.trim());
  return Number.isInteger(value) && value >= 1 && value <= 65535 ? value : null;
}

/** Edit the corpse item IDs and monster names the Hunter Knife may be used on. */
export default function HunterKnifeConfig({ corpses, species, defaults, onClose, onSaved }: {
  corpses: KnifeCorpse[];
  species: string[];
  defaults: { corpses: KnifeCorpse[]; species: string[] };
  onClose: () => void;
  onSaved: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState(corpses.map(c => ({ id: String(c.id), skinned: String(c.skinned) })));
  const [names, setNames] = useState(species);
  const [newId, setNewId] = useState("");
  const [newSkinned, setNewSkinned] = useState(String(SPENT));
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);

  function addCorpse() {
    const id = parseId(newId), skinned = parseId(newSkinned);
    if (id === null || skinned === null) return setError("ID musi być liczbą od 1 do 65535.");
    if (rows.some(r => Number(r.id) === id)) return setError(`ID ciała ${id} jest już na liście.`);
    setRows([...rows, { id: String(id), skinned: String(skinned) }]);
    setNewId("");
    setError("");
  }
  function addName() {
    const name = newName.trim().replace(/\s+/g, " ").toLowerCase();
    if (!name) return;
    if (!names.includes(name)) setNames([...names, name]);
    setNewName("");
  }
  async function save() {
    const parsed = rows.map(r => ({ id: parseId(r.id), skinned: parseId(r.skinned) }));
    if (parsed.some(r => r.id === null || r.skinned === null)) return setError("Każde ID musi być liczbą od 1 do 65535.");
    setBusy(true);
    setError("");
    try {
      await updateActionConfig("auto_hunter_knife", { corpses: parsed, species: names });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function restore() {
    setRows(defaults.corpses.map(c => ({ id: String(c.id), skinned: String(c.skinned) })));
    setNames(defaults.species);
    setError("");
  }

  return <dialog ref={dialog} className="spell-picker-dialog knife-config-dialog" aria-label="Ciała dla Hunter Knife"
    onCancel={e => { e.preventDefault(); if (!busy) onClose(); }}
    onClick={e => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="spell-picker-content">
      <header><strong>Hunter Knife · ciała do skórowania</strong>
        <button type="button" className="spell-remove" aria-label="Zamknij konfigurację Hunter Knife" disabled={busy} onClick={onClose}>×</button></header>
      <p className="knife-config-help">ID ciała sprawdzisz w zakładce <b>Diagnostyka</b> → <b>Identyfikacja przedmiotów</b>: kliknij Look na świeżym ciele, a potem na ciele po oskórowaniu. Nóż działa tylko na ciałach potworów zabitych przez Twoją postać, więc dopisz też nazwę potwora tak, jak widać ją w grze.</p>
      <section aria-label="ID ciał">
        <h4>ID ciał <small>{rows.length}</small></h4>
        <table className="knife-config-table"><thead><tr><th>Świeże ciało</th><th>Po oskórowaniu</th><th/></tr></thead><tbody>
          {rows.map((row, i) => <tr key={i}>
            <td><input inputMode="numeric" aria-label={`ID świeżego ciała ${i + 1}`} value={row.id} disabled={busy}
              onChange={e => setRows(rows.map((r, j) => j === i ? { ...r, id: e.target.value } : r))}/></td>
            <td><input inputMode="numeric" aria-label={`ID ciała po oskórowaniu ${i + 1}`} value={row.skinned} disabled={busy}
              onChange={e => setRows(rows.map((r, j) => j === i ? { ...r, skinned: e.target.value } : r))}/></td>
            <td><button type="button" className="spell-remove" aria-label={`Usuń ciało ${row.id}`} disabled={busy || rows.length <= 1}
              onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button></td>
          </tr>)}
          <tr><td><input inputMode="numeric" aria-label="Nowe ID świeżego ciała" placeholder="np. 9068" value={newId} disabled={busy}
            onChange={e => setNewId(e.target.value)} onKeyDown={e => { if (e.key === "Enter") addCorpse(); }}/></td>
            <td><input inputMode="numeric" aria-label="Nowe ID ciała po oskórowaniu" value={newSkinned} disabled={busy}
              onChange={e => setNewSkinned(e.target.value)} onKeyDown={e => { if (e.key === "Enter") addCorpse(); }}/></td>
            <td><button type="button" className="secondary-button" disabled={busy || !newId.trim()} onClick={addCorpse}>Dodaj</button></td></tr>
        </tbody></table>
      </section>
      <section aria-label="Potwory">
        <h4>Potwory <small>{names.length}</small></h4>
        <ul className="knife-config-names">{names.map(name => <li key={name}>{name}
          <button type="button" className="spell-remove" aria-label={`Usuń potwora ${name}`} disabled={busy || names.length <= 1}
            onClick={() => setNames(names.filter(n => n !== name))}>×</button></li>)}</ul>
        <div className="knife-config-add"><input aria-label="Nazwa nowego potwora" placeholder="np. Fury Roco" value={newName} maxLength={64} disabled={busy}
          onChange={e => setNewName(e.target.value)} onKeyDown={e => { if (e.key === "Enter") addName(); }}/>
          <button type="button" className="secondary-button" disabled={busy || !newName.trim()} onClick={addName}>Dodaj</button></div>
      </section>
      {error && <p role="alert">{error}</p>}
      <footer className="knife-config-actions">
        <button type="button" className="secondary-button" disabled={busy} onClick={restore}>Przywróć domyślne</button>
        <button type="button" className="primary-button" disabled={busy} onClick={save}>Zapisz</button>
      </footer>
    </div>
  </dialog>;
}
