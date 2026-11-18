# Codex on Windows

## Passive packet observer

The game socket forwards server frames before queuing a copy for passive analysis.
One worker parses in arrival order. Its queue is bounded to 64 frames and
2 MiB, plus one in flight. The owner checks the session generation and a 500 ms
age limit before publishing. A gap invalidates packet-relative position and
inventory correlations. Native health and map observations remain available.
The Diagnostics panel reports queue depth, lag and drops without packet content.
The normal game launcher still owns both sockets in its controller process.


## Staged session and command architecture (2026-11-17)

Rollback baseline is private GitHub tag `v1.0.0`, commit
`c319808d8c260e240f6704fd2038c5340e8086ce`. This is a checkpoint with known
issues, not a claim that every gameplay path has been validated. User settings,
local atlas databases, private recordings, logs and exported client assets are
excluded. The candidate is developed on `codex/session-command-lifecycles` in
an isolated worktree. Do not deploy sources or reload engine modules during play.

Stage 1: every accepted proxy connection owns a monotonically increasing session
number and a new GameState. Connection handover awaits old relay cleanup. Action
contexts and queued commands retain session identity; old commands cannot enter a
reconnected session, even when the proxy object or player ID is reused. Delayed
packet scans may update their captured state but cannot publish old chat or potion
evidence to the new session. The already-prepared LocalPlayer/relogin fix is included.

The bounded injection queue returns a CommandReceipt. States distinguish queued,
dispatched, rejected, expired, cancelled, failed-before-write and unknown-after-write.
The dispatcher rechecks TTL, session and live guard before writing. Action cancellation
invalidates pending commands; a timeout/cancellation after write is uncertain and
must not be treated as proof of failure or automatically retried as an item use.
Cooldowns are reserved after possible dispatch, never after a definite rejection.
A rejected/uncertain target selection does not authorize a spell on an older target.

Transport dispatch is NOT server execution. Position acknowledgments, observed band
equipment and Haste observations attach separate effect evidence where available.
Targeted spells have no universal success ACK; do not invent one from socket drain
or an HP delta. Potion diagnostics retain their existing typed server/count evidence.
API state exposes only bounded command metadata, no payload or credentials.

Stage 2: CavebotDomains supervises separate combat evaluation and navigation tasks.
Combat publishes chase/return intents; one navigation worker executes all movement
and transitions. Context-local command scopes prevent combat from inheriting another
coroutine's movement guard or receipt. Healing is a separate existing action task
with its own session-bound guarded dispatch; neither cavebot worker cancels it.

An uncertain transition holds movement for explicit review and does not repeat a
tool. Combat stays active when its own fresh inputs permit it. Temporary observer
gaps wait for fresh inputs. A combat programming error holds route movement and
reports a fault while healing remains independent. Global Stop/Pause, disconnect,
identity change and death still stop both cavebot workers. Ending a non-loop route
ends movement, while independently enabled combat continues. Existing enable flags,
profiles, routes and cooldown settings are not migrated or implicitly enabled.

The UI is now named Cavebot; internal adaptive_cavebot IDs and stored schemas remain
compatible. The run bar reports movement, combat and potion task states separately.
The pending UI section controls, private-chat mode fix, equipment-slot AiO lookup
and post-combat return fix are retained in this candidate.

This is task isolation within the existing process/event loop, not OS-process
isolation. CPU-heavy decrypt/parser work and proxy lifetime remain a later stage.
Synthetic tests cannot prove game server acceptance or eliminate client crash risk.
Before installation: preserve current settings and saved drafts, coordinate logout,
stop the old session, install the validated bundle, then restart with movement and
combat OFF. First verify read-only identity/HP/Ki/map, then coordinate any live test.

## Prepared section controls, chat mapping, recovery and relogin identity (2026-11-17)

This package is staged separately; do not reload engine modules during gameplay.
Coordinate logout before switching the engine. Preserve current settings and
do not restart navigation/combat as a side effect. The panel has matching native
checkboxes (custom appearance) above the movement/combat labels; MonsterRules
uses the same collapsible details section as shared spells. Editing semantics,
profile bindings and action enable flags are unchanged.

The previous alert parser confused internal MessageModes with DBV wire values.
Read-only verification of the pinned 8c15e00a...a54f5e8 client showed PrivateFrom
wire 6 (not 4), GM private 14, and NPC wire 4/5. parseTalk at RVA 1A99xx uses
U32 statement, string name, U16 level and converted U8 mode in this session.
The feature flags and bounded mode tree were read without message content.
Known NPC/channel/monster speech is now consumed without alerting, so a following
private message remains parseable. Unknown custom modes and opaque map data
still stop sequential scanning; never byte-scan chat out of those payloads.
Live private-message delivery remains to be verified after session replacement.

No-target combat drops an old return anchor once the player is already on the
current route cursor. This prevents waiting forever for an old blocked/offscreen
fight tile after route recovery. A real off-route chase retains its return path.
Manual movement grace no longer latches a sub-limit lure group into stationary
combat. Unreachable return paths now expose the usual tile blockage diagnostic;
combat snapshots include return_anchor. Live observation showed a return wait,
but did not establish the user's log item as the terrain cause.

Tool lookup checks all ten verified inventory slots, including AiO 8900 observed
in slot 10, as well as open containers. Inventory positions use (65535,slot,0)
and are revalidated with the item ID before dispatch. No items are moved/equipped
and actionbar shortcuts are not treated as inventory. Slot-10 use is covered by
synthetic transport tests; server acceptance remains a coordinated live check.

Speed Up diagnosis: the user reconnected at 17:38; the server assigned a new
LocalPlayer ID, while the bridge retained the previous ID. HP/Ki stayed 0/0 and
Speed Up never sent a cast. The pinned client's getter signatures, current
HP/Ki and Haste condition bit were verified by bounded read-only reads.

Each login now gets a new GameState; disconnect cancels the internal bridge too.
On the observer profile the bridge identifies the unique LocalPlayer vtable on
the verified center tile, validates ID/stats twice, then pins that ID for the
session. Packet parsing cannot replace this verified identity. User actions wait
for identity binding before capturing an ID. A delayed old login callback cannot
start actions in a newer session. Action settings and emergency-pause flags are
preserved; navigation remains excluded from automatic login startup.

The candidate reader was checked against the live client without injecting,
casting, moving or altering runtime state. Tests cover reconnect, torn/ambiguous
identity, stale callbacks, passive-reader cancellation and synthetic Speed Up
dispatch after binding. Actual automatic renewal and a second live login remain
to be checked after the coordinated session switch. No engine hot reload.

## Pending combined package (2026-11-17, after 15:30)

These sources are prepared and tested separately from the active game session.
Install only after a coordinated logout, saved drafts and stopping the old bot
and panel. Preserve current healing/band settings; do not enable V2, combat or
Speed Up as a side effect. This package does not establish crash causality.

Lure spacing uses recent contact or observed pursuit. A reachable target ahead
which the next step approaches does not cause a spacing wait.
The gather timer starts with the first engaged/adjacent monster, not a distant
candidate. Capacity, reachable-terrain filtering, finish-before-room-exit,
movement waits with continued combat and same-floor manual-movement recovery
remain in place. There is no new travelled-distance cap in this package.

Alerts cover parsed incoming private/Default messages and typed live-map player
arrivals. Each type has independent enable/sound/always-or-running-V2 settings.
They never pause actions or send replies. History is bounded to 100 in-memory
events, not written to settings or logs. Player jitter and chat bursts are
coalesced. Default speakers require a recent typed human identity; own messages,
NPCs and known spell words are excluded. Talk parsing accepts only recognized
sequential packet boundaries, not arbitrary bytes inside map data. Live chat
coverage still requires verification against the actual server. Electron audio
polling continues while the window is hidden; one trusted IPC tests the sound.

Speed Up reads the pinned LocalPlayer conditions and speed getters. Optional
getter failure cannot invalidate HP/Ki. One passive user-cast measurement was
33.266 s (15:31:02.378 to 15:31:35.638), speed delta +470; default duration is
33.3 s, editable. The two offsets specify maximum seconds before/after expiry.
Draw once per confirmed cycle; observe actual expiry where possible. Do not use
the old packet-icon heuristic. An already-active effect of unknown age waits
for expiry; failed confirmations are throttled and stop after three attempts.
Catalog cooldown 1 s is distinct from measured effect duration. Early renewal
acknowledgment is not yet verified live; uncertain renewal waits for expiry.

Named combat profiles and bot-configuration profiles live in local_data/profiles.json.
Route geometry stays in atlas drafts. A scoped route-to-combat binding in settings
loads its default when the user selects that route; a different combat profile
can then be loaded and optionally bound. Combat edits/load preserve stopped
navigation progress and spell deadlines. Existing embedded route combat remains
available as a legacy import. Whole-bot profiles contain configuration only,
not runtime enable flags; loading requires stopped user actions and starts none.

Pause/Break is registered by the Python engine using RegisterHotKey(VK_PAUSE,
MOD_NOREPEAT), so it works with the panel hidden. It toggles the existing manual
pause/resume path, preserving selection and V2 progress. Repeated keydown is
debounced; resume errors remain paused and visible. It never unlocks Emergency
STOP. Key registration and a physical-key round trip still need a live check.

At the user's request, a NEW visual-only marker replaces the withdrawn target
setter experiment: dbvobserver_marker_20261117.dll / dbvbot_marker_20261117 pipe.
It calls only fully byte-verified Creature::showStaticSquare/hideStaticSquare
leaf functions on the reviewed main-thread PeekMessageA boundary. It never
calls Game::setAttackingCreature, changes a Game shared_ptr, triggers Lua, or
sends attack/movement input. The red square represents a bot attack already
written to the server; it does not claim native Game target state was changed.
Manual selection and existing native marks take precedence. Fixed M messages
contain only a monster ID, expected native target and sequence; no addresses.
Requests expire after 350 ms; the visual lease expires after 1.2 s without fresh
renewal, and pause/manual selection clear intent. Generic commands remain denied.
Function/ABI/IAT mismatch disables the marker. The full executable hash is pinned
before injection. The old Game-target DLL remains withdrawn after the unresolved
actionbar crash. The new visual hook still requires coordinated gameplay testing;
offline tests cannot prove stability in the game. Replacing an already loaded
marker hook requires restarting the client because its module remains pinned.

Validation must include full Python tests, dashboard tests/typecheck/build,
32-bit marker compilation, the synthetic marker process and an isolated visual
panel preview. No game process is opened or input sent by those tests.

## Route-aware lure pacing and stopping places

An approaching monster starts influencing pace after adjacent contact or an
observed pursuit step. Two pursuit steps still confirm a follower for the group
count. The next route step waits if it would increase the walkable distance to
a trailing member already at the spacing limit. The engagement radius also
bounds that distance when a radius is configured. Combat rechecks current targets
after a cast. Navigation rechecks spacing before dispatching a route step.

When a gathered group needs a stopping place, the controller compares up to
three forward route steps with one adjacent step off the route. It measures
reachable adjacent attack positions and nearby open space, while keeping the
members within reach. An already adjacent group stays in place. A side step
keeps the waypoint cursor at the route anchor and uses the existing movement
executor. After combat the controller returns to the anchor.

The configured gathering time now starts with the first adjacent eligible monster
or confirmed follower, rather than the first distant candidate. Group-capacity
latching, blocked-path combat, room exits and the existing time limit remain.
There is no new distance-in-steps limit in this patch: lure_distance continues to
mean spacing from a trailing group, not total travelled distance. The panel's
runtime reason distinguishes approaching along the route from waiting for a
trailing group. No native target synchronization is re-enabled. Installation and
live testing require a coordinated session replacement, never a reload mid-fight.


## Client crash containment (2026-11-17, 14:57)

The session ended with a client fatal Lua error in game_actionbar/actionbar.lua,
updateCooldown at line 1201 (scheduled callback at 1222). The Python proxy then
observed disconnect and stopped actions; no preceding Python traceback was found.
There is no Windows application-crash event/dump establishing a native cause.
The subsequent client update replaced six Lua resources, including gamelib/const.lua,
but not actionbar.lua or the executable. Neither the update nor this stack trace
proves or excludes a contribution from the experimental target hook.

As containment, the current observer profile again uses dbvobserver_20261116.dll
and its read-only pipe. Proxy attacks no longer call a native target callback.
The native red-square integration below is withdrawn, not validated for gameplay.
Its source/binary are retained for offline investigation only; normal startup
does not select them. Existing movement/combat fixes remain in place. No client
files were modified, and the session is not automatically restarted after this
rollback. A future live check must be coordinated in a safe location.


## Combat edits and discarding stopped progress (2026-11-17)

Resuming checks a navigation identity (draft ID, points, explicit links and loop)
instead of the entire draft row. Saving combat, labels or a name preserves the
stopped cursor. Explicit Resume loads the latest saved combat rules while retaining
the previous spell/shared cooldown deadlines, resetting old target/lure decisions
and rechecking conflicts/fresh state. Configuration changes during asynchronous
resume still abort the pending request. Saving alone never starts an action.

Both V2 control bars offer Discard progress while stopped: it cancels pending
preparation and clears only the in-memory route cursor. It never deletes a draft,
changes combat settings or sends a game packet; an active runner must stop first.
An actual waypoint/link/loop change still requires a fresh plan. List highlighting
uses the same navigation identity and survives combat-only revision updates.

The completed state is explicitly labelled Zakończony and says that repeating is
disabled. Duplicate first/final coordinates choose the first on fresh Start;
nearest-waypoint starts elsewhere with loop disabled finish at the last waypoint.
Neither this diagnostic nor installation changes the user's saved loop setting.

## Responsive Start, waypoint tolerance and retained progress (2026-11-17)

Route preparation runs in a worker using captured inputs and an unpublished
controller. The main event loop remains available for DLL samples, healing and
Stop. The existing 500 ms client-state freshness guard remains unchanged. Start
rechecks current HP, map, position, player/proxy/atlas identity and draft revision
before publishing a controller. Stop, pause, emergency, disconnect/reset, manual
movement/use and cancelled/overlapping Start requests cannot later activate a
pending plan. This fixes the observed 0.7–0.8 s synchronous planner starving the
sampler and immediately rejecting its own stale HP/position sample.

Original waypoint numbers are retained separately from path cells through
nearest-waypoint starts, loops, detours and recovery. The list highlights the
upcoming waypoint and shows the last reached number only for the matching saved
draft revision. Pause and explicit Stop preserve the cursor in the current bot
session; Start on that unchanged route resumes it. A known ordinary approach
can rejoin its saved segment after manual movement; a different floor, unknown
approach, changed draft or required approach transition is rejected. Exact next
positions acknowledged during pause advance the cursor, including floor exits.
Disconnect/reset/emergency clear progress. No navigation restarts automatically.

A blocked ordinary waypoint within two SQM may be credited only after finding
a fresh-map detour towards its next waypoint. Occupancy and known solid obstacles
qualify; unknown/avoid cells and all object actions or transition endpoints do
not. Each actual step still rechecks live terrain. Ordinary detours/recovery
cannot silently skip later waypoint identities outside this tolerance. A cyclic
endpoint needs a real continuation back into its cycle; a blocked one-shot final
destination still pauses. The diagnostic log records the original waypoint and
distance used. This adds no combat, movement or equipment auto-enablement.

## Targeted Shinretsuha and recorded tool ascents (2026-11-17)

The user confirmed Shinretsuha hits the selected target. Its client catalogue
category is misleading: an explicit runtime override now treats it as on-target,
with no facing requirement, turn packet or adjacent Wave mask. Configured target
distance, monster count, HP/Ki, priority, shared/per-spell intervals and queue
guards remain unchanged. The picker labels it Celowany while preserving its
original icon, catalogue metadata and saved cooldown overrides. This supersedes
the provisional Shinretsuha Wave profile below; other Wave spells still require
a reviewed footprint. Server acceptance/range/cooldown are not inferred from a
queued command. The updated DLL remains an observer: proxy ATTACK does not set
the native red target square. No old-client targeting offsets/hooks are enabled.

V2 can execute an explicit observed use_with ascent by rope 3003 or AiO 8899/8900.
The learned entry object, adjacent/same-tile approach and exit one floor above
must match. It reads a tool from an open container or hand, rechecks both tool
location and live object in the queued operation, sends one use and waits for
the exact learned exit. Missing tools, changed/occupied objects and unexpected
or unacknowledged exits pause without repeated use or guessed walking.

AiO substitutes the required tool function, not an arbitrary action: rope and
AiO are interchangeable for this recorded ascent; shovel 3457 and AiO are
interchangeable for a recorded dig-then-enter descent. Digging is skipped when
the learned open-hole ID is already present. Scythe/crowbar/other uses and fly_up
remain unsupported until their mechanisms are implemented. Preview and executor
share matching support criteria. Saved rope/dig/use-enters gate links keep their adjacent
approach after a later observation from another side; conflicting destinations
across equivalent tools are rejected. Recorded rope use is matched to the
acknowledged ascent instead of reported as an unexplained extra action.
Installing this code does not enable combat,
movement, equipment actions or new DLL commands; live execution needs a separate
user-authorized test after session replacement.

## Stable editor viewport and wide atlas zoom (2026-11-17)

Saving/reloading the same draft keeps its current center, floor and zoom. Adding
or repairing a transition also keeps the view; Show exit is an explicit action.
The transition picker names the affected waypoint pair, and distinguishes a
repair from adding points at the end. A selected gap with no matching transition
cannot silently append a new tail. Existing points from earlier failed edits are
not deleted automatically.

Zoom-out supports a radius of 96 (193 tiles along the longer viewport dimension).
Wide views query fixed atlas chunks through the unchanged loopback read API,
using at most three concurrent requests. A view-local 24-chunk cache has a ten
second TTL; near views keep their live polling. Profile/floor boundaries are
checked, duplicate cells are merged by timestamp, and closed/cancelled views stop
issuing batches. The server's persistent atlas is unchanged and remains uncapped.
Fresh live cells still overlay the cached historical view.

## Readable transition previews (2026-11-17)

The map draws the single adjacent approach step into an explicit step/dig entry
even when the next acknowledged position is already on another floor. It never
invents movement for tool-only actions, remote entries or other floors. Repeated
markers at the same entrance/exit are combined visually, retaining all underlying
transitions and respecting per-stage visibility. Endpoint labels are smaller and
merged at shared coordinates. An observed destination on a generic passage is
labelled Up/Down by floor direction; this does not reclassify every matching item.

The preview lists concrete execution blockers (unsupported transition with its
number/tool/coordinates, and each avoided tile). Clicking one shows its location.
Geometrically complete plans are not advertised as ready when such blockers
remain. Previewing and selecting labels do not authorize movement or change
saved route points. Runtime support for rope/AiO use-only transitions is unchanged.

Reconfirming a moved adjacent approach to an existing exit repairs that floor
change in place. Simple step/use keeps the chosen adjacent point and relies on
the backend's observed-entry/unique-exit checks. Complex tool sequences return
to the observed approach; no unobserved tool binding is invented.

## Transition repair and independent combat controls (2026-11-17)

Selecting a transition on an ordinary anchor placed on its entry repairs that
occurrence in place, preserving the rest of the route. An existing exit is reused;
a missing exit is inserted. The selected gap disambiguates repeated visits.
Without that context, multiple unresolved visits require choosing a specific gap
instead of silently editing a different visit. Link indices follow occurrences,
and Undo restores the original points, labels and links. Conditional dig variants
with the same observed tool set, object, prepared state and destination appear as
one choice. Different mechanisms/destinations remain separate. The picker and
post-selection notice explain that only the atlas view changes floor.

The movement checkbox is beside Waypoints. Turning it off routes Start directly
to the existing combat-only command, even with an invalid or unsaved sketch;
the current combat configuration is passed explicitly. No route is planned or
executed. Route starts still require saved route/configuration. Combat settings
can be saved while editing points. Before saving an existing sketch, the editor
refreshes its revision only if the persisted geometry/name/labels/links/loop
fingerprint is unchanged; concurrent route edits are still rejected.
These are UI-only changes; they do not enable any action or restart the proxy.

## Editable optimization errors and recorded-link repair (2026-11-17)

Selecting a failed optimization leg leaves preview and opens the unchanged
original sketch in edit mode. It stops follow/simulation and enables waypoint
and context-menu edits; it never accepts the invalid candidate. A matching
source recording can restore missing transition links in an older saved sketch.
The point sequence must match exactly; labels and existing explicit links are
preserved. Geometry mismatches leave all edits intact. Restoration remains an
unsaved edit, supports undo and requires explicit Save. These changes affect
only the panel and do not require replacing the proxy or game connection.

## Recording repair, automatic return and route entry (2026-11-17)

An open-hole step and observed digging of that exact hole now share a
conditional dig transition when entry, exit and prepared item agree. The
executor skips digging when already open. Conflicting objects/destinations
remain rejected. A recorded, acknowledged southwest step demonstrated that
DBVictory allows diagonal movement between blocked side tiles; preview, step
guards and detours now validate the destination rather than those side tiles.
Unknown/blocked/occupied destinations and implicit floor transitions remain
excluded. Diagonals use normal path costs, without random detours.

With repeating enabled, the planner shows a known ordinary return to the first
waypoint without the previous two-SQM restriction. If that fails, it plans
waypoints in reverse order using independently learned inverse transitions
near each original exit/approach. One-way holes/teleports cannot invent a way
back; the preview identifies the unresolved return. Anchors remain unchanged.
Execution joins the nearest reachable waypoint on the current floor by known
ordinary terrain, rotates a verified closed cycle, and retains all transitions.
It does not silently traverse another floor to reach the route.

During luring, a fresh eligible monster occupying the next adjacent route tile
temporarily takes priority. The bot stays, attacks using the configured monster
policy/spells and resumes luring once the tile clears. This time does not consume
the gathering timeout. Ignore rules, creature classification, manual selection,
cooldowns and target guards remain enforced. A gathered group still stops for
combat normally; navigation does not enable combat when it was disabled.

Optimization returns the actual failing segment, coordinates and reason rather
than the generic instruction to repair gaps. The editor can focus that location
and explicitly save the original while previewing an invalid proposal; failed
saves retain edits. Partial path lengths are not shown as a successful reduction.
Previously saved recordings can be reimported from the source recording to
rebuild missing transition links; no user draft is rewritten automatically.
All validation uses local recording copies/synthetic transport; production
session replacement remains coordinated with manual logout.

## Recorded approaches and readable route stages (2026-11-17)

The learner's existing transition IDs identify entry/exit mechanisms and keep
only the most recent approach. Import now binds a confirmed recorded step/use
to its actual adjacent approach. Saved explicit links also remain usable after
a later observation updates that approach. Unknown entries, distant approaches,
conflicting exits and complex unobserved tool sequences are not generalized;
ordinary route legs still cannot take an implicit floor transition.

An unmatched world use is identified by item and position in simulation. When
the entire geometric route is valid, the editor offers an explicit omission of
those extra uses. This does not alter the recording and cannot acknowledge away
position gaps, avoided terrain or unsupported transitions.

The editor uses a dedicated flex workspace: its Save/Discard row never grows
with the canvas. Manual pan or floor selection disables following; the explicit
Show exit button reveals a transition's destination floor. Labels show transition names and
known target floors at readable zoom, with duplicate destination choices merged.
Routes use thin translucent lines, direction arrows and smaller waypoints.
The stage strip is visible while editing; per-stage visibility affects lines,
waypoints, original trace, transition markers and the all-floor overview, without
changing saved route data. Selecting a stage centers its floor independently
of following. All stages are visible by default.

`electron/layout-regression.cjs` renders the actual map component and compiled
styles in an invisible, sandboxed Electron test window using synthetic data.
It checks compact controls and usable map height at 1280x900 and 2491x1288,
and saves screenshots. It does not connect to the production dashboard or game.

## Recorded transition variants and draft editing (2026-11-17)

Recording import treats observations of the same specifically used object,
action, tool and exact origin/entry/exit as one transition even when loose items
changed the entry stack. Conflicting destinations still fail live-map validation;
recorded position evidence is mandatory. Drawn ordinary legs cannot silently
take a hole, staircase or teleport.

Recorded world uses are matched to planned transitions only with the correct
object, tool, target, origin and acknowledged crossing after the request.
Matched uses do not require redundant manual review; unknown requests, gaps,
avoided terrain and unsupported actions retain their warnings. A failed earlier
leg no longer incorrectly diagnoses the unvisited loop-closing leg.

Right-click always opens the tile menu, including in draw mode. Waypoint removal
is an explicit menu action and can be undone; object annotation stays available
on fresh visible tiles. Unknown historical cells still allow waypoint removal,
but cannot be relabeled as if their live state had been observed.

The map editor contains its own draft name, unsaved indicator, Save and Discard.
Drawing, importing, planning and optimizing never save a route automatically.
A blank name defaults to "Nowa trasa" only on explicit Save. Discard restores the
last acknowledged saved contents; deleting all points remains an unsaved edit.
No game commands or automation toggles are used to test this workflow. Backend
replacement still requires a coordinated session restart; no Python hot reload.

## Route workflow and tray corrections (2026-11-17)

Known ordinary doors with a uniquely observed closed/open pair and normal
open-then-step behavior may be opened along an ordinary same-floor leg.
The planner derives adjacent exits only from known terrain; execution checks
the live object, occupancy and opening acknowledgment before entering. Level
gates, stairs, holes and teleports still require explicit route transitions.
Recording preserves a sampled open-door crossing, including consecutive walk
commands whose complete acknowledged chain spans an intervening map sample.
Unknown or ambiguous door behavior is not generalized from neighboring IDs.

Loop returns follow the automatic-return rules described above. The planned
return is visible in preview; drawn anchors remain unchanged. A return cannot
invent a staircase, hole or teleport. Simulation checks the planned closure.

Saved/dirty state compares current sketch contents with the acknowledged save,
including labels, transition links and loop setting. Adding a known transition
retains it even when an earlier leg has a gap, and reports that actual gap.
The original follow icon is active by default; there is no extra checkbox.
Closing the main panel with X hides it and its editor to the Windows tray,
preserving unsaved renderer state. In the tray menu, **Zamknij panel** closes
Electron. **Zakończ bota** ends the owning engine session, its panel and the
standalone launcher. Log out to the character list first if connected through
the proxy. The game client stays open.

Use **Look** on a pet in the game world. Once the server identifies its owner
with `Master of …`, the live map and atlas show **P · Pet**. Cavebot excludes
that creature from targeting, spell conditions and lure counts. Its tile still
counts as occupied. Inspect again after a new login or summon. A pet without
a battle ID can be followed while its map instance stays visible. If the
description wasn't matched, right-click the pet's tile in the live map or
atlas and choose **Oznacz jako peta…**. The manual label follows that one
creature while it remains visible. **Usuń oznaczenie peta** clears it.

## Adaptive hunt recording

In **Waypointy → ⋯ → Nagrywanie adaptacyjne**, start observing before or on
the hunting ground. Hunt normally and revisit the rooms in any order. Use
the stop-recording icon to create a sketch preview. The recorder associates
position with XP changes that fit the reported level, including fights where
the player stands still. It compares separate visits on each floor, picks an
observed return through multiple areas with XP, then checks the walk and its
transitions against the atlas. The chosen pass may start on a different tile
in the same area. One-way travel, long trips without XP and disconnected
observations don't establish a circuit.

Use **Szybka symulacja** to review, then **Zapisz szkic** to keep the result.
The full recording is also available under **Wczytaj wcześniejsze nagranie →
Wczytaj pełny ślad**, including when the evidence is insufficient for a sketch.

## Explicit route transitions and Barrier Band (2026-11-17)

Ordinary waypoint legs and the approach to the route stay on one floor and
avoid known stairs, holes and teleports. Known normal doors are handled as described above. Select a learned transition in
the map editor to add its exact origin/exit pair and ID to the draft's `links`.
Recording creates those links only from observed crossings. Optimization,
save/load and undo retain them; moving or removing an endpoint revokes the link.
Existing drafts keep their points but need their transitions selected again.
An unknown tile is never evidence that a path is safe.

V2 can recover from a small unexpected displacement on the same floor using
fresh visible terrain: at most 6 SQM from its expected position, 12 return
steps, two recovery attempts in 30 seconds. It cannot skip a transition, override
a manual pause, or invent a return from another floor. Cross-floor recovery is
not implemented; an unexpected fall or teleport pauses navigation. Manual input
continues to pause route movement. Healing remains independently controlled.

`auto_barrier_band` is OFF unless explicitly enabled. It equips **Barrier Band
3051**, whose active equipped form **3088** was confirmed by the user and a
read-only client observation. **3052 Ki Band is not used.** Default destination
is the band slot (9); right/left hands (5/6) are optional. The action never
replaces another item or equips a second band while one is already equipped.
Keep spare bands in an open backpack. The reported 15 minute duration is not
used as a timer: renewal requires two fresh empty-slot observations, and success
is an equipment update, not an HP change. Unconfirmed attempts are separated by
at least 5 seconds and capped at three until an explicit off/on retry.

Inventory access is read-only and limited to the pinned updated client, bounded
containers and validated item vtables. Stale reads, death, changed identity,
closed bags, occupied destinations and pause/cancellation invalidate commands.
No movement, combat or equipment smoke test is performed by installing changes.
Live equip/expiry and recovery execution still require a controlled game test.

## Atlas storage and prepared holes (2026-11-17)

The previous 100,000-tile cap silently stopped recording new regions. It is
removed, along with the 80 MiB database cap. SQLite retains the entire atlas;
a 4,096-tile recent cache and on-demand 16x16 pages bound RAM use. View queries
and route planning can read older regions after cache eviction and restart.
Existing tiles/drafts are retained. Areas omitted by the OLD cap must be visited
again; learned transition metadata alone does not reconstruct their terrain.
Disk-full/read failures remain visible errors; no automatic atlas deletion.

Atlas follows the player by default. Loading a draft no longer changes that
preference. Panning or changing floors disables **Śledź postać**; zoom stays available.
Simulation temporarily shows its own position, then returns to player tracking.

V2 supports observed `use_with_then_step` digging transitions, with verified
closed/open IDs and a known downward exit. For example, the recorded hole at
669,407,7 changes 593 -> 594 and lands at 669,407,8. Closed holes require a
matching tool in an open container or hand, a guarded use-on packet, an observed
open state, then a guarded step and landing acknowledgment. Already open holes
skip tool use. Missing items, changed terrain, occupied entry or missing
acknowledgment pause the route without repeated digging. Alternatives are
accepted only when each tool has its own matching observed transition (here
3457 and 8900); this does not assume every AiO variant is interchangeable.
Rope/fly-up execution remains unsupported. Trapdoor 476 and ladder 1948 are
classified only for the verified current DAT profile.

The atlas, digging and band version was installed after user logout. Automated hole
traversal and band equipment have only synthetic transport tests, not live tests.

## Client update 2026-11-16

`client_profiles.py` pins both reviewed executable digests. Startup checks the
running executable before patching addresses or closing connections. The updated
build uses a separate read-only `dll/dbvobserver_20261116.dll`, built from
`dll/observer.cpp` with 32-bit MinGW (`-shared -static -O2 -std=c++17`).
It reads the visible 18x14 map and creature metadata and accepts no commands.
Attack, XTEA, keyboard and lighting hooks from the legacy bridge are not loaded.
Automatic DLL targeting is therefore unavailable for this profile. HP/Ki are read from the verified LocalPlayer double fields by a separate read-only
Python sampler. Atomic snapshots expire after 500 ms; delayed packet statistics
cannot overwrite them. Windows comparison with the client confirmed matching
HP/Ki after login. This does not establish server cooldown or future combat safety.

Map layout addresses were checked using static RTTI/accessors and bounded live
read-only snapshots. Each executable/DAT/server combination has its own learning
scope. Previous learned transitions and manually confirmed IDs remain on disk;
the new asset does not silently inherit old confirmations. Native decoder checks
can be compiled from `dll/test_observer.cpp` as an executable and run without a
game client. Login and gameplay still require live verification after compilation.

This fork exposes the existing bot through standard MCP over stdio. Codex sends
tool requests; Python runs enabled background actions. No Anthropic API or Claude
subscription is required. The integration does not make the game automation
compatible with a new client version automatically.

## Install

### Start without Codex (installed Windows setup)

Double-click `Uruchom bota.cmd` in the repository. Leave DBVictory at the
character list or login screen, then press Enter in the launcher. It uses the
same MCP engine and authenticated Electron panel, without starting Codex.
The local launcher defaults to world port 7475; `run_bot.py --game-port PORT`
overrides it. It reuses the installed Node runtime and Python environment.

Keep the launcher console open (minimizing is fine). Type `panel` and Enter to
reopen a closed dashboard during play: this does not reset the proxy, interrupt
the game or change actions. `status` and `actions` inspect the session. `quit`
or closing the launcher ends the proxy, which disconnects an active game.
The launcher refuses duplicate sessions/occupied ports. Use the existing console
if it is already running; do not start a second bot alongside Codex's session.

Every fresh standalone start disables saved combat/cavebot action switches.
Spell/monster configurations and saved healing preferences are preserved. Other
previously enabled actions, such as potions, resume after login. The optional
`--start` flag is an explicit assertion that the character is already logged out.

A fresh proxy cannot take over an existing game connection seamlessly. The
current `start_bot` implementation closes the client's old game sockets and
requires another login through the proxy. Prefer a safe manual logout first.
Closing only the panel is different: MCP `open_dashboard`/console `panel`
reopens that session's authenticated UI while the connection keeps working.

Use native Windows Python 3.11+ (3.12 recommended), Node.js 22.12+ and an installed,
signed-in Codex CLI. Start with a normal, non-elevated PowerShell terminal. The
bot and game should run under the same Windows account and integrity level.

From the repository root:

```powershell
.\scripts\setup-codex.ps1
.\scripts\start-codex.ps1
```

The setup script creates `venv`, installs the pinned direct Python dependencies
(including PyCryptodome), runs `npm ci` and a production dashboard build, then
registers `dbvictory-bot` with Codex using absolute paths. It does not launch the
game, patch memory, change PowerShell policy, or elevate privileges. Review the
script before running it. If script execution is blocked, follow your own
Windows policy; the equivalent manual steps are below.

`-PythonCommand C:\Path\To\python.exe` selects a Python installation.
`-SkipDashboard` skips Node installation/build for MCP-only setup. Move the
repository later? Run setup again to register the new paths. Registration uses
the server name `dbvictory-bot` and replaces any existing registration with that
name; other MCP servers are unaffected.

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location dashboard
npm ci
npm run build
Pop-Location
codex mcp add dbvictory-bot --env PYTHONUTF8=1 --env PYTHONUNBUFFERED=1 -- "$PWD\venv\Scripts\python.exe" "$PWD\run_mcp.py"
codex mcp list
codex
```

For manual TOML configuration, merge `config/codex.example.toml` into your Codex
configuration after changing the example paths. Do not replace the entire file.
The repository's legacy `.mcp.json` is for Claude Code, not Codex.

If the client uses different server ports, set `DBV_LOGIN_PORT` and/or
`DBV_GAME_PORT` in the MCP server environment. Defaults remain 7171 and 7172.
For example, a client observed connecting to game port 7475 needs
`DBV_GAME_PORT = "7475"` under `[mcp_servers.dbvictory-bot.env]`.
Restart the MCP session after changing these values. Verify the actual client
ports; the client version alone does not establish them. Character-list data
loaded after startup can restore the remote game address, so inspect the proxy
status after login instead of treating a successful memory patch as proof of
connection.

Official MCP configuration reference: https://developers.openai.com/codex/mcp

## Verify without connecting to the game

In Codex, use `/mcp` to check that `dbvictory-bot` is available. Ask:

> Use only get_status and list_actions. Do not start the bot or enable actions.

The status should show a running MCP server and an inactive proxy. No game client
is needed for this check. Starting MCP alone does not launch the dashboard.

For automated checks (also supported on Linux):

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -q
```

The suite includes a real MCP stdio handshake from a different working directory,
read-only status calls, HTTP authorization checks, and WebSocket authentication.
It does not inject the DLL or log into the game.

## Windows acceptance test — still required

1. Build `dll/dbvbot.dll` from source with a 32-bit MinGW toolchain (`cd dll; make`),
   as described in the main README. Review the existing build scripts before use;
   the Codex setup script does not install a compiler or download a prebuilt DLL.
2. Open `dbvStart.exe` at its login screen. Review `list_actions` and disable any
   previously saved actions you don't want to run. Fresh installs enable no user
   actions; the internal DLL bridge starts when the game connects.
3. Ask Codex to call `start_bot`. This changes the running client's memory and
   starts loopback proxies and the Electron panel. It returns while waiting for
   login in the background (up to 120 seconds).
4. Enter credentials in the game client only. Check proxy, player and DLL status.
   Do not paste account credentials into Codex or attach packet dumps containing
   personal data.
5. Enable one requested action, verify its effect, then disable it and confirm it
   stops. Check the dashboard receives state and its controls work.
6. Test reconnect/reset. It should stop only the dashboard processes launched by
   this MCP session; unrelated Electron applications must remain open.
7. Exit the MCP session normally, close the game, and restart the game to discard
   memory patches/DLL injection. Forced process termination may leave children
   running; stop only this bot's processes if recovery is needed.

If attachment fails, check the process name, client version, bitness and Windows
integrity levels. Do not solve it by routinely elevating the entire coding agent.
Some native operations may require elevation; privilege separation is not
implemented in this fork. WSL-only Python cannot attach through these Windows APIs.

## DBVClient_v5 status and player-state checks

Native Windows testing confirmed that the v5 login position uses marker `0x4B`
and a 19-byte prefix after the player ID. `offsets.json` records this layout.
PLAYER_STATS capacity is in hundredths: fallback validation must apply its
`capacity_scale` before rejecting an otherwise valid HP update. Bundled stats
messages are applied in wire order, with the newest value winning. Exact HP
must not be overwritten by the DLL's independently polled health percentage.
DBV can also send bonus Ki above the nominal maximum (observed 119/100).
The profile's `allow_mana_over_max` accepts this without rejecting the entire
initial HP snapshot; the absolute sanity limit still applies.

DBV v5 uses a 13-byte CREATURE_MOVE payload, including a two-byte step duration.
Treating it as 11 bytes misread duration values as map opcodes. A move followed
by its matching map strip supplies the absolute destination, so diagonal steps
update both axes once; nearby creature moves do not move the player model.
Coordinates below 100 are valid. Ping/pong prefixes are consumed before moves.

Stairs can bundle a map strip and multiple `0x4B` floor descriptions. The parser
recovers their shared center only in a verified map context and with at least
three consecutive ordered floor headers, including the center's floor, within
two tiles and one floor of the confirmed step. This is a constrained fallback,
not a full tile decoder; isolated/inconsistent patterns are ignored. Manual
Windows tests confirmed horizontal/diagonal movement and immediate stair Z
updates without another step. Unknown layouts and longer teleports still need
separate validation. Regression fixtures use synthetic data, not captured payloads.

The Electron panel registers no global Space shortcut. Client configuration was
observed to retain `wasd_attack: Space`; the user subsequently confirmed both
Space and mouse targeting work. No keyboard binding was changed, so the earlier
failure has no established cause. This does not validate automated DLL targeting.

Pipe status checks reuse the active bridge, and Win32 calls declare
pointer-sized HANDLE types so 64-bit Python correctly rejects failed opens.
To verify after a client update, compare initial HP and position with the game,
then check manual movement and HP changes without enabling user actions.
These checks do not establish compatibility of targeting or other DLL hooks.

## Potions, emergency pause and route safety

The `auto_potions` action now accepts an ordered `rules` list (at most 32 rules).
Each rule has a stable `id`, `enabled`, `resource` (`hp` or `ki`), `threshold`,
`jitter`, `item_id` (10161 Special Health Potion, 10160 Special Ki Potion or 8465
Senzu), `cooldown`, `interval` and `mode` (`use` or `self`). It executes only the
first eligible rule per poll, skipping rules on cooldown; it does not blindly
execute a sequence. Conditions use strict `<` percentage comparison. Inventory
availability and successful consumption are not inferred from a request.

The dashboard supports adding/removing and reordering rules with up/down buttons.
New rules, including the Senzu preset, are disabled and inherit the shared
cooldown (`cooldown: null`). An optional per-rule override can extend it. The
shared value must be set before enabling inherited rules. Switching the selected
item preserves the cooldown choice and outstanding global deadline. Threshold
variation is displayed beside the HP/Ki condition in percentage points, with the
resulting range; cooldown and minimum interval are labelled in seconds.
Existing two-potion settings are adapted in memory to HP then Ki rules; loading
the editor does not rewrite settings or enable actions. Saving commits the whole
ordered list. The action key remains `auto_potions` for backward compatibility.

The longer of interval/cooldown is enforced, plus `shared_cooldown` for
all three items. Cooldowns and fresh-stat checks belong to item IDs: duplicate
rules, reordered/replaced rule IDs, manual uses and lowered settings cannot
bypass a pending deadline. Multiple enabled rules for one item use its longest
configured cooldown. A threshold is sampled once per use/configuration change,
clamped to 1–100%. Timers survive action toggles within the same MCP session.
Unknown cooldowns (0) prevent enabling an active rule. The legacy `auto_senzu`,
`mana_recovery_20` and `mana_recovery_70` actions cannot run alongside this engine;
both toggle and startup paths reject the conflict without silently switching
another action off.
Use mode supports ordinary item use and use-on-self; confirm the mode in the
manual-use diagnostics for this client. No live potion use has been validated
by the offline suite. Stale stats (>5s), invalid values, death or disconnected
state suppress use, and another attempt requires a new stats update.

The passive potion diagnostics run for five minutes and retain up to 500 manual
and automated attempts, with time, source, response, reported item count and
HP/Ki deltas. Measurement does not pause healing or send requests. HP/Ki changes
are never acceptance evidence, including at full health. DBV's typed server
`Using one of N ...` message also precedes failed attempts: exhaustion is marked
as rejected. A uniquely matched subsequent N-1 report supports consumption by
the prior attempt; overlapping requests or inventory move/trade commands break
that inference. The final attempt can remain unconfirmed until another count.
Generic exhaustion is attributed only alongside the matching typed use response
in the same server packet; a Senzu-tagged refusal may match a unique recent Senzu
attempt. No arbitrary chat or raw payload is retained by these diagnostics.
This is count evidence, not an automatically established server cooldown.

The rule loop polls every 50 ms. Trigger logs include the actual sampled
threshold and age of the player snapshot. The previous 50% +/-10 p.p. setting
could wait below 43%; the exact historical sample was not logged. Existing user
thresholds are preserved, and the faster poll cannot override server exhaustion.
MCP exposes `configure_potions` and `potion_diagnostics`; neither implicitly
enables healing.

The dashboard's red STOP button and MCP `emergency_stop` latch off all user
actions, both cavebots and auto-walk, discard queued injected commands, and
request cancellation of walking/attack/follow. The proxy continues relaying
manual game traffic, and the internal DLL status bridge remains available.
Already-sent commands or existing memory patches cannot be undone by this
button. It does not detect anti-cheat, hide or unload the DLL. The pause and
disabled actions are saved across reconnects/restarts. `unlock_automation`
only removes the latch; actions/routes must be enabled again individually.

Route playback now requires a recorded point on the player's current floor
within eight tiles. It starts at the nearest such entry, uses the starting
side of floor transitions, and stops when it cannot rejoin a nearby point.
Both cavebot loops yield even when skipping nodes. This addresses a confirmed
busy loop when Baltra was started on a ship on floor 6 while all recorded
points were on other floors: it starved the event loop serving the game proxy.

Route conversion now stops attributing an item click's effects when a new walk
or item-use command arrives. In `Assasin_armor_nova_west`, a ground click was
incorrectly promoted to an interaction because a floor change occurred after
six keyboard steps. An interrupted click-to-walk ends at the next keyboard
command's recorded starting position instead of adding an unvisited click target.
DBVClient_v5 IDs confirmed by the user are 4561 (ground) and 4560 (stairs).
Known ground clicks remain movement even when adjacent; stair IDs are not
substituted for ground IDs. Existing recording files are preserved.
The next live replay traversed all four recorded floor transitions (7→6→5→6→7).
Item 8467 remains a separate recorded interaction of unverified meaning; it
timed out and the route continued. Movement still uses ground-use requests for
server pathfinding; walk logs identify these as ground clicks with an item ID
and stack layer, rather than implying an inventory item was consumed.

The recorder accepts OTCv8 NEW_WALK (0x45) as well as legacy movement packets.
It validates the direction list and preserves each step from the packet's
origin, so a lagging server-position snapshot does not erase keyboard movement.
It excludes server events older than recording start and drains pending events
on stop. Confirmed nearby packet Z changes feed stair events into recording;
an approach remains exact even when recording stops immediately on the new floor.
These are requested steps, not proof every step succeeded; blocked
or interrupted paths still require comparison with actual movement. Synthetic
tests cover the format; a fresh recording in this client needs live validation.

Nearest-point selection is used for initial entry/recovery. A subsequent loop
starts at the first node, preventing the observed rapid repetition of the last
two nodes. If the beginning is on another floor or more than eight tiles away,
playback stops with a request to record a return path. Existing recordings are
not silently closed or rewritten. The Assasin replay ended ten tiles from its
first node; this version will stop there instead of spinning at the tail.

## Passive map inspection

The separate **Pasywna mapa** panel reads an 18×14 grid on the current floor
four times per second. It is independent of the cavebot and never sends movement,
item use, packets, or client function calls. Clicking a cell only selects its
details; **Zatrzymaj obraz** freezes the displayed snapshot, not gameplay.
The older route minimap still represents recorded waypoints.

`file.dat` (1,415,686 bytes, SHA256
`26e6342c67378183dd422d3a044a7ef159e27ebfe2af1594fae8e0a35f200c91`) has a
nonstandard opaque disk format. It has not been decoded as a standard DAT file.
The panel instead reads the client's already-loaded ThingType definitions.
The catalogue contains 19,345 slots, not 19,345 necessarily valid items.
Loaded properties include ground, not-walkable, not-pathable and container.
The known stair ID 4560 and ground ID 4561 have the same relevant loaded flags:
the 4560 stair marker is explicitly based on the user's confirmation, not a
universal DAT rule. Stair ID 1956 was also confirmed at (115,57,8), above the
decorative/approach object 1957. Teleport IDs 11652 and 11941 were confirmed at
(74,79,7) and (75,79,7); underlying ground IDs are not classified as teleports.
Destinations and required interactions are unknown.

`map_navigation.py` classifies object types independently of tile collision.
The map preserves blocking/avoidance while displaying S/L/D/T/down-arrow symbols.
LensHelp attribute 29 is read as U16 at +4 only after checking the verified
holder vtable (RVA 0x8B6900, PE RTTI `stdext::any::holder<unsigned short>`).
The asset catalogue contains 485 LensHelp definitions, of which 328 match the
supported navigation groups: 6 ladders (1100), 2 descents/trapdoors (1101),
4 rope spots (1102), 301 door variants (1104/1105), 13 stairs (1106) and
2 covered holes (1110). These are **client hints**, shown with `?`, not verified
DBV server behavior. Codes use the author's
[DAT reader](https://blackdtools.com/tibiadat86.php); unsupported codes remain
unclassified. The known build has no attribute-252 floor-change entries.

The upper opening above ladder 14607 is ground item 14606 at (536,1166,5).
It has no LensHelp code, so it is explicitly classified as a hole. Client minimap
color 210 combined with not-moveable/not-pathable and absence of not-walkable
marks other unclassified objects only as **possible passages**, not proven holes,
stairs, or traversable tiles. This heuristic is based on the observed DBV
stairs/opening; it does not infer a destination or activate the object.

Confirmed types live in `map_navigation_types.json`, scoped to the asset hash.
The passive monitor checks the bounded data catalogue each sample and reloads
changed entries without restarting the proxy, actions or frontend. It validates
IDs and a fixed type enum; invalid/incompatible files discard confirmed labels.
Only the type name is consumed; provenance notes are never executed. Edits must
be based on observed objects, not neighboring IDs or unrelated Tibia catalogues.
Changed DAT assets disable confirmed ID labels until reviewed.

Confirmed IDs take precedence over generic help codes. Flags distinguish a
door's apparent open/closed state, but neither a lock hint nor absence of
blocking establishes level requirements, permissions, automatic closing or
teleport destination. Those properties need observation at the actual location;
they are not guessed or used for navigation. Open holes and custom variants
without metadata still need classification. Location-specific observations are
recorded separately as described below; they never relabel every item of an ID.

`passive_map_win32.py` opens only QUERY_INFORMATION | VM_READ and validates the
client executable SHA256 before using the version-specific layout. Reads are
bounded and performed in a separate thread. The verified MSVC map uses ordered
tree nodes, unlike the old DLL tile scanner's unordered-map assumption. Item
getId was checked in the executable and reads U16 at +0x35; flags come from the
loaded type's attribute-pointer vector. The client also stores server-supplied
tile speed and blocking, which are displayed separately. No memory addresses,
creature names, credentials, chat or packet payloads are exposed by the map API.

The legend distinguishes blocking, pathfinding avoidance, no ground and unknown
cells. Green means no decoded blocking flag, not guaranteed traversability.
Creatures are an overlay; their presence alone does not establish whether they
are passable. Missing/changed structures become unknown. Concurrent center/floor
changes discard the sample; stale samples are marked, and disconnect clears the
grid. Current build probes read all 252 cells in roughly 6 ms. Live Windows
validation confirmed the panel, updates during manual movement/floor changes,
cell selection with IDs/flags, and freezing/resuming only the displayed image.
The visible corridor and blocking layout were compared with the game. Other
stair IDs and reliable traversability still require live validation before any
use for adaptive navigation. The map does not change the existing zero-creature
DLL targeting state.

The map distinguishes player (G), NPC (N), monster (M), self and unknown creature.
Classification compares verified PE RTTI vtables for Player, LocalPlayer, Monster
and Npc after checking the existing isCreature accessor. It never invokes code,
reads names or infers categories from outfit or creature ID. Unrecognized classes
remain unknown; monster type alone does not establish hostility or ownership.
These markers do not populate or enable the separate DLL combat target list.

**Kopiuj dane pola** copies only selected tile coordinates, collision, item IDs,
flags, labels, occupant category counts and learned transition links. It uses the
system clipboard on an explicit click and never reads clipboard contents.

The asset catalogue also supports bounded location notes. A user-reported gate
of expertise at (986,609,7), ID 6264, requires level 320. This requirement applies
only to that coordinate and matching object, not every object sharing its ID.
The panel labels its source as the user's report; no opening or successful
crossing is inferred. Location notes reload alongside type definitions.

Right-click a tile (or use Shift+F10 / **Menu pola**) and choose its object type
to open a compact editor beside that tile. Select its layer/item ID, describe
its type,
optional activation method, required level (gates only) and a short note. These
user annotations are separate from observed successful transitions and do not
change collision flags or action settings. The authenticated, Origin-checked
`POST /api/map/annotation` validates the current profile, visible coordinate and
item against a fresh snapshot. Frozen/stale maps cannot be edited. Notes persist
atomically in ignored `local_data/map_notes-<profile>.json` (1000 notes/1 MiB),
separated by server and build/assets. Damaged or externally changed files are
not overwritten. Saved descriptions update on the next map sample; no session
restart is required. Removing a user note restores underlying client/catalogue
labels and preserves observed transitions.

Reference layouts: OTCv8
[tile properties](https://github.com/OTAcademy/otclientv8/blob/master/src/client/tile.h),
[item attributes](https://github.com/OTAcademy/otclientv8/blob/master/src/client/thingtype.h)
and [map storage](https://github.com/OTAcademy/otclientv8/blob/master/src/client/map.h).

## Passive transition learning

`transition_learning.py` correlates manual client movement/use metadata with
read-only map snapshots. The **Uczenie przejść** panel lists the last 20 variants:
position before, actual entrance, observed landing, step/use/use-with or
preparation-then-step, object/tool IDs, vertical direction and repeat count.
An observed location is also annotated in the passive map. It does not enable
cavebot, execute learned actions, or change saved action settings.

Only client-origin callbacks supply commands. Bot injection bypasses those
callbacks; learning is additionally suspended during either cavebot, auto-walk,
recording playback or a known dead player. Commands use a bounded queue and disk
I/O runs only on the map thread. Inventory potions, chat and packet payloads are
not retained. Formats follow OTCv8's
[movement and item commands](https://github.com/OTAcademy/otclientv8/blob/master/src/client/protocolgamesend.cpp),
plus the repository's older stack-only use-with format.

The learner stores bounded, atomic JSON in ignored `local_data/`, separated by
server endpoint, executable hash and asset hash. At most 2000 distinct variants
and 2 MiB are retained. Repeats update a count; a different exit is a separate
variant and receives a warning. Invalid or externally modified files are never
silently overwritten; storage failures are visible in the panel.

The default **Najbliższe wejścia** list selects from the entire saved profile,
not only the last 20 records. It prioritizes entrances on the current floor,
then other floors, leaving unknown entrances last. Distance is Chebyshev tile
distance, not a path through obstacles. **Ostatnio widziane** shows recency order.
Each row has a short ID, entrance item IDs, and **Pokaż pole na mapie** when its
entrance is visible. That button only selects/highlights the passive map cell.

This is observational evidence, not proof that an action is the only required
method. Each direction is learned separately. Floor changes and long jumps are
considered; short same-floor teleports need an already identified source. An
entrance/action is assigned only when the command and visible tile agree. A
second stable landing sample confirms the exit; moving immediately, multiple
commands or missing tile data can leave a review-only observation. Multi-step
click-to-walk commands are not guessed. Keep still briefly after a transition
for the clearest evidence. A use/tool followed by a step is linked only when
the targeted object visibly changed within six seconds. Door crossing requires
an observed open field, entry and subsequent manual exit. Level requirements,
automatic closing, hidden prerequisites and inverse routes are not inferred.

Disconnect/login, invalid map reads, queue overflow and gaps over one second
reset pending tracking. Persisted observations survive session restarts.
Synthetic tests exercise packet formats, floor changes, doors, tools, uncertainty,
session resets, suspension and persistence; these do not establish live accuracy.
The 2026-11-16 Windows session also exercised live passive recording: matched
step transitions, separate uncertain observations, repeated visits incrementing
counts, JSON persistence and dashboard display. Cavebots remained disabled and
the user's existing potion action continued. Use/tool/door sequences still need
their own live acceptance examples; offline coverage does not replace them.

## Dashboard and privacy changes

The compact graphite/plum dashboard uses four horizontal tabs: **Mapa**,
**Automatyka**, **Cavebot** and **Diagnostyka**. The transition list expands below
the map, while priority healing and additional actions share Automation.
Connection status, HP/Ki, position and the emergency
stop stay visible while navigating. Unlocking an emergency pause still does not
enable actions. Switching views preserves draft forms and returns the main
scroll area to the top. No global keyboard shortcuts were added.

The map has a separate tile inspector for copied data and local object notes.
Transition rows show entrance, exit, distance, evidence and repetitions together;
their map link selects the corresponding tile without moving the character.
Healing rows show a readable HP/Ki condition, item, variation and cooldowns.
Other actions have search/filter controls, explicit running/waiting/off states,
and compact rows with expandable descriptions and logs. Recording controls sit
above a separately scrollable route list. Connection details live in Diagnostics.
The Electron window defaults to 1000 by 740 with a minimum of 720 by 540;
layouts adapt to its width. The map uses the remaining window height, with an
independently scrollable inspector. No sidebar or large heading consumes space.

The redesign was checked in the Windows Electron production build with isolated
synthetic map/transition data, disabled actions and no game process attachment.
The preview is explicitly labelled **DEMO · BEZ GRY**. Adding a disabled Senzu
rule, changing its priority and saving the ordered list were exercised against
isolated settings; no action runner or game proxy was present. Right-click type
selection and Escape dismissal were also exercised. Tile selection, copying,
saving a local annotation and opening a transition on the map were exercised.
Emergency stop and unlock were also exercised against isolated settings; unlock
left every action disabled. Preview annotations use a separate local profile.
Electron security tests (4), TypeScript checking and the production build passed.
These checks do not validate
the updated game client, DLL offsets or live automation after a client update.

- Normal launches use a production Next.js build bound to `127.0.0.1:4747`.
- HTTP (`8089`) and WebSocket (`8090`) bind to loopback. HTTP requires a bearer
  token and validates Host and browser Origin. WebSocket requires the exact
  dashboard Origin and a token as its first message before sending any state.
- A fresh random token is generated per MCP process. It goes only to the launched
  Electron child through its environment, then to the trusted main frame via a
  narrow IPC bridge. It isn't embedded in the JavaScript bundle or put in URLs.
- Open the panel through `start_bot`. Ordinary browser access and separately
  launched Electron instances no longer receive a token and cannot control the
  bot. This is intentional. Restart the MCP session if you need a fresh token.
- Session keys are redacted from proxy logs. `packet_sniffer` logs only opcode and
  length by default. Raw diagnostic capture requires explicitly setting
  `DBV_ALLOW_PACKET_CAPTURE=1` before MCP startup; dumps can include private chat.
- These changes protect against unauthenticated local API calls and unrelated
  websites. They do not protect against malware already running as your Windows
  user, compromised trusted dependencies, or an incompatible game DLL.

Python direct dependencies and security-sensitive Node packages are pinned.
PostCSS, nanoid, source-map-js and sharp overrides keep the Next.js 15 dependency tree on patched versions; review these overrides when upgrading Next.js. Python transitive dependencies are not fully locked. Review updates regularly;
a clean dependency scan does not prove the whole program safe.

## Live object updates and tool spots

One unambiguous observation updates the current map session; further visits
increment the evidence count. Stable landing confirmation is a second sample,
not a requirement to traverse twice. Evidence remains local to the coordinate,
matching object IDs and executable/DAT/server profile. Replacing an object does
not transfer old confidence to a different ID. Uncertain events stay review-only.

Door use is tracked separately from crossing: an adjacent matching use followed
by an ID and blocking-flag change records opening without movement or movement
onto the gate. Current open/closed variants update on every sample. A subsequent
manual exit can record closure or remaining open for at least two seconds; these
are observations, not proof of permanent behavior or that no other player acted.
Unknown toggling objects remain passages, not inferred level gates. User notes
are applied before learning so manually identified doors are also recognized.

The current DAT profile includes user-reported Rope spot 9410, Shovel spot 606
and its opened hole 607; old-asset catalogues are kept separate. PPM permits
location-specific Rope spot and Shovel spot notes. Activation choices include
Rope 3003, fly up, either method, and Shovel 3457. AiO 8899, subsequently identified by the user, is selectable for both spot types.
Passive use-with metadata records which tool was actually observed.
The exact public spell `fly up` is recognized as a fixed navigation command only;
arbitrary chat/channel messages are ignored. Spell attribution requires a known
rope spot at the player's origin and an observed upward transition. Tool use,
changed object ID (for example 606 -> 607), subsequent step and landing are
recorded as one preparation sequence. No learned record sends any action or
starts cavebot.

## Automation overview, transient map reads and atlas

The main map view has compact cards for every enabled/running user action (DLL
is an internal service). A card remains available after switching it off in that
view. Global manual pause cancels user tasks, queued injections, walking and
attack/follow, while preserving selected actions and cooldown deadlines. Explicit
resume starts the still-selected actions; a paused cavebot route is checked
against the current floor/distance before restarting from a nearby route point.
Route continuation is not restored across a disconnect. Emergency STOP clears
all selections and requires separate unlocking/re-enabling as before. Manual
pause persists across restarts and cannot be bypassed by an action toggle.

Object annotations now expose type-specific defaults: stairs/hole/teleport ->
step, ladder -> use, doors/gates -> check state and open only if closed,
rope spot -> Rope/AiO/fly up, covered hole -> Shovel/AiO. The server rejects
incompatible new combinations; legacy notes are normalized in memory on load.
These are declarative notes, not navigation execution.

A concurrent map-center/tree update retains a marked, recent image and retries
with the same read-only handle after 30 ms. Cached images cannot be annotated or
fed to learning; after two seconds without a fresh sample they expire. A real
disconnect immediately clears the image. This fixes the prior behavior that
closed the reader and waited 1.5 seconds on every concurrent movement snapshot.

The passive atlas saves visible known tiles, without occupants/names/chat, in
ignored local_data/map_atlas-<profile>.sqlite3, scoped by executable, DAT and
server. All observed tiles are stored on disk without a fixed tile/file cap; no automatic deletion
of old visited areas. Unknown/interrupted samples never overwrite known cells.
Static cell data is updated when changed, timestamps at most every 30 seconds.
Atlas obstacles/door states are historical, not a live traversability guarantee.

Atlas and route editing opens on demand from the Waypoints block. Users can sketch coarse anchors,
change floor, inspect a proposed path, or start/stop a normal passive recording
and generate a separate preview. The recording remains unchanged. A bounded
Dijkstra search traverses known clear cells without diagonal corner-cutting.
Directed learned transitions preserve entry/exit, tool and action. Unknown,
ambiguous or replaced transition objects and unknown tiles do not create edges.
Missing connections remain visible gaps; no inverse floor transition is invented.
Recorded floor and interaction anchors are retained while small walk wiggles
are simplified. Recorded item actions remain separately listed for review;
this preview is not an executable cavebot route. Plan JSON can be copied through
an explicit user click. Neither planning nor viewing enables actions.

The atlas follows live XY/Z by default. Panning, floor browsing and drawing
detach the view; **Śledź postać** reattaches it. Fresh cells from the same
profile overlay historical cells immediately with shared map symbols. Unknown
or stale samples cannot erase cached terrain. Inspection shows source, level,
latest door state, observation count and exits. Historical states are dated;
a saved open door is not guaranteed open now.

Version 2 recordings include a separate observed position trace from the existing
fresh client sampler. Requested movement/floor packets remain in legacy waypoints
but do not define the preview. Gaps and the 20,000-position limit require review.
Older recordings are labelled command-derived: predicted stair entrances are
replaced with their approaches only when learned edges match all three positions.
Non-pathable fields are costly rather than blocked and require review. Blocking
flags and unknown cells remain impassable.

Named sketches (2–2048 anchors, 100 per profile) persist in atlas SQLite through
authenticated, Origin-checked GET/POST /api/map/drafts. Opening a sketch replans
using current evidence. **Symuluj na atlasie** animates only a UI marker, including
transition actions and floor changes, ending at a missing connection. It sends
no game commands and does not establish server acceptance or gate eligibility.

## Cavebot Adaptacyjny V2

The atlas can be panned with Ctrl + left drag (or middle drag), zoomed with the
Shift + wheel, and expanded by hiding its tools. It fills the rectangular workspace with
square cells. Ordered contiguous floor segments distinguish e.g. 7 → 6 → 7;
all visits on the selected floor are visible with distinct numbers/colors. During recording, following selects the
latest segment. During simulation, the current segment changes with the marker.

Record one continuous walk across floors, including the return route when needed.
Stop recording, inspect each segment, name and save the sketch, and simulate one
lap. `Powtarzaj trasę` defaults on and is saved with the sketch. The planner must
respect explicit closure at the first anchor; it never appends a hidden return
leg or assumes stairs/teleports work in reverse. Old open sketches need closure
before repeated execution, or can run once with looping off.

**Start postaci** is an explicit navigation command, separate from preview and
recording. It requires a saved sketch in the current profile, a complete route,
no avoided fields and fresh player/map data. The current position may be farther
from A; Start plans a one-time approach through known atlas cells and observed
directed transitions. Only the main route repeats.
The executor confirms one step at a time, checks diagonal corners/occupants,
locally detours on visible clear cells and pauses when it cannot proceed.
Observed `step`, `use` and `use_then_step` edges are supported: entrance ID, live
occupancy and known level requirements are checked; normal doors must confirm
open before walking. Rope/Shovel/AiO and spell transitions can be previewed but
are rejected for execution in this first version. An attempted transition is
never blindly retried. Single-step movement currently uses legacy walk opcodes;
server acceptance on this client remains a live acceptance test.

V2 alone has Pause/Resume/Stop. Manual movement or world-item interaction pauses
it. Death, invalid HP, stale telemetry,
profile change or an unexpected position also stop navigation; healing remains
independent. Pending navigation packets have a short expiry and an owner guard
that is invalidated by pause, even if immediately resumed. Emergency STOP clears
the route as well as cancelling actions. Login/disconnect never resumes movement.
Both historical engines are disabled through launch, toggle, restart and startup
paths; their recordings remain available for conversion. No new combat, target
selection or luring is active.

Next V2 modules should share this single movement owner: monster-only target
filters with priority/avoid lists; combat in place or while moving; explicit lure
points with creature count, maximum time and escape/HP limits; recovery to the
route; then separately tested Rope/Shovel/AiO executors. No NPC/player targeting
or automatic enablement should be introduced by those modules. Updated-client
legacy DLL targeting is unavailable; combat needs its own validated transport.

## Atlas navigation refinement

The whole route is now shown: all visits on the current floor have numbered
A/B endpoints, separate colors and slightly offset lanes where paths overlap.
Clickable T1/T2 labels pair entrances and exits. Small floor diagrams show the
entire multi-floor route; clicking one frames all its segments. An incomplete
plan retains its valid prefix as solid lines and shows unresolved sketch legs
as dashed hints, never as an executable route. Default editing places numbered anchors by clicking; anchors
can be dragged or removed with right-click. Freehand remains an option. Overview
cards are hidden while editing so adding the first point does not move the map
under the pointer; the whole-route button exits editing and restores the cards.

The floor selector always exposes Z0–Z15, including unexplored floors. Minus/up
means Z-1; plus/down means Z+1. The current view floor and player floor are shown
separately. Ctrl+wheel changes floors, Shift+wheel zooms; ordinary wheel does not
zoom. Ctrl+drag and the pan tool continue to move the map.

V2 now polls acknowledged position every 10 ms and requests map refresh every
50 ms only while its task is active (250 ms otherwise). Acknowledged single-step
walking uses a 40 ms minimum packet interval instead of the old 200 ms interval;
item interactions retain 200 ms. Freshness, occupancy, diagonal-corner checks,
queue-owner guards and one outstanding step are unchanged. This removes imposed
client-side waits; actual speed remains limited by server movement/response time.
The user confirmed a live loop and successful recording with the earlier V2;
smoother pacing still needs comparison in the updated session.

## Explicit closure, editing and persistent navigation controls

Looping no longer adds an automatic last-to-first leg to a plan. A repeated
route must explicitly end on its first anchor. Open sketches can be saved and
previewed; the UI and executor reject starting them as a loop. Click the first
point again (without dragging) or choose `Domknij do A` to add that leg. Dragging
the start of a closed sketch moves both joined endpoints. Right-click deletes
a point. Clicking a planned line inserts an anchor into the corresponding leg;
planner-provided path spans preserve the ordering across floors and repeated
visits. Overlapping exact lines select the topmost leg in the drawing.

Zoom accepts Shift-wheel events in either axis (Chromium may supply deltaX),
has explicit +/- buttons and scales by the longest viewport dimension. It no
longer stalls at a hidden width cap on wide/short maps. Lost pointer capture
clears drag state so zoom does not remain blocked after an interrupted drag.

A persistent V2 strip outside the scroll area is visible across all tabs while
a route runs or is paused. Pause, Resume and Stop affect V2 only; the existing
emergency STOP still disables all actions. Synthetic tests verify that a newly
occupied ordinary waypoint can be bypassed locally; an occupied final waypoint
pauses instead of claiming completion. Local search rejoins within the next
eight path cells, uses only fresh visible clear terrain and never skips a
required transition. It does not push objects, fight creatures or retry a
blocked transition indefinitely.

## Editing transitions, undo and approach to A

In point editing, clicking stairs/doors/other navigation objects opens a chooser
of observed, unambiguous exits matching the current atlas item IDs. The atlas
GET response includes `route_transitions` from the same validated edge builder
as planning. Selecting an exit adds the approach origin and destination, not an
ordinary anchor on the trigger tile. A trailing mistaken trigger anchor is
replaced. The plan must actually use that selected edge before the change is
accepted; the view then moves to the exit floor. No movement is sent. Unknown
exits require manual observation and are never invented from the DAT symbol.

Ctrl+Z or the Undo button restores the previous sketch and viewed floor for
adds, insertion, dragging, deletion and transition selection (up to 40 edits).
Text fields keep their native undo behavior. Loading a different sketch or a
profile clears history. Invalid plans retain the valid prefix visibly; only
unresolved legs are dashed. This avoids the disappearing-lines failure caused
by treating sparse anchors as adjacent map cells.

Explicit Start now plans the one-time approach from the current position to A
through the known atlas, including supported observed floor transitions. It
requires a fresh local map, applies the same live obstacle checks while moving,
rejects unknown paths/avoid fields/unsupported tool transitions, and rejoins A
before the first lap. The approach is excluded from subsequent laps. There is
no two-cell distance restriction. A 2–4-cell navigation corridor with tolerant
ordinary waypoints is a proposed future mode, not part of this implementation;
precision at transitions remains mandatory.


## V2 configuration workspace and combat

The main V2 page contains saved route selection, compact scrollable waypoints,
recording, Start/Pause/Stop and combat configuration. The Map navigation tab is
removed. The default V2 view does not mount the map SVG or request atlas tiles.
Open Map in Waypoints mounts an optional editor overlay; floor/zoom controls live
inside it. Closing it stops atlas polling. Passive collection needed by runtime
navigation/combat continues. Object annotations remain available on demand via
Objects in that editor. An optional separate sandboxed Electron window uses
the fixed local `/route-editor` path. Only windows
created by the main process can receive the session token, and only their main
frames. External navigation, popups and permissions remain denied. The editor
contains drawing, floor navigation, recording and marker-only simulation; no
Start button. A second open request focuses the existing editor without losing
its unsaved work. Saved edits appear in the main window automatically.

Drafts have additive SQLite `combat` and `deleted` columns. Existing routes load
with combat OFF. Deleting is reversible through the removed-sketch list. Only
the active profile may edit/delete/restore its records; a running/paused route
must be stopped before its stored definition changes. Editor saves preserve
combat configuration. Configuration saves change only combat and loop options.
Neither operation enables actions. The HP movement threshold was removed from
V2's UI and Start path as requested; death and invalid/stale telemetry still
stop commands. Healing is independent.

Combat rules use case-insensitive names with only `*` as a wildcard. The first
matching row chooses attack/ignore and numeric priority (0–100, higher first); within equal priority the
strategy is nearest, lowest HP, highest HP or finish current target. A target
requires a fresh DLL creature, a unique identity at that tile, and a fresh map
tile occupied only by one confirmed monster. Players, NPCs, ambiguous identities
and stale observations are excluded. The preview button evaluates these rules
without sending any command.

Fight in place consumes route iterations. Chase uses the same movement owner,
visible walkable cells and a bounded radius, then returns to its original route
anchor. Lure attacks/casts while advancing along the route, waiting when the
farthest confirmed follower reaches the configured SQM gap (before confirmation,
it waits for the nearest candidate). At the desired follower
count (default 4), time limit, transition or route end it fights in place until
the group is gone. No observed combat progress for 20 seconds pauses V2. Manual movement or
targeting yields control. Pause/Stop invalidate queued commands and cancel the
owned server target. Separate legacy targeting/combat actions cannot coexist
with an armed V2 combat controller.

Spells are explicit user-configured words with cooldown, minimum Ki, range and
minimum eligible monster count. The first eligible spell is requested; there is
no inferred server cooldown or acceptance. Attack uses the existing proxy packet
builder and never calls unsupported DLL targeting hooks. The V2 panel marks its
chosen target; the client's red target square and actual server combat/spell
acceptance remain UNTESTED until an explicit live test. Offline tests use only
synthetic creatures/maps/transport; they do not establish game acceptance.

## Recording, labels, review and combat-only mode

Recording starts/stops in Waypoints without loading a map. Stop proposes the
unoptimized observed path, preserving turns, backtracking and transition
anchors. Optimization is optional: the original remains untouched until Accept;
Reject discards the proposal and Undo Optimization restores the prior plan.
Fast simulation analyses the full path immediately, with any marker animation
bounded to 60 frames. It does not exercise the game server.

Each waypoint can have a 60-character label stored in an additive SQLite column.
Labels survive reopening, route combat changes and reversible deletion. Named
anchors are retained during optimization. Point edits/undo carry their labels.
Concurrent editor saves use expected_updated to reject stale overwrites.

V2 has one Run button and a default-on "Move along route" preference. Movement
is effective only when enabled and a saved route has at least two waypoints;
otherwise Run starts combat-only. The waypoint editor stays available in both
cases. Toggling movement never starts/stops actions or switches combat settings.
With a selected route, its monster/spell settings also apply with movement off;
without a route the standalone saved combat profile is used. Combat must be
enabled with attack rules before a combat-only Run. Running/paused controllers
lock the movement preference until stopped. Unsaved edits still block Run.

Combat-only does not need a
route, never sends movement/use/stop-walk commands, and permits manual movement.
Manual target/cancel yields to the user. It shares the V2 Pause/Stop and emergency
stop controls. Neither selecting this mode nor saving it starts actions.
Target engagement can be visible-screen or within X SQM (Chebyshev, 1 includes
diagonal adjacency). Spells independently select visible-screen or distance
conditions; target eligibility and server spell range/cooldowns still apply.
Preview parses the control API envelope before rendering; it never attacks.

Waypoint actions use one compact icon strip: map, recording, save, simulation,
repeat and an overflow menu. New/rename/delete, separate-window editing and
optimization remain in the overflow menu. The map has its own integrated icon
strip for floors, zoom, tracking, panning, drawing and undo. Icon controls retain
accessible names, hover titles, disabled states and pressed/recording feedback.

## Per-monster combat overrides

New monster rows default to select-and-attack. Saving a row/profile does not
enable combat. The first matching pattern still wins; numeric priority decides
between eligible monsters. A gear button expands one row's movement and spell
settings. Missing fields in old profiles inherit the route movement and shared
spells. Overrides can stand, chase, lure or maintain a distance, independently
of using shared spells, no spells or one explicitly configured spell. Custom
spells replace the shared list for that target; they do not run both lists.
Cooldowns are keyed by normalized spell words across all targets and rules.

Ranged stance accepts 2–7 SQM and aims for a distance band of N to N+1 (max 7).
Below N it cancels the owned attack and withholds spells. With movement enabled
it uses only fresh walkable cells, stays within the route anchor radius, checks
corners/occupants, avoids transitions and revalidates the monster before each
queued step. No available retreat pauses V2. The controller returns to the
route anchor after the group is gone. This does not select a ranged weapon,
change the client's server chase mode, infer projectile line of sight or prove
that a server accepted an attack. Those require a separately authorized live test.

With movement off (or no route), every per-monster stance remains movement-free:
chase/lure only select/attack/cast, ranged waits for the user to create the gap.
The UI explains this in the row. No tests start the real bot or enable actions.

## Local spell picker

Shared combat spells and per-monster custom spells use a searchable local catalogue
with static client UI icons. Selecting a different spell copies its words and
client exhaustion in seconds (clamped to the executor's 0.2–120 s range), preserving
trigger, Ki threshold, range and count. CD remains editable; reopening/reselecting
the current spell and saving/reloading preserve an override. The reset arrow restores
its catalogue value. Client metadata is a starting value, not verified server CD.
Selection edits configuration only; it does not save, enable combat or start V2.

`dashboard/public/local-spells/catalog.json` and `icons/<item-id>.png` are local client
assets ignored by Git. The catalogue has a `spells` array with id, name, words,
description, category, icon, cooldown_client_ms, level, ki, ki_percent, aggressive
and unavailable fields. Icon URLs must be null or `/local-spells/icons/<item-id>.png`.
Parameter-dependent spells and tool entries are visible but cannot be selected.
Missing graphics show a fallback symbol; a missing catalogue offers Retry while
keeping existing configured spells/CD intact. Unit tests use synthetic entries and
do not depend on proprietary local graphics. Re-export the local catalogue after a
client update before treating it as current.

The UI uses the same type-item icon mapping as the client action bar: optional
`typesid`, otherwise `spellTypes[types].itemid`. These 32×32 sprites come from the
client DAT/SPR, not the animated `/images/spell/` effect previews. Several spells
share one type icon, just as in the client selector.


## Confirmed pursuit during lure

`PursuitTracker` counts fresh, uniquely identified eligible monsters only after
movement toward the player in response to two distinct player steps away. A
stationary visible creature, repeated snapshots or one approach do not suffice.
Evidence is scoped to identity and floor; lost/dead/excluded/stale observations,
teleports, gaps over two seconds, pause and stop discard it. Pursuit without
renewed movement expires after three seconds; an already confirmed adjacent
attacker can stand while the player waits. This is movement-based evidence, not
a server aggro-target field, so incidental coordinated movement can still look
like pursuit. Bounded traces cover delayed movement, diagonal turns and resets.

Reaching the follower goal latches stationary combat until all eligible targets
are gone; killing one does not resume the lure. Attack and configured spells
continue while gathering, waiting and fighting. A timeout or transition stops
movement separately and is never reported as reaching the group goal. The panel
shows the current confirmed count, progress dots and the stop reason. Saving
settings never enables V2. No live combat was used to validate this change.

Spell inputs now say `Odstęp (s)`: this is the configured interval between cast
attempts, initially copied from client `exhaustion`, not a measured server CD.
The picker states that server cooldown is unconfirmed. Existing saved values are
preserved. Per-spell cooldowns remain shared between monster rules. The common spell interval
now lasts for the configured interval of the last queued spell (see below).

## Spell conditions and independent Atlas access

Spell rows are ordered priorities: the first available row wins. Comparison
operators (`>`, `>=`, `=`, `<=`, `<`) apply to HP, Ki, monster count and distance.
HP is optional; HP/Ki accept either percentage or absolute points. In the client's
percentage display mode (`max=100`) absolute conditions wait with an explanation;
they must not interpret percentages as points. The read-only stat reader accepts
integral raw resource values up to signed 32-bit maximum, retaining identity,
atomic snapshot and freshness checks. No alternate unverified offsets are used.

Catalogue Area spells default to checking the area around the player. Their
monster count is independent of the selected target's distance. Targeted spells
add a distance check on that target. Counts include only eligible, fresh visible
monsters matching the combat profile. The shared interval lasts for the last
queued spell's configured interval so a fallback spell cannot spam during that
interval; it is not a measured or server-confirmed cooldown. Passive target
preview and live diagnostics show counts, priority and failed conditions.

Changes to a combat target or conditions while waiting to send skip that attempt
and re-evaluate next tick. They no longer pause all combat as a navigation error.
Global disconnection, death, emergency pause, stale state and movement guards
remain in force. A queued attempt is not proof of server acceptance. The updated
read-only observer cannot call local `g_game.attack`, so proxy attacks do not
provide the client's red selection square; no legacy targeting hook is enabled.

The global Atlas button opens saved atlas/live map browsing from any workspace.
Closing it unmounts the viewer and stops its panel requests. This does not stop
the existing passive map collector. Cavebot waypoint/editor access is preserved.

## Directional spells and manual targets (prepared 2026-11-16)

Wave spells use a separate directional footprint and projectile-wall check.
Their count is restricted to eligible monsters inside that footprint; a visible
target or an increased range condition cannot expand it. The client catalogue
and official profession page specify category/cost/exhaustion, not a server hit
mask: https://dbv.dbvictory.eu/spell-zamasu.html . Shinretsuha therefore currently
uses only one adjacent axial cell as a conservative prediction. Its full width
and length remain unverified. Other Wave spells without a reviewed profile are
blocked with a diagnostic reason. Do not describe this as measured server damage.

In the pinned updated executable, the Creature getDirection binding at RVA
0xC7554 points to RVA 0x72B40 (`mov eax,[ecx+0x58]; ret`). Game::turn at RVA
0x951F0 emits the existing single-byte cardinal opcodes 0x6F through 0x72.
The read-only player sampler now includes optional facing; unstable direction
does not invalidate HP/Ki. Combat sends a turn without stepping, waits for a
fresh matching facing snapshot and rechecks player position, target, footprint,
walls and lifetime in the cast queue guard. Unacknowledged turns retry at most
once per second and do not reserve spell cooldown or pause combat.

A positive manual ATTACK selects/pins that eligible monster and expires pending
bot requests without pausing V2. The pin is released after the target dies or
leaves fresh observations. An excluded manual target suppresses automatic target
changes while it remains present. Cancel/escape retains its explicit pause
semantics. This does not add the client's native red square for proxy-selected
targets; the observer is still read-only and no unverified local attack call is
introduced. Native targeting synchronization still needs a separate integration.

## Gate evidence and atlas editing (prepared 2026-11-16)

An observed `use_enters` change is a directed use edge from the recorded approach
to the object's tile. Existing observations work immediately; opening in place,
closing after exit and appearance-only hints are not promoted into use edges.
Closed/open IDs must match a unique observed pair. Preview, editor and execution
share those edges. An already-open paired gate is entered by a step, not used to
close it. No reverse approach or level requirement is inferred. A waypoint on
the entered gate can be followed by ordinary movement out of it.

The global atlas and editor expose the shared tile menu. Its portal is above map
overlays. In point-editing mode, right-click on an existing waypoint still removes
that waypoint; elsewhere it opens object information/annotations. Historical
cells remain read-only. An annotation is not a command to open/close a game door.
The main waypoint section exposes Discard sketch outside the overflow menu.

These prepared changes require a coordinated session restart. Offline validation
does not establish live turn acknowledgment, spell hits or gate execution.


## Lure reachability, capacity and movement waits (2026-11-17)

Combat candidates now require a path on fresh visible same-floor terrain.
Closed doors, walls, unknown cells and implicit floor transitions separate groups;
open doors reconnect them. Creature occupancy does not sever terrain connectivity.
This filter also protects spell counts, pursuit tracking and queued target guards.
Lure capacity counts the union of confirmed followers and eligible adjacent mobs,
so four creatures already in melee range stop gathering before pursuit evidence
arrives. The UI calls this Group, not a claim about server aggro ownership.
A brief clear check at transitions prevents crossing on one empty identity frame.

A changed ordinary step is dropped and re-evaluated instead of pausing combat.
While a local obstacle prevents a detour, combat continues and movement retries
after fresh checks. Unknown transition outcomes hold navigation without repeating
use commands; already authorized combat continues stationary, and an explicit
Check passage again can retry after validation. Disconnect, stale vital state,
manual Stop/Pause and emergency guards still stop automation. No action is enabled
by installing these changes.

Loop is an execution preference saved immediately for an existing idle draft.
It does not dirty the sketch or save unsaved geometry. Concurrent combat/geometry
saves preserve the latest stored loop preference. Changing loop while executing
is disabled because its return path must be replanned. Combat-only saves preserve
the stored navigation identity and stopped progress; users can discard progress
independently of saved route content.

## Dispatch lure guards, local recovery and native selection (2026-11-17)

This package remains staged until a coordinated logout/session switch. It does
not enable actions, alter saved combat rules, or change the dashboard build.

Lure capacity is checked again after awaited attacks/spells and immediately
before dispatching route commands. Adjacent matching monsters and confirmed
followers count once each; fresh physical monster occupancy covers temporary
identity gaps without authorizing attacks on unknown identities. A reached limit
latches stationary combat until a brief empty-group confirmation. Open doors
also mark room exits: finish eligible reachable monsters before crossing, even
when the room contains fewer than the configured group limit. Walls and closed
doors still separate reachable groups. This is not proof of server aggro.

Ordinary manual movement, a nearby push and temporary route contention no
longer stop the combat owner. A brief manual-motion grace period is followed by
replanning to the current leg through fresh visible terrain. The local recovery
limit remains 6 SQM on the same floor; an unexpected floor change or larger jump
requires inspection. User Stop/Pause, cancel attack, emergency, disconnect, death
and stale authoritative state remain stop conditions. Manual world-object use
still requires inspection rather than assuming an unknown transition succeeded.

Queued ordinary steps rejected by their dispatch guard are re-evaluated without
blacklisting the destination. An unacknowledged sent step has a one-second retry
backoff. Movement waits expose origin, target, waypoint, item IDs, occupants and
cause in the runtime snapshot and rate-limited local logs, never raw packets or
session keys. Old logs cannot retrospectively distinguish these causes.

Long ordinary legs may use a bounded alternative bend within 2 SQM of the planned
leg and at most two steps longer than the shortest visible route. A chosen local
segment remains stable while traversed. Waypoint order, checkpoint identity,
explicit transition entries/exits and object actions remain exact. Unknown
cells, occupied cells and implicit floor transitions remain excluded. This is a
navigation variation feature, not a claim of reduced detection.

The separate 32-bit `dbvobserver_target_20261117.dll` uses the pinned executable
profile (SHA256 8c15e00a00c9f68c05291fbe0bb6669c362c780fe901f2580299db999a54f5e8).
The ordinary observer and generic read-only command denial remain intact. A new
bounded capability mirrors an already dispatched proxy ATTACK into native client
selection using Game::setAttackingCreature, not Game::attack. It sends no second
attack packet. The Game shared_ptr and setter ABI are checked against the current
client; requests run only at the reviewed game-loop PeekMessageA boundary, never
from the pipe or WndProc thread. An expected-current-target guard, sequence and
350 ms lifetime protect newer manual selections; only fresh same-floor living
monsters may be selected, and only owned targets may be cleared. Hook mismatch
disables synchronization while observation continues. The module is pinned until
client exit; further native-hook replacement requires restarting the client.

Offline validation covers dispatch cancellation, lure races, room exits, local
pushes, reachability, bounded route variation, fixed pipe protocol and native
target guards in a synthetic 32-bit process. Compilation and synthetic tests do
not establish gameplay compatibility. Native highlighting and live behavior
must be checked after the coordinated switch; do not report them as verified
before that check.
