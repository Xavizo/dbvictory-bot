import { t, type TextKey } from "@/lib/i18n";
import type {CombatConfig,LureProgress} from '@/lib/cavebotModel';

export function LureSettings({value,onChange}:{value:CombatConfig;onChange:(next:CombatConfig)=>void}) {
  return <div className="v2-lure-settings">
    <div className="lure-settings-title"><strong>{t("lure.title")}</strong><span>{t("lure.attackMoving")}</span></div>
    <div className="lure-settings-fields">
      <label>{t("lure.stopAt")}<span><input aria-label={t("lure.countAria")} type="number" min={1} max={12} value={value.lure_count} onChange={e=>onChange({...value,lure_count:Number(e.target.value)})}/> {t("lure.mobsInGroup")}</span></label>
      <label>{t("lure.waitFrom")}<span><input aria-label={t("lure.distanceAria")} type="number" min={1} max={7} value={value.lure_distance} onChange={e=>onChange({...value,lure_distance:Number(e.target.value)})}/> SQM</span></label>
      <label>{t("lure.originLimit")}<span><input aria-label={t("lure.originAria")} type="number" min={1} max={64} value={value.lure_origin_limit??12} onChange={e=>onChange({...value,lure_origin_limit:Number(e.target.value)})}/> SQM</span></label>
    </div>
    <small title={t("lure.countTitle")}>{t("lure.countNote")}</small>
  </div>;
}

export default function LureStatus({progress,paused=false}:{progress:LureProgress;paused?:boolean}) {
  const {following,goal,waiting,stop_reason:reason}=progress;
  const count=progress.engaged??following;
  const caption=t(paused?'lure.paused':reason?`lure.stop.${reason}` as TextKey:waiting?'lure.waiting':'lure.gathering');
  return <div className={`lure-progress ${reason?'is-fighting':''}`} role="status" aria-label={t("lure.progress")} title={`${t("lure.inGroup")} ${count}/${goal} · ${caption}`}>
    <div><strong>{t("lure.inGroup")} <b>{count} <span>/ {goal}</span></b></strong><small>{caption}</small></div>
    <div className="lure-progress-dots" aria-hidden="true">{Array.from({length:Math.min(12,goal)},(_,i)=><i key={i} className={i<count?'filled':''}/>)}</div>
  </div>;
}
