import type {BotState} from "./api";

type Stats = NonNullable<BotState["session_statistics"]>;
const windowSeconds = 900;

function emptyStats(): Stats {
  return {source:"panel", status:"waiting", elapsed_seconds:0, start_level:null,
    level:null, levels_gained:null, xp_gained:null, xp_per_hour:null,
    window_seconds:0, window_target_seconds:windowSeconds, sample_age_seconds:null};
}

/** Consumes snapshots already received by this panel. Times are monotonic seconds. */
export class PanelSession {
  private id: number | undefined;
  private start = 0;
  private lastSample = -Infinity;
  private firstXp: number | null = null;
  private samples: {at:number; xp:number}[] = [];
  private stats = emptyStats();

  observe(state:BotState, now:number): BotState {
    if (state.session_statistics) return state;
    if (!state.connected) {
      this.id = undefined;
      if (this.stats.start_level !== null) this.stats = {...this.stats, status:"ended", xp_per_hour:null};
      return {...state, session_statistics:this.stats};
    }
    const id = state.commands?.session;
    if (id === undefined) return state;
    if (id !== this.id) {
      this.id = id;
      this.stats = emptyStats();
      this.samples = [];
      this.firstXp = null;
      this.lastSample = -Infinity;
    }
    const {level, experience, stats_age:age} = state.player;
    if (!Number.isInteger(level) || level < 1 || age === undefined || !(age >= 0 && age <= 2)) {
      if (this.stats.start_level !== null) this.stats = {...this.stats,status:"stale",xp_per_hour:null};
      return {...state, session_statistics:this.stats};
    }
    if (now - this.lastSample < 1) return {...state, session_statistics:this.stats};
    this.lastSample = now;
    if (this.stats.start_level === null) this.start = now;
    const startLevel = this.stats.start_level ?? level;
    this.stats = {...this.stats,status:"active",elapsed_seconds:Math.floor(now-this.start),
      start_level:startLevel,level,levels_gained:level-startLevel,sample_age_seconds:age,
      xp_gained:null,xp_per_hour:null,window_seconds:0};
    // A missing XP report appears as zero on older engines.
    if (Number.isSafeInteger(experience) && experience >= 0 && (experience > 0 || level === 1)) {
      this.firstXp ??= experience;
      const last = this.samples.at(-1);
      if (last && now-last.at > 5) this.samples = [];
      this.samples.push({at:now,xp:experience});
      const cutoff = now-windowSeconds;
      while (this.samples.length > 1 && this.samples[1].at <= cutoff) this.samples.shift();
      const [oldest,next] = this.samples;
      const start = Math.max(oldest.at,cutoff);
      const base = next && oldest.at < start
        ? oldest.xp+(next.xp-oldest.xp)*(start-oldest.at)/(next.at-oldest.at) : oldest.xp;
      const duration = now-start;
      this.stats = {...this.stats,xp_gained:experience-this.firstXp,window_seconds:duration,
        xp_per_hour:duration >= 1 ? Math.round((experience-base)*3600/duration) : null};
    }
    return {...state, session_statistics:this.stats};
  }
}
