"use client";

import { t, labels, locale, plural } from "@/lib/i18n";
import Help from "@/app/Help";
import { useState } from "react";
import type { TransitionAction, PassiveMapState, TransitionObservation } from "@/lib/api";

export const transitionActions: Readonly<Record<TransitionAction, string>> = labels("transition.action", ["clear_web", "step", "use", "use_with", "use_then_step", "use_with_then_step", "fly_up", "unknown"] as const);
const objectBehaviors = labels("transition.behavior", ["open_then_step", "use_enters", "manual_close", "closes_after_exit", "stays_open"] as const);
const statuses = labels("transition.status", ["waiting", "learning", "suspended", "error"] as const);
const reasons: Readonly<Record<string,string>> = labels("transition.reason", ["matched_command", "door_crossing", "no_command", "multiple_commands", "incomplete_entry", "multiple_steps", "sample_gap", "long_jump"] as const);
const coords = (p: number[] | null) => p ? `(${p.join(", ")})` : t("transition.unknownPlace");
const tiles=(n:number)=>plural("transition.tiles",n);

export default function TransitionLearningPanel({ map, onInspect }: { map?: PassiveMapState; onInspect?:(pos:[number,number,number])=>void }) {
  const learning=map?.learning, center=map?.center;
  const [order,setOrder]=useState<"nearby"|"recent">("nearby");
  const distance=(r:TransitionObservation)=>r.entry && center && r.entry[2]===center[2] ? Math.max(Math.abs(r.entry[0]-center[0]),Math.abs(r.entry[1]-center[1])) : Infinity;
  const fallback=[...(learning?.records ?? [])].sort((a,b)=>{
    if(!a.entry || !b.entry) return Number(!a.entry)-Number(!b.entry);
    return distance(a)-distance(b) || b.last_seen-a.last_seen;
  });
  const rows=order==="nearby" ? learning?.nearby_records ?? fallback : learning?.records ?? [];
  return <section aria-label={t("transition.aria")} className="transition-panel">
    <div className="transition-summary">
      <div><span>{t("transition.saved")}<Help text={t("transition.savedNote")}/></span><strong>{learning?.total ?? 0}</strong></div>
      <div><span>{t("transition.linked")}<Help text={t("transition.linkedNote")}/></span><strong className="text-emerald-300">{learning?.observed ?? 0}</strong></div>
      <div><span>{t("transition.toCheck")}<Help text={t("transition.toCheckNote")}/></span><strong className="text-amber-300">{(learning?.total ?? 0)-(learning?.observed ?? 0)}</strong></div>
    </div>
    <div className="transition-toolbar">
      <div><h2>{t("transition.base")}</h2><span className={learning?.status === "learning" ? "text-emerald-300" : "text-amber-300"}>
        {statuses[learning?.status ?? "waiting"]}
      </span></div>
    <label>{t("transition.sort")}{order==="nearby"&&<Help text={t("transition.context")}/>}
      <select value={order} onChange={e=>setOrder(e.target.value as "nearby"|"recent")}>
        <option value="nearby">{t("transition.nearby")}</option><option value="recent">{t("transition.recent")}</option>
      </select>
    </label>
    </div>
    {learning?.storage_error && <p role="alert" className="mt-2 rounded border border-amber-700 p-2 text-sm text-amber-200">{learning.storage_error}</p>}
    {learning?.limit_reached && <p className="mt-2 text-xs text-amber-300">{t("transition.limit")}</p>}
    {!learning?.records.length ? <div className="empty-panel"><h2>{t("transition.emptyTitle")}</h2><p>{t("transition.emptyNote")}</p></div> :
      <div className="transition-table-wrap">
        <table className="transition-table">
          <thead><tr>
            <th>{t("transition.col.distance")}</th><th>{t("transition.col.entryExit")}</th><th>{t("transition.col.action")}</th><th>{t("transition.col.rating")}</th><th>{t("transition.col.repeats")}</th>
          </tr></thead>
          <tbody>{rows.map(row => <tr key={row.id}>
            <td><span className="distance-label">{!row.entry ? "—" : !center ? "—" : row.entry[2]!==center[2] ? `Z = ${row.entry[2]}` : distance(row)===0 ? t("transition.here") : tiles(distance(row))}</span>
              <small className="distance-caption">{t(!row.entry ? "transition.unknownEntry" : !center ? "transition.noPosition" : row.entry[2]!==center[2] ? "transition.otherFloor" : "transition.thisFloor")}</small>
            </td>
            <td className="font-mono whitespace-nowrap">
              <strong className="transition-entry">{coords(row.entry)}</strong> <span className="transition-exit">{row.object_change ? `· ${row.object_change.closed_id} ⇄ ${row.object_change.open_id}` : `→ ${coords(row.exit)}`}</span>
              <p className="mt-1 text-gray-400">#{row.id.slice(0,6)} · ID: {row.entry_items.join(", ") || "?"}</p>
              <div className="transition-row-tools">
              {row.entry && map?.cells.some(c=>c.x===row.entry![0] && c.y===row.entry![1] && c.z===row.entry![2]) && onInspect &&
                <button className="text-sky-300" aria-label={t("transition.showOnMap",{place:coords(row.entry)})} onClick={()=>onInspect([...row.entry!] as [number,number,number])}>{t("itemId.map")} ↗</button>}
              <details className="font-sans text-gray-400"><summary className="cursor-pointer">{t("transition.details")}</summary>
                <p>{t("transition.before")}: {coords(row.origin)}</p>
                <p>{t(row.direction === "up" ? "transition.up" : row.direction === "down" ? "transition.down" : "transition.sameFloor")}</p>
                <p>{t("transition.entryIds")}: {row.entry_items.join(", ") || t("transition.unknownLower")}</p>
                <p>{t("transition.lastSeen")}: {new Date(row.last_seen*1000).toLocaleString(locale())}</p>
                <p>{row.object_change ? t("transition.objectChange") : reasons[row.reason] ?? t("transition.needsCheck")}</p>
              </details>
              </div>
            </td>
            <td>{row.object_change ? objectBehaviors[row.object_change.behavior] : transitionActions[row.action]}
              {row.item_id !== null && <p className="mt-1 text-gray-400">{t("transition.object")}: {row.item_id}</p>}
              {row.prepared_item_id && <p className="text-gray-400">{t("transition.afterOpen")}: {row.prepared_item_id}</p>}
              {row.tool_id !== null && <p className="text-gray-400">{t("transition.tool")}: {({3003:"Rope",3457:"Shovel",8899:"AiO"} as Record<number,string>)[row.tool_id] ?? "ID"} · {row.tool_id}</p>}
            </td>
            <td><span className={`evidence-badge ${row.evidence}`}>{t(row.evidence === "observed" ? "transition.observed" : "transition.review")}</span>
              {row.variable_exit && <p className="mt-1 text-amber-300">{t("transition.variableExit")}</p>}
            </td>
            <td><span className="repeat-count">× {row.count}</span><small className="distance-caption">{new Date(row.last_seen*1000).toLocaleTimeString(locale())}</small></td>
          </tr>)}</tbody>
        </table>
      </div>}
    <div className="transition-footer"><span>{t(order==="recent" ? "transition.footerRecent" : "transition.footerNearby",{shown:rows.length,total:learning?.total ?? 0})}{order==="nearby" && learning && !learning.nearby_records ? " "+t("transition.fromLast20") : ""}</span><span>{t("transition.passive")}</span></div>
    <details className="map-source"><summary>{t("transition.howTo")}</summary><p className="mt-2">{t("transition.howToNote")}</p>{learning?.file && <p className="mt-1 break-all">{t("transition.localFile")}: local_data/{learning.file}</p>}</details>
  </section>;
}
