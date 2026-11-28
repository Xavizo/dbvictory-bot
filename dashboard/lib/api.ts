import { authenticatedFetch } from "./auth";

const API_PORT = 8089;
const API_BASE = `http://127.0.0.1:${API_PORT}`;

export interface MapNavigationFeature {
  kind: "web" | "stairs" | "ladder" | "descent" | "hole" | "covered_hole" | "rope_spot" | "door" | "level_gate" | "teleport" | "floor_change" | "passage";
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
  pet_owners?: (string | null)[];
  unidentified_monsters?: number;
  pet_candidate_name?: string;
  items: { id: number; flags: string[]; stack: number; lens_help?: number; minimap_color?: number }[];
  navigation?: MapNavigationFeature[];
  web_cut?: boolean;
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

export type TransitionAction = "clear_web" | "step" | "use" | "use_with" | "use_then_step" | "use_with_then_step" | "fly_up" | "unknown";
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

// Shapes of /api/state, as dashboard_api._build_state_json writes them.
export interface ActionInfo { // one entry of "actions"
  name: string; description: string;
  enabled: boolean; running: boolean; completed?: boolean;
  logs?: string[];                 // the action's latest bot.log lines
  config?: Record<string, any>;    // its settings, without "enabled"
}
export interface PlayerInfo { // "player": vitals and position
  hp: number; max_hp: number; mana: number; max_mana: number;
  level: number; experience: number; magic_level: number; soul: number;
  position: [x: number, y: number, z: number];
  stats_age?: number;              // seconds since the last stats update, -1 before any
}
export interface CreatureInfo { id: number; name: string; health: number; x: number; y: number; z: number }
export interface RecordingSummary {
  name: string; count: number; mode?: "manual" | "adaptive"; created_at: string;
  last_stats?: {xp_per_hour: number; kills_per_hour: number; senzu_per_hour: number; session_seconds: number; timestamp: string};
}
export interface CavebotState { // "cavebot": the route recorder and saved routes
  recording: { active: boolean;
    mode?: "manual" | "adaptive";
    hunt_progress?: {stage: 0 | 1 | 2 | 3 | 4 | null; areas?: number; total_areas?: number; revisited?: number; sectors?:number; missing_sectors?:number} | null;
    name: string; waypoint_count: number; waypoints: Record<string, any>[];   // the last 20 raw waypoints
    position_count?: number;
    observed_path?: {pos:[number,number,number];t:number;break_before?:boolean}[];
    trace_limited?: boolean;
  };
  recordings: RecordingSummary[];
}
type ProxyStep = "proxy_created" | "listening" | "client_connected" | "server_connected" | "xtea_captured" | "logged_in" | "packets_flowing";
/** How far the game proxy got; timestamps are seconds after the proxy was created. */
export type ProxySequence = Record<ProxyStep, boolean> & {timestamps?: Record<string, number | null>};

export type AlertKind = "private" | "default" | "player" | "ki_loss";
export type AlertConfig = Record<AlertKind, {enabled:boolean;sound:boolean;scope:"always"|"cavebot"}>;
export interface AlertState {
  paused?:boolean;automation_paused?:boolean;silenced_through?:number;
  session:string;config:AlertConfig;latest_id:number;unread:number;cavebot_active:boolean;map_fresh:boolean;
  chat_received:number;last_chat_at:number|null;
  vitals_alarm_id?:number|null;
  ki_alarm_id?:number|null;
  events:{id:number;kind:AlertKind|"vitals";at:number;sender:string;text:string;sound:boolean;position?:number[]}[];
}
export const configureAlerts=(config:AlertConfig)=>control("/api/alerts",{command:"configure",config});
export const acknowledgeAlerts=(through:number)=>control("/api/alerts",{command:"acknowledge",through});
export const setAlertsPaused=(paused:boolean)=>control("/api/alerts",{command:"set_paused",paused});

export type LiveMovementPreview = {path:[number,number,number][];stop:[number,number,number]|null};

export interface BotState { // the whole /api/state answer
  language?: "pl" | "en"; // the launcher's language switch (dbv/language.py)
  commands?: {session:number} | null;
  login_startup?: {phase:"waiting_for_login"|"waiting_for_session"|"activated"|"failed"} | null;
  observation_families?: {
    session_id:number; revision:number;
    families:Record<string,{source:string|null;reason:string|null;state:"fresh"|"stale"|"waiting"|"gap"|"observed";age_ms:number|null;revision:number}>;
  };
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
  barrier_spell?:{status:string}|null;
  speed_up?:{status:string;remaining:number|null;offset:number|null;effect_active:boolean|null;confirmed:boolean;attempts:number};
  combat_profile?: import("./cavebotModel").CombatConfig;
  /** Walka (actions/auto_combat.py): fighting, yielding to the cavebot, waiting or off. */
  standalone_combat?: {status:"fighting"|"yielding"|"waiting"|"off";reason:string;combat?:{spells?:import("./cavebotModel").SpellCheck[];target?:{id:number;name:string;health:number}|null;held?:{id:number;name:string;lost:boolean}|null}|null};
  adaptive_cavebot?: {navigation_wait?:{reason:string;observations:string[];review_in_ms:number}|null;map_preview?: LiveMovementPreview | null;domains?:{movement:{status:string;reason:string|null};combat:{status:string;reason:string|null}}|null;navigation_hold?:string|null;preparing?:boolean;resumable?:boolean;scope?:string;waypoint?:import("./cavebotModel").WaypointProgress;mode?:"route"|"combat";status:string;reason:string;name?:string;index?:number;total?:number;loops?:number;loop?:boolean;position?:[number,number,number];logs?:string[];draft_id?:string;combat?:{spells?:import("./cavebotModel").SpellCheck[];last_spell_attempt?:{words:string;at:number;status:string}|null;lure?:import("./cavebotModel").LureProgress|null;enabled:boolean;phase:string;target?:{id:number;name:string;health:number}|null}};
  passive_map?: PassiveMapState;
  connected: boolean;               // logged in through the game proxy
  session_kills?: number;
  automation_paused?: boolean;
  automation_pause_kind?: "manual"|"emergency"|null;
  potion_diagnostics?: PotionDiagnosticState;
  item_identification?: ItemIdentificationState | null;
  hunter_knife?: {last_issue?:{position:[number,number,number];reason:string;action:"skin"|"uncover";spell?:string;since_spell_ms?:number}|null;unassigned_bodies?:number;status:string;processed:number;unattributed?:number;from_combat?:number;last_timing_ms?:{recognition:number;wait_for_use:number;delivery_wait?:number;confirmation:number}|null;personal_loot:number;unclassified_loot:number;denied:number;corpses?:KnifeCorpse[];default_corpses?:KnifeCorpse[]};
  barrier_band?: {status:string;equipped_id:number|null;stock:number|null;attempts:number};
  actions: ActionInfo[];             // every script in actions/, internal ones excluded
  packets_from_server: number; packets_from_client: number;
  player: PlayerInfo; creatures: CreatureInfo[];
  dll_injected: boolean;            // the observer pipe is connected
  dll_bridge_connected: boolean;    // ... and its reader action is running
  cavebot?: CavebotState; proxy_sequence?: ProxySequence;
}

export interface IdentifiedItem {item_id:number;where:"map"|"inventory";position:[number,number,number]|null;time:number}
export interface ItemIdentificationState {enabled:boolean;recent:IdentifiedItem[]}
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
export type MovementHistoryStatus = {active:boolean;capturing:boolean;available:boolean;latest:string|null;error:string|null;unwritten_reports:number};
export async function movementHistory(save=false,signal?:AbortSignal):Promise<MovementHistoryStatus>{
  const response=await authenticatedFetch(API_BASE+"/api/movement-history",{method:save?"POST":"GET",signal});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
export const setPotionDiagnostics = (enabled: boolean) => control("/api/potions/diagnostics", { enabled });
export const setItemIdentification = (enabled: boolean) => control("/api/diagnostics/item-identification", { enabled });
export interface MapAnnotationInput {
  scope:string; operation:"save" | "remove"; position:[number,number,number]; item_id:number;
  kind:string; required_level:number|null; activation_hint:string|null; note:string;
}
export const saveMapAnnotation = (data:MapAnnotationInput) => control("/api/map/annotation",data);
export const labelVisiblePet = (position:number[],ref:string,name:string|null) => control("/api/map/pet",{position,ref,name});

export type KnifeCorpse = {id:number;skinned:number;monster?:string};

function okOrThrow(response: Response): Response {
  if (response.ok) return response;
  throw new Error(`HTTP ${response.status}`);
}

export async function fetchState(signal?: AbortSignal): Promise<BotState> {
  return okOrThrow(await authenticatedFetch(`${API_BASE}/api/state`, {signal})).json();
}

export async function fetchCavebotReports<T>(query:string,signal:AbortSignal):Promise<T>{
  const response=await authenticatedFetch(API_BASE+"/api/cavebot/reports"+query,{signal});
  if(!response.ok)throw Error(String(response.status));
  return response.json();
}

const actionPath = (name: string, verb = "") => `/api/actions/${encodeURIComponent(name)}${verb && "/" + verb}`;

/** Turn an action on or off; rejects with the server's reason. */
export const toggleAction = (name: string, enabled: boolean) => control(actionPath(name, "toggle"), {enabled});
/** Merge settings into an action's config (not "enabled"); rejects with the server's reason. */
export const updateActionConfig = (name: string, config: Record<string, any>) => control(actionPath(name, "config"), config);

/** Stop and delete an action's script. A failure is only logged; the next state shows what happened. */
export async function deleteAction(name: string) {
  const response = await authenticatedFetch(API_BASE + actionPath(name), {method: "DELETE"});
  if (!response.ok) console.error("Delete failed:", response.status, await response.json().catch(() => ({})));
}
