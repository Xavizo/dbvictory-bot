import { authenticatedFetch } from "./auth";

const API_BASE = "http://127.0.0.1:8089";

export interface MapNavigationFeature {
  kind: "stairs" | "ladder" | "descent" | "hole" | "covered_hole" | "rope_spot" | "door" | "level_gate" | "teleport" | "floor_change" | "passage";
  item_id: number; stack: number; source: "confirmed_id" | "confirmed_location" | "client_hint" | "user_annotation" | "observed";
  observation_count?:number;
  behavior_hint?:"use_enters"|"open_then_step"|"unknown";
  note?: string;
  required_level?: number; requirement_source?: "user_report";
  activation_hint?: "use" | "rope" | "step" | "shovel" | "fly_up" | "rope_or_fly_up" | "aio" | "open_if_closed";
  direction_hint?: "up" | "down";
  door_state_hint?: "open" | "closed" | "unknown";
  closing_policy?: "unknown" | "automatic" | "manual" | "after_exit" | "observed_open";
  lock_hint?: boolean;
}

export interface PassiveMapCell {
  x: number; y: number; z: number;
  kind: "clear" | "blocked" | "avoid" | "no_ground" | "unknown";
  stairs: "confirmed_id" | "type_flag" | null;
  occupants: number;
  occupant_types?: Partial<Record<"player" | "self" | "npc" | "monster" | "pet" | "unknown", number>>;
  pet_names?: string[];
  pet_ref?: string;
  pet_manual?: boolean;
  items: { id: number; flags: string[]; stack: number; lens_help?: number; minimap_color?: number }[];
  navigation?: MapNavigationFeature[];
  transitions?: MapTransitionLink[];
  server_blocking?: number; ground_speed?: number; reason?: string;
}

export interface PassiveMapState {
  preview?: boolean;
  status: string; passive: boolean; stale: boolean;
  cells: PassiveMapCell[]; message?: string;
  center?: [number, number, number]; origin?: [number, number];
  width?: number; height?: number; read_ms?: number;
  age_seconds?: number; captured_at?: number; catalogue_items?: number;
  asset?: { status: string; message: string; size?: number; known_asset?: boolean };
  learning?: TransitionLearningState;
  annotation_scope?: string | null; annotation_error?: string | null; annotation_editable?: boolean;
}

export type TransitionAction = "step" | "use" | "use_with" | "use_then_step" | "use_with_then_step" | "fly_up" | "unknown";
export interface MapTransitionLink {
  id: string; exit: [number, number, number]; action: TransitionAction;
  direction: "up" | "down" | "same_floor"; count: number;
}
export interface TransitionObservation extends MapTransitionLink {
  prepared_item_id?: number;
  object_change?: {kind:"door"|"level_gate"|"passage";closed_id:number;open_id:number;
    behavior:"open_then_step"|"use_enters"|"manual_close"|"closes_after_exit"|"stays_open"};
  origin: [number, number, number]; entry: [number, number, number] | null;
  item_id: number | null; tool_id: number | null; entry_items: number[];
  evidence: "observed" | "review"; reason: string;
  first_seen: number; last_seen: number; variable_exit: boolean;
}
export interface TransitionLearningState {
  status: "waiting" | "learning" | "suspended" | "error";
  total: number; observed: number; records: TransitionObservation[];
  nearby_records?: TransitionObservation[];
  storage_error?: string | null; file?: string; limit_reached?: boolean; passive: boolean;
}

export interface ActionInfo {
  name: string;
  enabled: boolean;
  running: boolean;
  completed?: boolean;
  description: string;
  logs?: string[];
  config?: Record<string, any>;
}

export interface PlayerInfo {
  stats_age?: number;
  hp: number;
  max_hp: number;
  mana: number;
  max_mana: number;
  level: number;
  experience: number;
  position: [number, number, number];
  magic_level: number;
  soul: number;
}

export interface CreatureInfo {
  id: number;
  health: number;
  name: string;
  x: number;
  y: number;
  z: number;
}

export interface WaypointInfo {
  type: string;
  direction?: string;
  x?: number;
  y?: number;
  z?: number;
  item_id?: number;
  stack_pos?: number;
  index?: number;
  from_x?: number;
  from_y?: number;
  from_z?: number;
  to_x?: number;
  to_y?: number;
  to_z?: number;
  to_stack_pos?: number;
  label?: string;
  pos: number[];
  player_pos?: number[];
  dll_pos?: number[];
  t: number;
}

export interface RecordingLastStats {
  xp_per_hour: number;
  kills_per_hour: number;
  senzu_per_hour: number;
  session_seconds: number;
  timestamp: string;
}

export interface RecordingInfo {
  name: string;
  mode?: "manual" | "adaptive";
  count: number;
  created_at: string;
  last_stats?: RecordingLastStats;
}

export interface MinimapNodeInfo {
  index: number;
  type: string;
  target: [number, number, number];
  visited: boolean;
}

export interface MinimapData {
  grid: string[];
  width: number;
  height: number;
  origin: [number, number];
  floor: number;
  floors: number[];
  nodes: MinimapNodeInfo[];
  player_node_index: number;
}

export interface MinimapSequence {
  seq_index: number;
  floor: number;
  start: number;
  end: number;
  minimap: MinimapData;
}

export interface ActionsMapNode {
  type: string;
  target: [number, number, number];
  item_id?: number;
  stack_pos?: number;
  index?: number;
  label?: string;
  x?: number;
  y?: number;
  z?: number;
  wp_range?: [number, number];
}

export interface ActionsMapResponse {
  name: string;
  actions_map: ActionsMapNode[];
  text_preview: string;
  node_count: number;
}

export interface CavebotState {
  recording: {
    active: boolean;
    mode?: "manual" | "adaptive";
    hunt_progress?: {stage: 0 | 1 | 2 | 3 | 4 | null; areas?: number; total_areas?: number; revisited?: number; sectors?:number; missing_sectors?:number} | null;
    name: string;
    waypoint_count: number;
    waypoints: WaypointInfo[];
    position_count?: number;
    observed_path?: {pos:[number,number,number];t:number;break_before?:boolean}[];
    trace_limited?: boolean;
  };
  playback: {
    active: boolean;
    recording_name: string;
    index: number;
    total: number;
    loop: boolean;
    logs: string[];
    minimap: MinimapSequence[] | Record<string, MinimapData> | null;
    actions_map_count: number;
    stats?: {
      loop_count: number;
      kills: number;
      senzu_used: number;
      xp_gained: number;
      xp_per_hour: number;
      predicted_level_1h: number;
      session_seconds: number;
      senzu_per_hour: number;
      senzu_series: [number, number][];
    };
  };
  recordings: RecordingInfo[];
}

export interface ProxySequence {
  proxy_created: boolean;
  listening: boolean;
  client_connected: boolean;
  server_connected: boolean;
  xtea_captured: boolean;
  logged_in: boolean;
  packets_flowing: boolean;
  timestamps?: Record<string, number | null>;
}

export type AlertKind = "private" | "default" | "player";
export type AlertConfig = Record<AlertKind, {enabled:boolean;sound:boolean;scope:"always"|"cavebot"}>;
export interface AlertState {
  paused?:boolean;silenced_through?:number;
  session:string;config:AlertConfig;latest_id:number;unread:number;cavebot_active:boolean;map_fresh:boolean;
  chat_received:number;last_chat_at:number|null;
  events:{id:number;kind:AlertKind;at:number;sender:string;text:string;sound:boolean;position?:number[]}[];
}
export const configureAlerts=(config:AlertConfig)=>control("/api/alerts",{command:"configure",config});
export const acknowledgeAlerts=(through:number)=>control("/api/alerts",{command:"acknowledge",through});
export const setAlertsPaused=(paused:boolean)=>control("/api/alerts",{command:"set_paused",paused});

export interface BotState {
  commands?: {session:number} | null;
  senzu_usage?: {total:number; per_hour:number|null; window_seconds:number; ended:boolean}|null;
  packet_observer?: {status:"active"|"degraded"|"error"|"closed";pending:number;pending_bytes:number;processing:boolean;capacity:number;accepted:number;applied:number;dropped:number;failures:number;lag_ms:number;max_lag_ms:number;last_problem:string|null;last_applied_age_seconds:number|null}|null;
  session_statistics?: {
    source?: "panel";
    status:"waiting"|"active"|"stale"|"ended"|"error";
    elapsed_seconds:number;start_level:number|null;level:number|null;levels_gained:number|null;
    xp_gained:number|null;xp_per_hour:number|null;window_seconds:number;window_target_seconds:number;
    sample_age_seconds:number|null;
  };
  alerts?:AlertState|null;
  pause_hotkey?:{registered:boolean;error:string|null;key:string}|null;
  profile_selection?:{combat:string|null;bot:string|null;routes:Record<string,string>};
  speed_up?:{status:string;remaining:number|null;offset:number|null;effect_active:boolean|null;confirmed:boolean;attempts:number};
  combat_profile?: import("./cavebotModel").CombatConfig;
  adaptive_cavebot?: {domains?:{movement:{status:string;reason:string|null};combat:{status:string;reason:string|null}}|null;navigation_hold?:string|null;preparing?:boolean;resumable?:boolean;scope?:string;waypoint?:import("./cavebotModel").WaypointProgress;mode?:"route"|"combat";status:string;reason:string;name?:string;index?:number;total?:number;loops?:number;loop?:boolean;position?:[number,number,number];logs?:string[];draft_id?:string;combat?:{spells?:import("./cavebotModel").SpellCheck[];last_spell_attempt?:{words:string;at:number;status:string}|null;lure?:import("./cavebotModel").LureProgress|null;enabled:boolean;phase:string;target?:{id:number;name:string;health:number}|null}};
  passive_map?: PassiveMapState;
  connected: boolean;
  automation_paused?: boolean;
  automation_pause_kind?: "manual"|"emergency"|null;
  potion_diagnostics?: PotionDiagnosticState;
  barrier_band?: {status:string;equipped_id:number|null;stock:number|null;attempts:number};
  actions: ActionInfo[];
  packets_from_server: number;
  packets_from_client: number;
  player: PlayerInfo;
  creatures: CreatureInfo[];
  dll_injected: boolean;
  dll_bridge_connected: boolean;
  cavebot?: CavebotState;
  proxy_sequence?: ProxySequence;
}

export interface PotionDiagnosticState {
  active: boolean;
  capacity?: number;
  remaining_seconds?: number;
  rows: { id?:number; item_id:number; mode:string; source?:string; time:string; gap_seconds:number|null;
    hp_delta:number; ki_delta:number; status?:string; response?:string|null; reported_count?:number|null;
    stock_delta?:number|null; stats_age_ms?:number|null }[];
}

async function control(path: string, body?: object): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(result.error || `HTTP ${res.status}`);
}

export const pauseAutomation = () => control("/api/automation/pause");
export const resumeAutomation = () => control("/api/automation/resume");
export const emergencyStop = () => control("/api/emergency-stop");
export const unlockAutomation = () => control("/api/automation/unlock");
export const setPotionDiagnostics = (enabled: boolean) => control("/api/potions/diagnostics", { enabled });
export interface MapAnnotationInput {
  scope:string; operation:"save" | "remove"; position:[number,number,number]; item_id:number;
  kind:string; required_level:number|null; activation_hint:string|null; note:string;
}
export const saveMapAnnotation = (data:MapAnnotationInput) => control("/api/map/annotation",data);
export const labelVisiblePet = (position:number[],ref:string,name:string|null) => control("/api/map/pet",{position,ref,name});

export async function fetchState(): Promise<BotState> {
  const res = await authenticatedFetch(`${API_BASE}/api/state`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export async function toggleAction(
  name: string,
  enabled: boolean
): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/actions/${name}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
}


export async function deleteAction(name: string): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/actions/${name}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Delete failed:", res.status, err);
  }
}

export async function updateActionConfig(
  name: string,
  config: Record<string, any>
): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/actions/${name}/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
}

// ── Cavebot API ─────────────────────────────────────────────────

export async function startRecording(name: string): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/cavebot/record/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Start recording failed:", res.status, err);
  }
}

export async function stopRecording(discard = false): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/cavebot/record/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ discard }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Stop recording failed:", res.status, err);
  }
}

export async function playRecording(
  name: string,
  loop = false
): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/cavebot/play`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, loop }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
}

export async function stopPlayback(): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/cavebot/play/stop`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Stop playback failed:", res.status, err);
  }
}

export async function clearCavebotLogs(): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/cavebot/logs/spacer`, {
    method: "POST",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Spacer failed:", res.status, err);
  }
}

export async function fetchRecording(
  name: string
): Promise<{ name: string; waypoints: WaypointInfo[] } | null> {
  const res = await authenticatedFetch(`${API_BASE}/api/recordings/${encodeURIComponent(name)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function deleteRecording(name: string): Promise<void> {
  const res = await authenticatedFetch(`${API_BASE}/api/recordings/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    console.error("Delete recording failed:", res.status, err);
  }
}

export async function removeWaypoints(
  name: string,
  indices: number[]
): Promise<{ waypoints: WaypointInfo[] } | null> {
  const res = await authenticatedFetch(
    `${API_BASE}/api/recordings/${encodeURIComponent(name)}/remove_waypoints`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ indices }),
    }
  );
  if (!res.ok) return null;
  return res.json();
}

export async function fetchActionsMap(
  name: string
): Promise<ActionsMapResponse | null> {
  const res = await authenticatedFetch(
    `${API_BASE}/api/cavebot/actions_map/${encodeURIComponent(name)}`
  );
  if (!res.ok) return null;
  return res.json();
}
