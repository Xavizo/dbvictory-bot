import type {BotState} from "@/lib/api";

export function sessionTime(seconds:number) {
  const total=Math.max(0,Math.floor(seconds));
  return [Math.floor(total/3600),Math.floor(total/60)%60,total%60].map(n=>String(n).padStart(2,"0")).join(":");
}
const number=new Intl.NumberFormat("pl-PL",{notation:"compact",maximumFractionDigits:1});
const signed=(n:number)=>`${n>0?"+":""}${n.toLocaleString("pl-PL")}`;

export default function SessionStats({stats,senzu}:{stats:BotState["session_statistics"];senzu?:BotState["senzu_usage"]}) {
  const consumption=senzu && <>
    <span title={`Potwierdzone zużycie od uruchomienia Cavebota. Kolejny raport zapasu potwierdza poprzednie użycie.${senzu.ended?" Sesja zakończona.":""}`}>Senzu w sesji <strong>{senzu.total.toLocaleString("pl-PL")}</strong></span>
    <span title={`Potwierdzone zużycie przeliczone na godzinę. Próbka ${sessionTime(senzu.window_seconds)} z ostatnich 15 minut.`}>Senzu/h <small>15 min</small> <strong>{senzu.per_hour?.toLocaleString("pl-PL") ?? "—"}</strong></span>
  </>;
  if(!stats)return consumption?<div className="session-stats" aria-label="Statystyki sesji">{consumption}</div>:null;
  const ready=stats.start_level!==null;
  const stateLabel=stats.status==="ended"?"Zakończona":stats.status==="stale"?"Brak świeżego odczytu":stats.status==="error"?"Statystyki niedostępne":!ready?"Czekam na postać":null;
  const duration=Math.min(stats.window_seconds,stats.window_target_seconds);
  const rateTitle=stats.xp_per_hour===null?"Czekam na świeżą próbkę XP":`Tempo netto z ostatnich ${sessionTime(duration)}. Zysk XP w sesji: ${stats.xp_gained?.toLocaleString("pl-PL") ?? "—"}.`;
  return <div className="session-stats" aria-label="Statystyki sesji">
    <span title={stats.source==="panel"?"Czas od pierwszego odczytu w tym oknie. Odświeżenie okna rozpoczyna pomiar od nowa.":"Czas od pierwszego odczytu po zalogowaniu. Pauza automatyki nie zeruje sesji."}>Sesja <strong>{ready?sessionTime(stats.elapsed_seconds):"—"}</strong></span>
    <span title={ready?`Poziom początkowy ${stats.start_level} → obecny ${stats.level}. Bilans uwzględnia utratę poziomów.`:"Czekam na odczyt poziomu"}>Poziomy <strong>{stats.levels_gained===null?"—":signed(stats.levels_gained)}</strong></span>
    <span title={rateTitle}>XP/h <small>15 min</small> <strong>{stats.xp_per_hour===null?"—":number.format(stats.xp_per_hour)}</strong></span>
    {consumption}
    {stateLabel?<small className="session-note">{stateLabel}</small>:stats.xp_gained===null?<small className="session-note">Czekam na XP</small>:duration<stats.window_target_seconds?<small className="session-note" title="Do zebrania 15 minut tempo jest liczone z dostępnej, krótszej próbki.">Próbka {Math.floor(duration/60)}:{String(Math.floor(duration)%60).padStart(2,"0")} / 15:00</small>:null}
  </div>;
}
