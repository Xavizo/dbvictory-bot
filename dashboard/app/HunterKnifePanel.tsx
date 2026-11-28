"use client";
import { t } from "@/lib/i18n";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { toggleAction, updateActionConfig, type BotState, type KnifeCorpse } from "@/lib/api";
import HelpTip from "./HelpTip";

const KNIFE = 5908;
const SPENT = 8141;

/** "mutant wolf" → "Mutant Wolf"; the bot saves names in lower case. */
export const monsterLabel = (name?: string) =>
  name ? name.replace(/(^|[\s-])(\p{L})/gu, (_, sep: string, ch: string) => sep + ch.toUpperCase()) : t("knife.unknown");
export const corpseLabel = (c: KnifeCorpse) => `${monsterLabel(c.monster)} (id:${c.id})`;

/** A corpse typed by hand, or the reason it cannot be added. */
export function manualCorpse(list: KnifeCorpse[], name: string, id: string, skinned: string): KnifeCorpse | string {
  const monster = name.trim().replace(/\s+/g, " ");
  const body = Number(id.trim()), after = skinned.trim() ? Number(skinned.trim()) : SPENT;
  const valid = (n: number) => Number.isInteger(n) && n >= 1 && n <= 65535;
  if (!monster || monster.length > 64 || /[[\]]/.test(monster)) return t("knife.badName");
  if (!valid(body) || !valid(after) || body === KNIFE || body === after) return t("knife.badId");
  if (list.some(c => c.id === body)) return t("knife.exists", { id: body });
  if (list.some(c => c.skinned === body) || list.some(c => c.id === after)) return t("knife.badId");
  return { id: body, skinned: after, monster };
}

export default function HunterKnifePanel({ state, onRefresh }: {
  state: BotState | null;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const action = state?.actions.find(a => a.name === "auto_hunter_knife");
  if (!action) return null;
  const enabled = action.enabled;
  const info = state?.hunter_knife;
  const corpses = info?.corpses ?? [];
  const toggle = () => run(() => toggleAction("auto_hunter_knife", !enabled));
  const save = (list: KnifeCorpse[]) => run(() => updateActionConfig("auto_hunter_knife", { corpses: list }));
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
      onRefresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  const timing = info?.last_timing_ms;
  const issue = info?.last_issue;
  const details = info ? [
    t("knife.counts", { combat: info.from_combat ?? 0, loot: info.personal_loot, done: info.processed, denied: info.denied ?? 0 }),
    info.unassigned_bodies ? `${t("knife.unassigned")}: ${info.unassigned_bodies}` : "",
    info.unattributed ? `${t("knife.unattributed")}: ${info.unattributed}` : "",
    info.unclassified_loot > 0 ? `${t("knife.unclassified")}: ${info.unclassified_loot}` : "",
    timing ? t("knife.timing", { recognition: timing.recognition, wait: timing.wait_for_use })
      + (timing.delivery_wait != null ? ` · ${t("knife.delivery", { ms: timing.delivery_wait })}` : "")
      + ` · ${t("knife.confirmation", { ms: timing.confirmation })}` : "",
  ].filter(Boolean).join("\n") : "";
  const issueText = issue ? `${t(issue.action === "skin" ? "knife.skin" : "knife.move")} · (${issue.position.join(", ")}) · ${issue.reason}`
    + (issue.spell ? ` · ${t("knife.sinceSpell", { ms: issue.since_spell_ms ?? "—", spell: issue.spell })}` : "") : "";
  return <section className="barrier-band-panel knife-panel" aria-label="Hunter Knife">
    <div><strong>Hunter Knife</strong>
      {info && <small>{t("knife.stats", { done: info.processed, loot: info.personal_loot })} {details && <HelpTip text={details} label={t("knife.details")}/>}
        {issue && <> · <span className="knife-issue">{t("knife.lastIssue")}</span> <HelpTip text={issueText} label={t("knife.lastIssue")}/></>}</small>}
    </div>
    <span className="barrier-band-status">{enabled ? info?.status ?? t("speedUp.waiting") : t("barrierSpell.off")}</span>
    {info?.corpses && <button type="button" className="knife-settings" aria-haspopup="dialog" aria-label={t("knife.settings")}
      title={t("knife.settings")} onClick={() => setOpen(true)}><span aria-hidden="true">⚙</span><small>{corpses.length}</small></button>}
    <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
      aria-label={t("knife.switch")} disabled={busy || !!state?.automation_paused} onClick={toggle}><span/></button>
    {error && !open && <p role="alert">{error}</p>}
    {open && info?.corpses && <CorpseDialog corpses={corpses} defaults={info.default_corpses ?? []} busy={busy} error={error}
      save={save} onClose={() => { setOpen(false); setError(""); }}/>}
  </section>;
}

function CorpseDialog({ corpses, defaults, busy, error, save, onClose }: {
  corpses: KnifeCorpse[]; defaults: KnifeCorpse[]; busy: boolean; error: string;
  save: (list: KnifeCorpse[]) => Promise<boolean>; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(""), [id, setId] = useState(""), [skinned, setSkinned] = useState("");
  const [problem, setProblem] = useState("");
  useEffect(() => { const d = dialog.current; d?.showModal(); return () => d?.close(); }, []);
  async function add(e: FormEvent) {
    e.preventDefault();
    const entry = manualCorpse(corpses, name, id, skinned);
    if (typeof entry === "string") { setProblem(entry); return; }
    setProblem("");
    if (await save([...corpses, entry])) { setName(""); setId(""); setSkinned(""); }
  }
  return <dialog ref={dialog} className="spell-picker-dialog knife-dialog" aria-label={t("knife.settings")}
    onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="spell-picker-content">
      <header><strong>{t("knife.settings")} <HelpTip text={t("knife.learn")}/></strong>
        <button type="button" className="spell-remove" aria-label={t("common.close")} onClick={onClose}>×</button></header>
      <ul className="knife-list" aria-label={t("knife.corpsesAria")}>
        {corpses.map(c => <li key={c.id}><span title={t("knife.skinned", { id: c.skinned })}>{corpseLabel(c)}</span>
          <button type="button" className="knife-remove" aria-label={t("knife.remove", { id: corpseLabel(c) })} disabled={busy}
            onClick={() => save(corpses.filter(x => x.id !== c.id))}>×</button></li>)}
        {!corpses.length && <li className="knife-empty">{t("knife.none")}</li>}
      </ul>
      <form className="knife-add" onSubmit={add}>
        <input aria-label={t("knife.name")} placeholder={t("knife.name")} value={name} maxLength={64} onChange={e => setName(e.target.value)}/>
        <input aria-label={t("knife.id")} placeholder="ID" inputMode="numeric" value={id} onChange={e => setId(e.target.value)}/>
        <span className="knife-after"><input aria-label={t("knife.after")} placeholder={String(SPENT)} inputMode="numeric" value={skinned}
          onChange={e => setSkinned(e.target.value)}/><HelpTip text={t("knife.afterHelp", { id: SPENT })}/></span>
        <button type="submit" className="primary-button" disabled={busy}>{t("knife.add")}</button>
      </form>
      {(problem || error) && <p role="alert">{problem || error}</p>}
      <footer><button type="button" className="secondary-button" disabled={busy} onClick={() => save(defaults)}>{t("knife.defaults")}</button></footer>
    </div>
  </dialog>;
}
