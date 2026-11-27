"use client";
import { t } from "@/lib/i18n";
import { useState } from "react";
import { toggleAction, updateActionConfig, type BotState, type KnifeCorpse } from "@/lib/api";

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
  const corpses = info?.corpses ?? [];
  const toggle = () => run(() => toggleAction("auto_hunter_knife", !enabled));
  const save = (list: KnifeCorpse[]) => run(() => updateActionConfig("auto_hunter_knife", { corpses: list }));
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
      onRefresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return <section className="barrier-band-panel" aria-label="Hunter Knife">
    <div><strong>Hunter Knife</strong><small>5908 · {t("knife.fromList")}</small></div>
    <span className="barrier-band-status">{enabled ? info?.status ?? t("speedUp.waiting") : t("barrierSpell.off")}</span>
    <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
      aria-label={t("knife.switch")} disabled={busy || !!state?.automation_paused} onClick={toggle}><span/></button>
    {info && <small className="barrier-band-help">{t("knife.counts",{combat:info.from_combat ?? 0,loot:info.personal_loot,done:info.processed,denied:info.denied??0})}
      {!!info.unassigned_bodies && ` · ${t("knife.unassigned")} ${info.unassigned_bodies}`}
      {!!info.unattributed && ` · ${t("knife.unattributed")} ${info.unattributed}`}
      {info.unclassified_loot > 0 && ` · ${t("knife.unclassified")} ${info.unclassified_loot}`}</small>}
    {info?.last_timing_ms && <small className="barrier-band-help">{t("knife.timing",{recognition:info.last_timing_ms.recognition,wait:info.last_timing_ms.wait_for_use})}
      {info.last_timing_ms.delivery_wait != null && ` · ${t("knife.delivery",{ms:info.last_timing_ms.delivery_wait})}`} · {t("knife.confirmation",{ms:info.last_timing_ms.confirmation})}</small>}
    {info?.last_issue && <small className="barrier-band-help">{t("knife.lastIssue")} · {t(info.last_issue.action === "skin" ? "knife.skin" : "knife.move")} · ({info.last_issue.position.join(", ")}) · {info.last_issue.reason}
      {info.last_issue.spell && ` · ${t("knife.sinceSpell",{ms:info.last_issue.since_spell_ms ?? "—",spell:info.last_issue.spell})}`}</small>}
    {info?.corpses && <div className="knife-corpses" aria-label={t("knife.corpsesAria")}>
      <small>{t("knife.corpses")}:</small>
      {corpses.map(c => <span key={c.id} title={`${c.monster ? c.monster + " · " : ""}${t("knife.skinned",{id:c.skinned})}`}>{c.id}
        <button type="button" className="knife-remove" aria-label={t("knife.remove",{id:c.id})} disabled={busy}
          onClick={() => save(corpses.filter(x => x.id !== c.id))}>×</button></span>)}
      {!corpses.length && <small>{t("knife.none")}</small>}
      <button type="button" className="secondary-button" disabled={busy}
        onClick={() => save(info.default_corpses ?? [])}>{t("knife.defaults")}</button>
      <small className="barrier-band-help">{t("knife.learn")}</small>
    </div>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
