import type { CreatureInfo, PlayerInfo, ProxySequence } from "@/lib/api";

const percent = (part: number, whole: number) => whole > 0 ? Math.round(part * 100 / whole) : 0;

function Meter({ name, value, limit, tone }: { name: string; value: number; limit: number; tone: string }) {
  return <div>
    <div className="mb-1 flex justify-between text-xs"><span className="text-gray-400">{name}</span><span className="tabular-nums">{value} / {limit}</span></div>
    <div className="h-3 overflow-hidden rounded-full bg-gray-700" role="meter" aria-label={name} aria-valuenow={value} aria-valuemin={0} aria-valuemax={limit}>
      <div className={`h-full rounded-full transition-all duration-300 ${tone}`} style={{ width: `${percent(value, limit)}%` }}/>
    </div>
  </div>;
}

/** HP and Ki bars with level and position, as the game proxy last saw them. */
export function PlayerStats({ player }: { player: PlayerInfo }) {
  const where = player.position.join(", ");
  return <div className="mb-8 space-y-3 rounded-lg border border-gray-700 bg-gray-800 p-4">
    <div className="flex items-center justify-between">
      <span className="text-sm font-semibold text-gray-300">Poziom {player.level}</span>
      <span className="text-xs tabular-nums text-gray-500">({where})</span>
    </div>
    <Meter name="HP" value={player.hp} limit={player.max_hp} tone="bg-red-500"/>
    <Meter name="Ki" value={player.mana} limit={player.max_mana} tone="bg-blue-500"/>
  </div>;
}

// The proxy's connection steps, in the order a login goes through them.
const PROXY_STEPS: [keyof Omit<ProxySequence, "timestamps">, string][] = [
  ["proxy_created", "Proxy utworzone"], ["listening", "Nasłuch"], ["client_connected", "Klient połączony"],
  ["server_connected", "Serwer połączony"], ["xtea_captured", "Klucze XTEA"], ["logged_in", "Zalogowano"],
  ["packets_flowing", "Ruch pakietów"],
];

/** Which proxy steps are done; the first missing one is where a login is stuck. */
export function ConnectionSequence({ seq, onClose }: { seq: ProxySequence; onClose: () => void }) {
  const stuckAt = PROXY_STEPS.findIndex(([step]) => !seq[step]);
  return <ol className="mb-4 flex flex-wrap items-center gap-1" aria-label="Etapy połączenia">
    {PROXY_STEPS.map(([step, name], position) => {
      const after = seq.timestamps?.[step];
      const when = after == null ? "" : `+${after}s`;
      const look = seq[step] ? "bg-emerald-900/50 text-emerald-300"
        : position === stuckAt ? "animate-pulse bg-red-900/50 text-red-300" : "bg-gray-800 text-gray-500";
      return <li key={step} className="flex items-center gap-1">
        {position > 0 && <span aria-hidden="true" className={`text-xs ${seq[step] ? "text-emerald-600" : "text-gray-600"}`}>→</span>}
        <span className={`rounded px-2 py-1 text-xs font-medium ${look}`} title={when ? `${name} (${when})` : name}>
          {name}{when && <small className="ml-1 text-[10px] opacity-70">{when}</small>}
        </span>
      </li>;
    })}
    <li><button aria-label="Zamknij etapy połączenia" onClick={onClose} className="ml-1 text-xs text-gray-500 hover:text-gray-300">✕</button></li>
  </ol>;
}

/** Creatures the observer reports on the player's floor, with their health in percent. */
export function CreatureList({ creatures }: { creatures: CreatureInfo[] }) {
  return <div className="mb-8">
    <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-gray-400">
      Stworzenia <span className="rounded-full bg-gray-700 px-2 py-0.5 text-xs tabular-nums text-gray-300">{creatures.length}</span>
    </h2>
    {creatures.length === 0 ? <p className="text-sm text-gray-500">Brak stworzeń w pobliżu</p> :
      <ul className="divide-y divide-gray-700 rounded-lg border border-gray-700 bg-gray-800">
        {creatures.map(creature => <li key={creature.id} className="flex items-center gap-3 px-4 py-2">
          <span className="w-28 shrink-0 truncate text-sm text-gray-200" title={`#${creature.id}`}>{creature.name || `#${creature.id}`}</span>
          <span className="w-24 shrink-0 text-xs tabular-nums text-gray-500">({creature.x}, {creature.y}, {creature.z})</span>
          <span className="h-2.5 flex-1 overflow-hidden rounded-full bg-gray-700">
            <span className="block h-full rounded-full bg-gradient-to-r from-emerald-600 to-emerald-400 transition-all duration-300" style={{ width: `${creature.health}%` }}/>
          </span>
          <span className="w-10 text-right text-xs tabular-nums text-gray-300">{creature.health}%</span>
        </li>)}
      </ul>}
  </div>;
}
