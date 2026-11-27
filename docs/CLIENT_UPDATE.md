# Client compatibility: 2026-11-26

This compatibility update was derived from the local executable on
2026-11-26. Static evidence and synthetic tests are available. A later Windows
read-only check confirmed HP/Ki and the protocol object. The operator opened
the dashboard and logged in, exposing the handshake failure described below.
After installing v1.38.3 and the requested fresh-login test, the operator
reported on 2026-11-26: **“Its working perfectly.”** This records user acceptance
of the reported connection/HP/Ki observation repair. Native highlighting,
individual automation features and recovery were not separately measured.
No game actions or reconnects were performed by the agent.

For future builds, follow the [client-update maintenance guide](CLIENT_UPDATE_GUIDE.md).
This file retains the exact November 26 evidence and historical test results.

Executable SHA-256:
`af43383076c0abc7192b407210efc54da6b6cbaf4ff8a2aa18f22a7f7d7486e4`.
It is x86 PE32 with preferred image base `0x400000`. Runtime readers continue
using the loaded module base. DAT SHA-256 remains
`cc6d91160218e7565ab1247d19ae483485dedaf6a18e24a05978ef31badb3669`.

## What changed

`dbv/client_bridge/client_profiles.py` preserves both previous profiles and adds `NOVEMBER_26`.
Player readers take their layout from the selected profile. Startup, the
bridge and injection all select `dbvobserver_marker_20261126.dll` and the
`dbvbot_marker_20261126` pipe for this build. Older clients retain the previous
marker DLL and pipe. Unknown hashes still fail before session teardown,
socket closure, memory patches or injection.

No production dependency, saved configuration, action enable flag, route,
recording or learned observation is changed. The existing executable/DAT/server
scope rules remain in effect. Network framing and relay destinations are unchanged;
the November 26 game proxy now uses the verified client's protocol key as
described below. Older clients retain their existing RSA path.
The launcher's existing game port remains 7475. The fresh-login outcome was
confirmed by the operator after the protocol correction below.

## Static evidence

Addresses below are RVAs unless explicitly described as object fields. Analysis
followed MSVC RTTI, Lua registration references, getters and constructors; no
live memory dump or authenticated packet capture was used.

| Subject | New address/layout | Evidence |
| --- | --- | --- |
| Map blocks / center | `B79E60` / `B7A00C` | `getCentralPosition` binding at `BF2B8`, getter `BCA10` reads map `+1AC`; `getTile` at `17D0D0` preserves the floor/tree/block layout |
| Item types | `B7A4B0` | `getThingType` binding at `BDD9E`, implementation `1DBAF0` |
| Game / containers | `B79B50`, object field `+2C` | `getContainers` binding `C3D54`, implementation `BB8E0` |
| Player / self vtables | `8BE824` / `8BE900` | RTTI `Player` / `LocalPlayer` and constructor assignments |
| Monster / NPC vtables | `8C4C3C` / `8C4CFC` | RTTI `Monster` / `Npc` |
| Tile / item / type vtables | `8C4F4C` / `8BE72C` / `8C4F18` | RTTI `Tile`, `Item`, `ThingType` |
| U16 attribute holder | `8BE7B0` | RTTI `stdext::any::holder<unsigned short>` |
| isCreature | `6D6C0` | Constant true accessor, compared but never executed |
| HP / max HP | object `+558` / `+560`, doubles | Lua-bound getters `BB3C0` / `BB3D0` |
| Ki / stored max Ki | object `+590` / `+598`, doubles | `73B40` / `73B50`; the native max getter returns max(current, stored maximum), while the bot retains its existing raw-maximum semantics |
| Level / magic level | object `+580` / `+5A0`, doubles | Getters `BB410` / `BB430` |
| Condition flags | object `+548`, U32 | Getter `BB310` |
| Inventory | object `+484`, shared-pointer slots | Getter `BB4A0`; supported equipment slots remain 1–10 |
| Direction / speed | object `+58` / `+D4` | Getters `737D0` / `BAEC0`; unchanged field widths |
| Creature ID / health | object `+38` U32 / `+54` **byte** | Getters `6D3E0` / `BAE30`; neighboring bytes cannot be interpreted as health |
| Creature name / native target | object `+3C` string / Game `+14` shared pointer | Lua-bound getters `BADD0` / `BBC10`; read only |
| Item ID | object `+35`, U16 | Virtual getter `ADB60` |
| Visual square | visible `+15D`, RGBA `+13C` | Entire setters at `BA920` / `BA960` verified before calling |
| PeekMessageA boundary | IAT `7AE3A4`, sites `405455` / `405480` | Imported function and both native message-loop call sites |

The marker retains its existing manual-selection, expiry, ownership and
signature checks. It changes only the visual square, not the Game attack
target, Lua state or packet stream. A signature mismatch leaves observation
available without enabling the marker. Actual Windows hook behavior is pending.

## Repeatable offline checks

From the bot directory, without starting the launcher or game:

```powershell
.\venv\Scripts\python.exe tools/check_client_build.py 'C:\DBVClient_v5\dbvStart.exe' --dll '.\dll\dbvobserver_marker_20261126.dll'
.\venv\Scripts\python.exe -m pytest -q
```

`tools/check_client_build.py` uses only Python built-ins. It checks the exact digest,
PE architecture, DAT digest, 35 code/type signatures for this build and the
selected DLL's PE architecture and pipe name. It does not attach, write process
memory, connect to a server, approve unknown hashes or discover replacement
offsets automatically. A successful result is static evidence, not gameplay
acceptance. Older known builds receive identity/DAT checks without the new
build's signature checks.

Build with the existing 32-bit MinGW toolchain:

```powershell
.\build_dll.ps1 -ClientVersion 20261126
```

`-ClientVersion 20261116` builds the prior marker observer; the default is
`20261126`. The first (legacy) client is no longer supported. Build errors now return failure. No agent or
game elevation is required for compilation.

The matching synthetic native tests can be compiled as follows and executed
on Windows without a game process:

```powershell
& 'C:\mingw32\bin\g++.exe' -static -O2 -std=c++17 -DDBV_CLIENT_20261126 dll/test_target_marker.cpp -o test_target_marker.exe -luser32 -lkernel32
& 'C:\mingw32\bin\g++.exe' -static -O2 -std=c++17 -DDBV_CLIENT_20261126 dll/test_marker_observer.cpp -o test_marker_observer.exe -luser32 -lkernel32
.\test_target_marker.exe
.\test_marker_observer.exe
```

Cross-compilation on Linux succeeded for both supported observer layouts and
both synthetic native executables. Those Windows executables were not executed
here. The delivered November 26 DLL was built with GCC 13.2, 32-bit MinGW,
`-shared -static -s -O2 -std=c++17 -DDBV_CLIENT_20261126 -luser32 -lkernel32`.
Its SHA-256 is
`82e6ce9431171506fd0bc5f41680b55abb77452927454f504ecd1a1b6dc22103`.
Rebuilding may change the binary digest due to compiler and linker metadata.

## Initial v1.38.1 offline results

Validation used Python 3.12.3 in an isolated Linux environment; no packages were
changed in the installed Windows environment.

- `python tools/check_client_build.py <client> --dll <new DLL>`: 31 static checks,
  executable/DAT identity and DLL architecture/pipe checks passed.
- `python -m pytest -q`: **1749 passed, 23 skipped, 1 failed** in 104.50 seconds.
  The failure was
  `test_mcp_local_transport.py::test_data_keeps_flowing_after_controller_error_and_before_replacement`.
  The same test fails on the unchanged source at `aca1dcf` in this environment.
  It remains unresolved; this is not a fully passing suite.
- Earlier full runs also had intermittent combat freshness failures in
  `test_per_monster_movement_override_cannot_move_combat_only[lure]` and
  `test_shunkan_priority_and_shinretsuha_fallback_on_diagonal[True]`. Both passed
  in the final complete run. The corresponding unchanged-source combat checks
  passed separately (20 tests).
- Both observer layouts and both synthetic native test executables compiled
  with 32-bit MinGW. Execution of those native tests is still pending on Windows.
- Targeted Python compilation, release-note JSON parsing and `git diff --check`
  passed.

## Protocol startup correction in v1.38.3

The operator's November 26 session forwarded packets but never activated the
decoded session: the 137-byte, checksummed game-login envelope (opcode 10,
OS 10, version 372) failed the default-RSA decoder. Forwarded packet counters
alone therefore did not establish working observation. A separate bounded
Windows read returned valid HP/Ki while the dashboard had no decoded session.

For this exact executable only, `dbv/client_bridge/client_protocol.py` reads the existing native
session key. Static Lua bindings identify Game::getProtocolGame at RVA `BBD20`
and its shared pointer at Game `+24`; ProtocolGame RTTI gives vtable `8BE5FC`.
Protocol::setXteaKey at `3ECC40` and getXteaKey at `3ECC60` agree on four U32
fields at `+18..+24`. enableXteaEncryption at `1C9A10` sets byte `+3E`.
Read-only Windows inspection confirmed the type, enabled flag and stable key
availability. No key bytes, credentials or packet payloads were recorded.

The reader verifies the executable profile, selected PID, accessor signatures,
vtable and encryption flag, then rereads the key, identity and shared pointer
to reject changes during the read. Its process handle has QUERY/VM_READ rights
only and is closed after each attempt. Keys are not included in status or logs.

The proxy begins confirmation only after the exact known client login envelope.
It requires two consecutive server frames with the same freshly read key,
valid checksums, bounded decrypted lengths and recognized opcodes. Login error,
wait and update responses cannot establish the session. Only the first cipher
block is inspected on the relay loop, after forwarding. Both initial frames
are retained in memory for the ordinary observer (at most two 65535-byte
frames), so confirming the key does not discard the initial player/map data.
Eight failed attempts stop confirmation for that connection while forwarding
continues without activating automation. Disconnect/replacement clears all
pending confirmations. Older profiles keep their previous RSA behavior, with
the unconditional packet-hex warning removed.

This correction needs a fresh launcher/game connection. It is not injected
into an existing authenticated session. The operator subsequently confirmed
the fresh-login result as described above. `python -m pytest -q tests/test_client_protocol.py` exercises
synthetic keys, mismatches, torn reads, rejected logins, relay order, initial
frame delivery, and redaction. The current offline build checker has 35 checks.

Validation on Windows used the repaired Python 3.12.14 environment from an
isolated candidate directory, without starting another bot or game connection:

- `python -m pytest -q tests/test_client_protocol.py tests/test_packet_observer.py tests/test_relogin_identity.py tests/test_automation_safety.py tests/test_client_november_26.py`:
  **122 passed**.
- `python tools/check_client_build.py <client> --dll <installed DLL>`: **35 checks
  passed**, with matching executable/DAT identity and DLL architecture/pipe.
- The actual `read_live_protocol_key` adapter successfully read a stable key
  from the operator's running client using read-only access. Only success flags
  were output; the key was neither displayed nor saved.
- `node --test tests/desktop_launcher.test.cjs`: **10 passed** on Node.js 22.
- Linux `python -m pytest -q`: **1767 passed, 23 skipped, 2 failed** in the
  final complete run (106.96 seconds). The previously reproduced baseline
  failure in `test_data_keeps_flowing_after_controller_error_and_before_replacement`
  remains. `test_occupied_final_waypoint_waits_instead_of_claiming_completion`
  timed out in that run and passed separately. An earlier complete run had
  the same totals with `test_route_completion_keeps_enabled_combat_worker_alive`
  as the timing failure; all 13 automation-domain tests subsequently passed.
  These cavebot/domain files are unchanged from `aca1dcf`. The full suite is
  **not fully passing**; timing-test reliability remains unresolved.

The Python-only v1.38.3 correction can use the normal release queue. To try it,
log out and close the game, then choose **Zakończ bota** from the bot's Windows
tray menu. Closing the dashboard with X only hides it. Reopen
`DBVictory Launcher.exe`, install the queued update and check for v1.38.3.
Use Start, log in to the character list, click **Połącz z grą**, and enter the
game after the bot panel opens. Check session/observation status and HP/Ki
before enabling automation. A queued update does not change the running process.

## Windows acceptance and installation

Native updates are a separate maintenance operation: the normal release queue
intentionally rejects DLL/source changes. Keep that restriction. Close the
client and launcher before installing the matching source and DLL. Existing
recordings/settings and the previous DLLs must be retained.

Before a separately authorized connection test, review saved enabled actions:
they can resume at login. First check the existing character-list connection
flow with automation disabled, then compare HP, Ki, facing, position, creatures
and equipment with the client. Check the visual marker separately. Only enable
requested automation after observations and proxy status agree. Never use
`start_bot` as an offline smoke test or treat these static results as live
permission.
