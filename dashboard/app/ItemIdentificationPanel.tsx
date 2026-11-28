"use client";
import { t, locale } from "@/lib/i18n";
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
  return <section className="speed-up-panel" aria-label={t("itemId.title")}>
    <div className="speed-heading">
      <strong>{t("itemId.title")}</strong><span>{t(enabled ? "itemId.on" : "itemId.off")}</span>
      <button className={`action-switch ${enabled ? "on" : ""}`} role="switch" aria-checked={enabled}
        aria-label={t("itemId.title")} disabled={busy || !online} onClick={() => void toggle()}><span/></button>
    </div>
    <small>{t("itemId.note")}</small>
    {enabled && (recent.length ? <table className="w-full text-xs mt-2">
      <thead><tr className="text-left text-gray-400"><th className="py-1">ID</th><th className="py-1">{t("itemId.place")}</th><th className="py-1">{t("itemId.time")}</th></tr></thead>
      <tbody>{recent.map((item, i) => <tr key={`${item.time}-${i}`} className="border-t border-gray-700">
        <td className="py-1"><strong>{item.item_id}</strong></td>
        <td className="py-1">{item.where === "inventory" ? t("itemId.inventory") : item.position ? item.position.join(", ") : t("itemId.map")}</td>
        <td className="py-1">{new Date(item.time * 1000).toLocaleTimeString(locale())}</td>
      </tr>)}</tbody>
    </table> : <p className="text-xs text-gray-400 mt-2">{t("itemId.none")}</p>)}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
