// The phone preview's stand-in for the bot (see mock/README.md). It replaces
// public/desktop-bridge.js in the mock build: it defines window.dbvAuth & co., answers the
// panel's requests to http://127.0.0.1:8089 and plays the WebSocket on :8090 with
// invented game data. It never contacts a game, a bot or any other host.
(() => {
  "use strict";
  const W = window.__dbvMockWorld;
  const store = {
    memory: (window.__dbvMockMemory ??= {}), // shared by the mock scripts, used when the viewer's browser keeps no storage
    get(key, fallback) { try { const v = localStorage.getItem("dbv.mock." + key); return v === null ? fallback : JSON.parse(v); } catch { return key in this.memory ? this.memory[key] : fallback; } },
    set(key, value) { this.memory[key] = value; try { localStorage.setItem("dbv.mock." + key, JSON.stringify(value)); } catch { /* storage off */ } },
  };
  const now = () => Date.now() / 1000;
  const levelXp = n => Math.floor(50 * (n ** 3 - 6 * n ** 2 + 17 * n - 12) / 3);

  // ---- Fake bot state ---------------------------------------------------------------
  const MONSTERS = [
    { name: "Mutant Wolf", max: 1800, xp: 950 }, { name: "Fire Elemental", max: 2600, xp: 1400 },
    { name: "Roco", max: 1200, xp: 700 }, { name: "Gunso", max: 3200, xp: 1900 },
  ];
  const sim = {
    session: 1, tick: 0, step: 0, level: 87, experience: levelXp(87) + 420000,
    hp: 9200, maxHp: 11000, ki: 6100, maxKi: 7400, kills: 0, senzu: 0, packetsIn: 18234, packetsOut: 4210,
    paused: false, pauseKind: null, cavebot: "idle", target: null, nextId: 0x40000100,
    creatures: [], knife: { processed: 0, personal_loot: 0, unclassified_loot: 0, denied: 0 },
    alerts: { latest: 2, read: 0, paused: false, events: [] }, recording: false,
  };
  const corpses = [
    { id: 6080, skinned: 6081, monster: "Mutant Wolf" }, { id: 6324, skinned: 6325, monster: "Fire Elemental" },
    { id: 6512, skinned: 6513, monster: "Roco" }, { id: 6514, skinned: 6515, monster: "Gunso" },
  ];
  const spell = (words, extra) => ({ words, trigger: "distance", cooldown: 2, ki: 10, range: 5, count: 1, aim: "target", ...extra });
  const combat = store.get("combat", {
    enabled: true, mode: "chase", strategy: "nearest", engage: "near", radius: 6, distance: 1,
    lure_count: 4, lure_seconds: 8, lure_distance: 3, lure_origin_limit: 12,
    rules: [{ pattern: "Mutant Wolf", action: "attack", priority: 80, stance: "inherit", spell_mode: "inherit" },
      { pattern: "Fire Elemental", action: "attack", priority: 60, stance: "distance", distance: 3, spell_mode: "inherit" },
      { pattern: "*", action: "attack", priority: 10, stance: "inherit", spell_mode: "inherit" }],
    spells: [spell("darkness blaster"), spell("energy grenade", { trigger: "visible", aim: "self", count: 2, range: 1 })],
  });
  const actions = store.get("actions", [
    { name: "adaptive_cavebot", description: "Cavebot — explicit atlas navigation, loop and local detours.", enabled: true, config: {} },
    { name: "auto_potions", description: "Priority HP/Ki healing rules: Special Health Potion, Special Ki Potion and Senzu.", enabled: true,
      config: { shared_cooldown: 1, defer_senzu_ki: true, rules: [
        { id: "hp", enabled: true, resource: "hp", item_id: 10161, threshold: 60, jitter: 5, cooldown: 1, interval: 1, mode: "use" },
        { id: "ki", enabled: true, resource: "ki", item_id: 10160, threshold: 40, jitter: 5, cooldown: 1, interval: 1, mode: "use" },
        { id: "senzu", enabled: true, resource: "hp", item_id: 8465, threshold: 25, jitter: 0, cooldown: null, interval: 1, mode: "self" }] } },
    { name: "auto_hunter_knife", description: "Hunter Knife 5908 · świeże ciała z listy w panelu.", enabled: true, config: { corpses } },
    { name: "auto_barrier_band", description: "Barrier Band 3051: uzupełnia pusty slot; aktywny band ma ID 3088.", enabled: false, config: { slot: "ring" } },
    { name: "barrier_spell", description: "Utrzymuje barierę postaci za pomocą czaru Barrier.", enabled: false, config: {} },
    { name: "speed_up", description: "Speed Up: recasts haste at a random moment before the effect ends.", enabled: true, config: {} },
    { name: "eat_food", description: "Food: eat a red ham every 10 seconds.", enabled: true, config: {} },
    { name: "auto_combat", description: "Walka: fight without a route, with the cavebot's combat settings of its own.", enabled: true,
      config: { combat: { ...combat, enabled: true, mode: "stop", engage: "visible", hold_target: true,
        rules: [{ pattern: "Mutant Wolf", action: "attack", priority: 80, stance: "inherit", spell_mode: "inherit" },
          { pattern: "Roco", action: "attack", priority: 50, stance: "inherit", spell_mode: "inherit" }],
        spells: [spell("darkness blaster", { trigger: "visible", range: 8 })] } } },
    { name: "packet_sniffer", description: "Packet diagnostics: log every packet the client sends.", enabled: false, config: {} },
    { name: "dll_bridge", description: "Internal action: attach the observer DLL and feed its data into the game state.", enabled: true, config: {} },
  ]);
  const saveActions = () => store.set("actions", actions);
  let drafts = store.get("drafts", null) || [{
    id: "mock-route-1", name: "Wolf valley loop", tags: [{ text: "demo", color: "mint" }], points: W.ROUTE.map(p => [...p]),
    labels: W.ROUTE.map(() => ""), links: [], loop: true, updated: 1764300000, combat, navigation_revision: "nav-1",
  }];
  let trash = store.get("trash", []);
  const saveDrafts = () => { store.set("drafts", drafts); store.set("trash", trash); };
  const profiles = store.get("profiles", [
    { id: "p-combat-1", name: "Wolves (chase)", kind: "combat", config: combat },
    { id: "p-bot-1", name: "Hunting set", kind: "bot", config: {} },
  ]);
  let selection = { combat: "p-combat-1", bot: "p-bot-1", routes: {} };

  const scenario = () => store.get("scenario", "hunting");
  const language = () => store.get("language", "pl");
  const inGame = () => ["hunting", "idle", "low-hp"].includes(scenario());

  function spawn() {
    const kind = MONSTERS[Math.floor(Math.random() * MONSTERS.length)];
    const [px, py] = W.PATH[sim.step];
    for (let i = 0; i < 20; i++) {
      const x = px + Math.round(Math.random() * 16 - 8), y = py + Math.round(Math.random() * 12 - 6);
      if (Math.max(Math.abs(x - px), Math.abs(y - py)) >= 5 && W.tile(x, y, 7).kind === "clear") return { id: sim.nextId++, name: kind.name, kind, hp: kind.max, x, y, z: 7 };
    }
    return null;
  }
  function advance() {
    sim.tick++;
    if (!inGame()) return;
    const hunting = sim.cavebot === "running" && !sim.paused;
    if (scenario() === "low-hp") sim.hp = Math.max(900, Math.round(sim.maxHp * 0.18));
    while (sim.creatures.length < (hunting ? 5 : 3)) { const c = spawn(); if (!c) break; sim.creatures.push(c); }
    const [px, py] = W.PATH[sim.step];
    // Monsters wander; the targeted one closes in. Ones left far behind despawn.
    sim.creatures = sim.creatures.filter(c => Math.max(Math.abs(c.x - px), Math.abs(c.y - py)) <= 10);
    for (const c of sim.creatures) {
      const chase = c === sim.target && Math.max(Math.abs(c.x - px), Math.abs(c.y - py)) > 1;
      if (!chase && Math.random() > 0.3) continue;
      const x = chase ? c.x + Math.sign(px - c.x) : c.x + Math.round(Math.random() * 2 - 1);
      const y = chase ? c.y + Math.sign(py - c.y) : c.y + Math.round(Math.random() * 2 - 1);
      if (W.tile(x, y, 7).kind === "clear" && (x !== px || y !== py)) { c.x = x; c.y = y; }
    }
    const near = sim.creatures.filter(c => Math.max(Math.abs(c.x - px), Math.abs(c.y - py)) <= 3);
    if (hunting) {
      sim.target = sim.target && sim.creatures.includes(sim.target) ? sim.target : near[0] ?? null;
      const reach = sim.target && Math.max(Math.abs(sim.target.x - px), Math.abs(sim.target.y - py)) <= 1;
      if (sim.target && !reach) { /* waiting for it to come close */ }
      else if (sim.target) {
        sim.target.hp -= Math.round(sim.target.kind.max * (0.12 + Math.random() * 0.1));
        sim.hp = Math.max(1500, sim.hp - Math.round(Math.random() * 260));
        sim.ki = Math.max(400, sim.ki - 90);
        if (sim.target.hp <= 0) {
          sim.creatures = sim.creatures.filter(c => c !== sim.target);
          sim.kills++; sim.experience += sim.target.kind.xp;
          sim.knife.processed++; if (Math.random() < 0.6) sim.knife.personal_loot++; else sim.knife.unclassified_loot++;
          if (sim.experience >= levelXp(sim.level + 1)) sim.level++;
          sim.target = null;
        }
      } else sim.step = (sim.step + 1) % W.PATH.length;
      if (sim.hp < sim.maxHp * 0.6 && Math.random() < 0.3) { sim.hp = Math.min(sim.maxHp, sim.hp + 2600); }
      if (sim.ki < sim.maxKi * 0.4 && Math.random() < 0.3) { sim.ki = Math.min(sim.maxKi, sim.ki + 2000); }
      if (sim.hp < sim.maxHp * 0.3) { sim.hp = sim.maxHp; sim.senzu++; }
    } else {
      sim.target = null;
      if (scenario() !== "low-hp") { sim.hp = Math.min(sim.maxHp, sim.hp + 60); sim.ki = Math.min(sim.maxKi, sim.ki + 40); }
    }
    sim.packetsIn += 14 + Math.floor(Math.random() * 9); sim.packetsOut += 3;
  }

  function passiveMap(center) {
    const [cx, cy, cz] = center, width = 19, height = 15, ox = cx - 9, oy = cy - 7;
    const occupants = new Map();
    const mark = (x, y, type) => { const k = x + "," + y; const o = occupants.get(k) ?? { count: 0, types: {} }; o.count++; o.types[type] = (o.types[type] ?? 0) + 1; occupants.set(k, o); };
    mark(cx, cy, "self");
    for (const c of sim.creatures) if (c.z === cz) mark(c.x, c.y, "monster");
    const cells = [];
    for (let y = oy; y < oy + height; y++) for (let x = ox; x < ox + width; x++) cells.push(W.cell(x, y, cz, occupants.get(x + "," + y)));
    return { status: "ready", passive: true, stale: false, cells, center, origin: [ox, oy], width, height, read_ms: 4,
      age_seconds: 0.2, captured_at: now(), catalogue_items: 4120,
      learning: { status: "learning", total: 1, observed: 1, passive: true, records: [{
        id: "t1", exit: [1061, 1031, 8], action: "step", direction: "down", count: 3, origin: [1061, 1030, 7], entry: [1061, 1031, 7],
        item_id: 1385, tool_id: null, entry_items: [1385], evidence: "observed", reason: "stairs", first_seen: now() - 3600, last_seen: now() - 120, variable_exit: false }] },
      annotation_scope: W.SCOPE, annotation_editable: true };
  }

  function actionRows() {
    const playing = inGame();
    return actions.map(a => ({ ...a, running: playing && a.enabled && (a.name !== "adaptive_cavebot" || sim.cavebot === "running"),
      logs: a.enabled && playing ? [`[${new Date().toLocaleTimeString()}] ${a.name}: ok`] : [] }));
  }

  function snapshot() {
    const playing = inGame(), position = [...W.PATH[sim.step]];
    const families = Object.fromEntries(["health", "position", "map", "creatures", "target", "messages", "loot", "inventory", "effects"].map(name =>
      [name, { source: "dll", reason: null, state: playing ? "fresh" : "waiting", age_ms: playing ? 80 + (sim.tick * 37) % 300 : null, revision: sim.tick }]));
    const state = {
      language: language(), connected: playing, commands: { session: sim.session },
      login_startup: { phase: playing ? "activated" : "waiting_for_login" },
      player: { hp: sim.hp, max_hp: sim.maxHp, mana: sim.ki, max_mana: sim.maxKi, level: sim.level, experience: sim.experience,
        magic_level: 42, soul: 100, position, stats_age: playing ? 0.3 : -1 },
      creatures: playing ? sim.creatures.map(c => ({ id: c.id, name: c.name, health: Math.max(1, Math.round(c.hp / c.kind.max * 100)), x: c.x, y: c.y, z: c.z })) : [],
      actions: actionRows(), packets_from_server: sim.packetsIn, packets_from_client: sim.packetsOut,
      dll_injected: playing, dll_bridge_connected: playing,
      automation_paused: sim.paused, automation_pause_kind: sim.pauseKind, session_kills: sim.kills,
      senzu_usage: playing ? { total: sim.senzu, per_hour: sim.senzu ? sim.senzu * 6 : null, window_seconds: 600, ended: false } : null,
      observation_families: { session_id: sim.session, revision: sim.tick, families },
      proxy_sequence: { proxy_created: true, listening: true, client_connected: playing, server_connected: playing, xtea_captured: playing, logged_in: playing, packets_flowing: playing,
        timestamps: { proxy_created: 0, listening: 0.1, client_connected: playing ? 2.4 : null, logged_in: playing ? 6.8 : null } },
      packet_observer: { status: "active", pending: 0, pending_bytes: 0, processing: false, capacity: 4096, accepted: sim.packetsIn, applied: sim.packetsIn, dropped: 0, failures: 0,
        lag_ms: 3, max_lag_ms: 41, last_problem: null, last_applied_age_seconds: 0.1 },
      profile_selection: selection, combat_profile: combat,
      hunter_knife: { status: actions.find(a => a.name === "auto_hunter_knife")?.enabled ? "active" : "off", ...sim.knife, unattributed: 0, from_combat: sim.knife.processed,
        unassigned_bodies: 0, last_timing_ms: { recognition: 18, wait_for_use: 120, confirmation: 240 }, last_issue: null,
        corpses: actions.find(a => a.name === "auto_hunter_knife")?.config.corpses ?? corpses, default_corpses: corpses },
      speed_up: { status: playing ? "active" : "waiting", remaining: playing ? 21 : null, offset: 4, effect_active: playing, confirmed: true, attempts: 1 },
      barrier_spell: { status: "off" }, barrier_band: { status: "off", equipped_id: null, stock: 3, attempts: 0 },
      potion_diagnostics: { active: false, rows: [] }, item_identification: { enabled: false, recent: [] },
      cavebot: { recording: { active: sim.recording, name: sim.recording ? "mock-recording" : "", waypoint_count: 0, waypoints: [] }, recordings: [] },
      alerts: { paused: sim.alerts.paused, automation_paused: sim.paused, session: "mock", config: alertConfig, latest_id: sim.alerts.latest,
        unread: Math.max(0, sim.alerts.latest - sim.alerts.read), cavebot_active: sim.cavebot === "running", map_fresh: playing, chat_received: 12, last_chat_at: now() - 90,
        vitals_alarm_id: scenario() === "low-hp" ? 3 : null, events: alertEvents() },
      passive_map: playing ? passiveMap(position) : { status: "waiting", passive: true, stale: true, cells: [] },
    };
    const standalone = actions.find(a => a.name === "auto_combat");
    state.standalone_combat = !standalone?.enabled || !playing ? { status: "off", reason: "" }
      : sim.cavebot === "running" ? { status: "yielding", reason: "Cavebot ma pierwszeństwo.", combat: null }
      : { status: "fighting", reason: "", combat: { target: null, spells: [],
          held: sim.target ? null : { id: 0x40000077, name: "Mutant Wolf", lost: true } } };
    const route = drafts[0];
    state.adaptive_cavebot = playing && sim.cavebot !== "idle" ? {
      status: sim.cavebot, reason: sim.cavebot === "paused" ? "paused" : "", name: route?.name, draft_id: route?.id, scope: W.SCOPE, mode: "route", loop: true, loops: Math.floor(sim.tick / 400),
      index: nearestWaypoint(position), total: route?.points.length ?? 0, position, resumable: sim.cavebot === "paused",
      waypoint: { navigation_revision: route?.navigation_revision ?? null, current: nearestWaypoint(position), last_reached: Math.max(0, nearestWaypoint(position) - 1), total: route?.points.length ?? 0, draft_updated: route?.updated ?? null, tolerated: 0 },
      domains: { movement: { status: sim.target ? "holding" : "walking", reason: null }, combat: { status: sim.target ? "fighting" : "searching", reason: null } },
      combat: { enabled: true, phase: sim.target ? "fight" : "search", target: sim.target ? { id: sim.target.id, name: sim.target.name, health: Math.round(sim.target.hp / sim.target.kind.max * 100) } : null,
        spells: combat.spells.map((s, i) => ({ words: s.words, aim: s.aim ?? "target", matched_count: 1, ready: i === sim.tick % 2, conditions_met: true, remaining: i === sim.tick % 2 ? 0 : 1.2, reason: "", selected: i === 0 })) },
      logs: ["Mock: walking the loop", sim.target ? "Mock: attacking " + sim.target.name : "Mock: looking for monsters"],
    } : playing ? { status: "idle", reason: "" } : undefined;
    return state;
  }
  function nearestWaypoint([x, y]) {
    const points = drafts[0]?.points ?? [];
    let best = 0, distance = Infinity;
    points.forEach((p, i) => { const d = Math.hypot(p[0] - x, p[1] - y); if (d < distance) { distance = d; best = i; } });
    return best;
  }
  const alertConfig = { private: { enabled: true, sound: true, scope: "always" }, default: { enabled: true, sound: false, scope: "cavebot" },
    player: { enabled: true, sound: true, scope: "cavebot" }, ki_loss: { enabled: false, sound: false, scope: "always" } };
  function alertEvents() {
    const t = now();
    return [{ id: 2, kind: "private", at: t - 300, sender: "Vegetto", text: "hej, idziesz na bossa?", sound: true },
      { id: 1, kind: "player", at: t - 900, sender: "Krillin", text: "Player on screen", sound: true, position: [1080, 1012, 7] }];
  }

  // ---- HTTP answers -----------------------------------------------------------------
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const ok = (message = "ok") => json({ ok: true, message });
  const draftList = extra => ({ scope: W.SCOPE, drafts, trash, ...extra });
  function atlas(x, y, z, radius) {
    const cells = [];
    const occupied = new Map(inGame() ? sim.creatures.map(c => [c.x + "," + c.y + "," + c.z, { count: 1, types: { monster: 1 } }]) : []);
    if (inGame()) { const [px, py, pz] = W.PATH[sim.step]; occupied.set(px + "," + py + "," + pz, { count: 1, types: { self: 1 } }); }
    for (let cy = y - radius; cy <= y + radius; cy++) for (let cx = x - radius; cx <= x + radius; cx++) {
      if (W.tile(cx, cy, z).kind === "no_ground") continue;
      cells.push({ ...W.cell(cx, cy, z, occupied.get(cx + "," + cy + "," + z)), seen_at: now() - 60 });
    }
    return { scope: W.SCOPE, center: [x, y, z], radius, cells, floors: W.FLOORS, total: cells.length, limit: null,
      route_transitions: [{ id: "t1", origin: [1061, 1030, 7], entry: [1061, 1031, 7], exit: [1061, 1031, 8], action: "step", item_id: 1385, count: 3 }] };
  }
  function plan(body) {
    const points = body.points ?? [];
    const path = [];
    for (let i = 0; i < points.length; i++) {
      const [ax, ay, z] = points[i], next = points[i + 1] ?? (body.loop ? points[0] : null);
      path.push([ax, ay, z]);
      if (!next || next[2] !== z) continue;
      let x = ax, y = ay;
      while (x !== next[0] || y !== next[1]) { x += Math.sign(next[0] - x); y += Math.sign(next[1] - y); if (x !== next[0] || y !== next[1]) path.push([x, y, z]); }
    }
    return { scope: W.SCOPE, path, points, original: points, complete: true, preview_only: false, warning: "", loop_closed: !!body.loop,
      links: body.links ?? [], transitions: [], legs: [], gaps: [] };
  }
  const zones = () => ({ scope: W.SCOPE, revision: "z1", side: 8,
    cells: [[1072, 1008, 7], [1080, 1008, 7], [1112, 1016, 7], [1120, 1048, 7], [1088, 1064, 7], [1040, 1064, 7], [1008, 1040, 7]],
    habitats: [{ position: [1080, 1008, 7], species: [["Mutant Wolf", 6], ["Roco", 2]] }, { position: [1120, 1048, 7], species: [["Fire Elemental", 4]] }],
    spawns: [{ point: [1083, 1011, 7], name: "Mutant Wolf", count: 6 }, { point: [1124, 1051, 7], name: "Fire Elemental", count: 4 }] });
  const reports = () => ({ active: sim.cavebot === "running" ? { route: drafts[0]?.name ?? "", lap: 3, events: [
    { ms: 4000, kind: "travel", position: [1060, 1030, 7], waypoint: 0, engaged: null, goal: null, target: null, detail: null },
    { ms: 21000, kind: "fight", position: [1080, 1012, 7], waypoint: 1, engaged: 2, goal: 4, target: null, detail: null }] } : null,
    reports: [1, 2].map(lap => ({ id: "r" + lap, route: drafts[0]?.name ?? "", revision: "nav-1", profile: "Wolves (chase)", started_at: now() - lap * 700, lap,
      complete: true, duration_ms: 612000, blocked_ms: 9000, unobserved_ms: 0, xp: 182000, xp_hour: 1070000, senzu: 1, senzu_million: 0.9, group_median: 3,
      observed_deaths: 21, event_count: 54, end_reason: "complete" })), more: false });
  const spells = { spells: [
    ["Darkness Blaster", "darkness blaster", "Distanse", 2000, 40, 120], ["Energy Grenade", "energy grenade", "Area", 4000, 60, 200],
    ["Kamehameha", "kamehameha", "Wave", 6000, 80, 400], ["Speed Up", "speed up", "speed", 30000, 10, 50], ["Barrier", "barrier", "barrier", 60000, 30, 100],
    ["Senzu Creation", "create senzu", "createsenzu", 120000, 50, 300]].map(([name, words, category, cd, level, ki], i) =>
    ({ id: 100 + i, name, words, description: name + " (mock)", category, icon: null, cooldown_client_ms: cd, level, ki, ki_percent: 0, aggressive: !["speed", "barrier", "createsenzu"].includes(category), unavailable: null })) };

  async function answer(url, init) {
    const method = (init?.method ?? "GET").toUpperCase();
    let body = {};
    try { body = init?.body ? JSON.parse(init.body) : {}; } catch { body = {}; }
    const path = url.pathname, q = url.searchParams;
    if (scenario() === "offline") return json({ error: "Mock: the bot is offline in this scenario" }, 503);
    if (path === "/api/state") return json(snapshot());
    let m = path.match(/^\/api\/actions\/([^/]+)(?:\/(toggle|config))?$/);
    if (m) {
      const action = actions.find(a => a.name === decodeURIComponent(m[1]));
      if (!action) return json({ error: "Unknown action" }, 404);
      if (method === "DELETE") { actions.splice(actions.indexOf(action), 1); saveActions(); return ok(); }
      if (m[2] === "toggle") action.enabled = !!body.enabled; else if (m[2] === "config") action.config = { ...action.config, ...body };
      saveActions(); return ok();
    }
    switch (path) {
      case "/api/automation/pause": sim.paused = true; sim.pauseKind = "manual"; return ok();
      case "/api/automation/resume": sim.paused = false; sim.pauseKind = null; return ok();
      case "/api/emergency-stop": sim.paused = true; sim.pauseKind = "emergency"; sim.cavebot = sim.cavebot === "running" ? "paused" : sim.cavebot; return ok();
      case "/api/automation/unlock": sim.paused = false; sim.pauseKind = null; return ok();
      case "/api/adaptive/control": {
        const c = body.command;
        if (c === "start" || c === "start_combat" || c === "resume") sim.cavebot = "running";
        else if (c === "pause") sim.cavebot = "paused";
        else if (c === "stop") sim.cavebot = "idle";
        else if (c === "configure_combat") { Object.assign(combat, body.combat); store.set("combat", combat); }
        return ok("Mock: " + c);
      }
      case "/api/adaptive/preview": {
        const [px, py] = W.PATH[sim.step];
        const targets = (inGame() ? sim.creatures : []).map(c => ({ id: c.id, name: c.name, health: Math.round(c.hp / c.kind.max * 100), priority: 50, distance: Math.max(Math.abs(c.x - px), Math.abs(c.y - py)) }));
        return json({ message: targets.length ? "Mock: monsters on screen" : "Mock: no monsters on screen", targets, selected: targets[0] ? { id: targets[0].id } : null });
      }
      case "/api/map/drafts":
        if (method === "GET") return json(draftList());
        if (body.operation === "delete") { const d = drafts.find(r => r.id === body.id); drafts = drafts.filter(r => r !== d); if (d) trash.push(d); }
        else if (body.operation === "restore") { const d = trash.find(r => r.id === body.id); trash = trash.filter(r => r !== d); if (d) drafts.push(d); }
        else if (body.operation === "set_loop") { const d = drafts.find(r => r.id === body.id); if (d) d.loop = !!body.loop; }
        else if (body.operation === "set_tags") { const d = drafts.find(r => r.id === body.id); if (d) d.tags = body.tags; }
        else if (body.operation === "rename") { const d = drafts.find(r => r.id === body.id); if (d) d.name = body.name; }
        else if (!body.operation && body.points) {
          let d = drafts.find(r => r.id === body.id);
          if (!d) { d = { id: "mock-route-" + Date.now(), tags: body.tags ?? [], combat, navigation_revision: "nav-" + Date.now() }; drafts.push(d); }
          Object.assign(d, { name: body.name, points: body.points, labels: body.labels ?? [], links: body.links ?? [], loop: body.loop ?? true, updated: Math.floor(now()) });
          saveDrafts(); return json(draftList({ saved_id: d.id }));
        }
        saveDrafts(); return json(draftList());
      case "/api/map/drafts/export": { const d = drafts.find(r => r.id === q.get("id")); return json({ file_name: (d?.name ?? "route") + ".json", pack: { complete: true, draft: d } }); }
      case "/api/map/drafts/import": return json(draftList());
      case "/api/map/plan": return json(plan(body));
      case "/api/map/atlas": return json(atlas(+q.get("x"), +q.get("y"), +q.get("z"), Math.min(32, +q.get("radius") || 12)));
      case "/api/map/world": { const since = q.get("since"), z = +q.get("z"); return json(since === "w-" + z ? { revision: since } : { revision: "w-" + z, sectors: W.sectors(z) }); }
      case "/api/map/zones": return json(zones());
      case "/api/map/grounds": return json({ scope: W.SCOPE, sector_size: 8, areas: [], drafts: [], discoveries: [] });
      case "/api/map/discoveries": return json({ scope: W.SCOPE, revision: "d1", discoveries: [] });
      case "/api/cavebot/reports": return json(reports());
      case "/api/cavebot/record/start": sim.recording = true; return ok();
      case "/api/cavebot/record/stop": sim.recording = false; return ok();
      case "/api/movement-history": return json({ active: inGame(), capturing: false, available: true, latest: null, error: null, unwritten_reports: 0 });
      case "/api/profiles": {
        if (method === "GET") return json({ profiles });
        if (body.command === "apply") { const p = profiles.find(r => r.id === body.id); if (!p) return json({ error: "Unknown profile" }, 404); selection = { ...selection, [p.kind]: p.id }; return json({ message: p }); }
        if (body.command === "save") { let p = profiles.find(r => r.id === body.id); if (!p) { p = { id: "p-" + Date.now(), kind: body.kind }; profiles.push(p); } Object.assign(p, { name: body.name, config: body.config }); store.set("profiles", profiles); return json({ message: p }); }
        if (body.command === "delete") { profiles.splice(profiles.findIndex(r => r.id === body.id), 1); store.set("profiles", profiles); return ok(); }
        if (body.command === "bind") { selection = { ...selection, routes: { ...selection.routes, [body.scope + ":" + body.draft_id]: body.id } }; return ok(); }
        return ok();
      }
      case "/api/alerts":
        if (body.command === "acknowledge") sim.alerts.read = body.through;
        else if (body.command === "set_paused") sim.alerts.paused = !!body.paused;
        else if (body.command === "configure") Object.assign(alertConfig, body.config);
        return ok();
      case "/api/potions/diagnostics": case "/api/diagnostics/item-identification": case "/api/map/annotation": case "/api/map/pet": return ok();
    }
    console.warn("[mock] no fake answer for", method, path);
    return json({ error: "Mock: no fake answer for " + path }, 404);
  }

  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.href);
    if (url.host === "127.0.0.1:8089") { await new Promise(r => setTimeout(r, 60)); return answer(url, init); }
    if (url.pathname.endsWith("/local-spells/catalog.json")) return json(spells);
    return realFetch(input, init);
  };

  // ---- WebSocket on :8090 -----------------------------------------------------------
  const RealSocket = window.WebSocket;
  class MockSocket extends EventTarget {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    constructor(url) {
      super();
      if (!String(url).includes("127.0.0.1:8090")) return new RealSocket(url);
      this.url = url; this.readyState = 0; this.onopen = this.onmessage = this.onclose = this.onerror = null;
      setTimeout(() => {
        if (scenario() === "offline") { this.readyState = 3; this.onclose?.({}); return; }
        this.readyState = 1; this.onopen?.({});
      }, 150);
    }
    send() {
      if (this.timer) return;
      const push = () => {
        if (this.readyState !== 1) return;
        if (scenario() === "offline") { this.close(); return; }
        this.onmessage?.({ data: JSON.stringify(snapshot()) });
      };
      push();
      this.timer = setInterval(push, 500);
    }
    close() { clearInterval(this.timer); if (this.readyState === 3) return; this.readyState = 3; setTimeout(() => this.onclose?.({}), 0); }
  }
  window.WebSocket = MockSocket;
  setInterval(advance, 500);
  if (scenario() === "hunting") sim.cavebot = "running";
  window.addEventListener("dbv-mock-scenario", () => { sim.cavebot = scenario() === "hunting" ? "running" : "idle"; sim.paused = false; sim.pauseKind = null; });

  // ---- The desktop window's operations (public/desktop-bridge.js in the real panel) ----
  const say = text => window.__dbvMockToast?.(text);
  const fixed = value => Object.freeze(value);
  Object.defineProperties(window, {
    dbvAuth: { value: fixed({ getToken: async () => "mock-token" }) },
    dbvMap: { value: fixed({ copyCell: async text => { try { await navigator.clipboard.writeText(text); } catch { /* frame refused */ } say("Copied"); return true; },
      copyPlan: async text => { try { await navigator.clipboard.writeText(text); } catch { /* frame refused */ } say("Copied"); return true; } }) },
    dbvEditor: { value: fixed({ open: async draftId => { location.href = "route-editor.html?draft=" + encodeURIComponent(draftId); return true; } }) },
    dbvRoutes: { value: fixed({ save: async fileName => { say("Mock: would save " + fileName); return fileName; }, open: async () => null }) },
    dbvAlerts: { value: fixed({ testSound: async () => { say("Mock: alert sound"); return true; } }) },
  });
})();
