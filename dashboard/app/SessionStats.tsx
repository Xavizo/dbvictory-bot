import { t, num } from "@/lib/i18n";
import type {BotState} from "@/lib/api";

export function sessionTime(seconds:number) {
  const total=Math.max(0,Math.floor(seconds));
  return [Math.floor(total/3600),Math.floor(total/60)%60,total%60].map(n=>String(n).padStart(2,"0")).join(":");
}
const compact=(n:number)=>num(n,{notation:"compact",maximumFractionDigits:1});
const signed=(n:number)=>`${n>0?"+":""}${num(n)}`;

export default function SessionStats({stats,senzu}:{stats:BotState["session_statistics"];senzu?:BotState["senzu_usage"]}) {
  const consumption=senzu && <>
    <span title={t("stats.senzuTitle")+(senzu.ended?" "+t("stats.sessionEnded"):"")}>{t("stats.senzu")} <strong>{num(senzu.total)}</strong></span>
    <span title={t("stats.senzuRateTitle",{sample:sessionTime(senzu.window_seconds)})}>Senzu/h <small>15 min</small> <strong>{senzu.per_hour!=null?num(senzu.per_hour):"—"}</strong></span>
  </>;
  if(!stats)return consumption?<div className="session-stats" aria-label={t("stats.aria")}>{consumption}</div>:null;
  const ready=stats.start_level!==null;
  const stateLabel=stats.status==="ended"?t("stats.ended"):stats.status==="stale"?t("stats.stale"):stats.status==="error"?t("stats.error"):!ready?t("common.waitingForCharacter"):null;
  const duration=Math.min(stats.window_seconds,stats.window_target_seconds);
  const rateTitle=stats.xp_per_hour===null?t("stats.waitingXpSample"):t("stats.rateTitle",{window:sessionTime(duration),xp:stats.xp_gained!=null?num(stats.xp_gained):"—"});
  return <div className="session-stats" aria-label={t("stats.aria")}>
    <span title={t(stats.source==="panel"?"stats.sessionPanel":"stats.sessionLogin")}>{t("stats.session")} <strong>{ready?sessionTime(stats.elapsed_seconds):"—"}</strong></span>
    <span title={ready?t("stats.levelsTitle",{start:stats.start_level??"—",level:stats.level??"—"}):t("stats.waitingLevel")}>{t("stats.levels")} <strong>{stats.levels_gained===null?"—":signed(stats.levels_gained)}</strong></span>
    <span title={rateTitle}>XP/h <small>15 min</small> <strong>{stats.xp_per_hour===null?"—":compact(stats.xp_per_hour)}</strong></span>
    {consumption}
    {stateLabel?<small className="session-note">{stateLabel}</small>:stats.xp_gained===null?<small className="session-note">{t("stats.waitingXp")}</small>:duration<stats.window_target_seconds?<small className="session-note" title={t("stats.shortSample")}>{t("stats.sample")} {Math.floor(duration/60)}:{String(Math.floor(duration)%60).padStart(2,"0")} / 15:00</small>:null}
  </div>;
}
