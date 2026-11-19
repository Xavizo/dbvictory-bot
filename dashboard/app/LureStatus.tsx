import type {CombatConfig,LureProgress} from '@/lib/cavebotModel';

export function LureSettings({value,onChange}:{value:CombatConfig;onChange:(next:CombatConfig)=>void}) {
  return <div className="v2-lure-settings">
    <div className="lure-settings-title"><strong>Zbierz grupę</strong><span>Atakuj również w ruchu</span></div>
    <div className="lure-settings-fields">
      <label>Zatrzymaj się przy<span><input aria-label="Liczba mobów do lurowania" type="number" min={1} max={12} value={value.lure_count} onChange={e=>onChange({...value,lure_count:Number(e.target.value)})}/> mobach w grupie</span></label>
      <label>Czekaj na grupę od<span><input aria-label="Odstęp lurowania SQM" type="number" min={1} max={7} value={value.lure_distance} onChange={e=>onChange({...value,lure_distance:Number(e.target.value)})}/> SQM</span></label>
    </div>
    <small title="Limit liczy sumę bez duplikatów: potwierdzone podążanie i osiągalne moby obok postaci. Nie jest to odczyt celu agresji serwera.">Liczą się podążające moby oraz te stojące obok. Cele bez dojścia nie zatrzymują trasy.</small>
  </div>;
}

export default function LureStatus({progress,paused=false}:{progress:LureProgress;paused?:boolean}) {
  const {following,goal,waiting,stop_reason:reason}=progress;
  const count=progress.engaged??following;
  const captions = {group:'Grupa zebrana · walczę',timeout:'Limit czasu · walczę',transition:'Przed przejściem · walczę',corridor:'Przed wąskim korytarzem · walczę'};
  const caption=paused?'Lurowanie wstrzymane':reason?captions[reason]:waiting?'Czekam na moby · atakuję':'Zbieram grupę · atakuję';
  return <div className={`lure-progress ${reason?'is-fighting':''}`} role="status" aria-label="Postęp lurowania" title={`W grupie ${count}/${goal} · ${caption}`}>
    <div><strong>W grupie <b>{count} <span>/ {goal}</span></b></strong><small>{caption}</small></div>
    <div className="lure-progress-dots" aria-hidden="true">{Array.from({length:Math.min(12,goal)},(_,i)=><i key={i} className={i<count?'filled':''}/>)}</div>
  </div>;
}
