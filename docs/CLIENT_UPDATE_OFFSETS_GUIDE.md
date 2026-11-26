# Deriving the client memory layout yourself

Companion to `docs/CLIENT_UPDATE_GUIDE.md`. That guide covers the *process*
(backup, candidate checkout, build, test, install, acceptance). This one covers
the one step it leaves as "use a disassembler": **how to find the new addresses**
in a fresh `dbvStart.exe` and turn them into a working profile — the part that
was previously done for you.

Everything here is **offline and read‑only**: you parse the `.exe` on disk. You
never attach to the running game, never patch memory, never touch the network.
Do this with the game closed.

Worked example throughout = the Nov 26 build
(`af43383076c0abc7192b407210efc54da6b6cbaf4ff8a2aa18f22a7f7d7486e4`), whose
answers are already in `client_profiles.py` so you can check yourself.

---

## 0. The one rule that keeps you safe

**Never shift all offsets by a single delta.** Each address is proven on its own
against an *anchor* in the binary. The Nov 26 stats prove why: they are **not**
contiguous — HP `+0x558`, max‑HP `+0x560`, but level `+0x580`, mana `+0x590`,
magic `+0x5A0`. A blind "+0x18 shift of the old block" would have been wrong for
three of five fields. Anchors, not deltas.

There are only four kinds of anchor, and every value in a profile comes from one:

| Anchor | Finds |
| --- | --- |
| **A. MSVC RTTI** (class‑name strings) | every vtable (Player, LocalPlayer, Monster, Npc, Tile, Item, ThingType, ProtocolGame, the `holder<unsigned short>` one) |
| **B. Lua‑binding strings** (`"getCentralPosition"`, …) | the singletons `g_game`, `g_map`, `g_things` |
| **C. getter byte‑shapes** (`mov eax,[ecx+disp]; ret`, `fld qword…`) | object field offsets (hp, mana, id, direction, inventory, container map, …) |
| **D. the import table** (`PeekMessageA`) | the marker DLL's IAT slot + call sites |

---

## 1. Tools

- **Ghidra** (free) or IDA/Binary Ninja — for the human‑judgement steps
  (reading a getter to confirm which field it is). This is the only non‑scripted part.
- **Python 3** — to run `derive.py` below. No `pip` packages needed; it parses
  the PE by hand. (`pefile`/`capstone` are nice‑to‑have, not required.)
- The repo's own **`check_client_build.py`** — your acceptance judge.
- The x86 MinGW already in the repo runtime
  (`.codex\runtime\mingw32\bin\g++.exe`) to build the DLL.

Keep the **previous** `dbvStart.exe` next to the new one. Diffing old→new is far
faster than deriving from scratch, because the layout usually only *moves*.

---

## 2. Run the derivation helper first

Save this as `tools\derive.py` in your candidate checkout (it is a maintenance
helper, not part of the shipped bot). Run it against the new exe:

```powershell
python -I tools\derive.py 'D:\Gry\DBVClient_v5\dbvStart.exe'
```

It prints candidates for the deterministic anchors (A, B, D) outright, and for
the field getters (C) it dumps every candidate getter with its displacement so
you can match them by name in Ghidra. Verified against the Nov 26 build, its
output reproduces the committed profile exactly.

```python
#!/usr/bin/env python3
"""Offline, read-only derivation of DBVictory client memory layout from the .exe.

Does NOT attach to a process, open a socket, or write anything. It parses the
PE on disk and prints candidate addresses for a new ClientProfile. Every number
is a CANDIDATE: feed them into check_client_build.py, which re-proves them.

Usage:  python3 -I derive.py PATH/TO/dbvStart.exe
"""
import struct, sys, re, hashlib

data = open(sys.argv[1], "rb").read()
print("# sha256:", hashlib.sha256(data).hexdigest())
pe = struct.unpack_from("<I", data, 0x3C)[0]
assert data[pe:pe+4] == b"PE\0\0", "not a PE"
nsec = struct.unpack_from("<H", data, pe+6)[0]
opt = pe+24
IB = struct.unpack_from("<I", data, opt+28)[0]
opt_size = struct.unpack_from("<H", data, pe+20)[0]
secs = []
for i in range(nsec):
    sec0 = opt+opt_size+i*40
    s = data[sec0:sec0+40]
    vs, va, rs, rp = struct.unpack_from("<IIII", s, 8)
    secs.append((va, max(vs, rs), rp))
def o2r(off):
    for va, sz, rp in secs:
        if rp <= off < rp+sz: return va+off-rp
def r2o(rva):
    for va, sz, rp in secs:
        if va <= rva < va+sz: return rp+rva-va
def rd(rva, n): return data[r2o(rva):r2o(rva)+n]
def ptr(rva): return struct.unpack("<I", rd(rva, 4))[0] - IB   # RVA stored at rva
print("# image_base:", hex(IB))

# ---- 1. vtables via MSVC RTTI --------------------------------------------
def vtable_for(name):
    tag = (".?AV"+name+"@@\0").encode() if not name.startswith("holder") \
          else (".?AU?$"+name+"@@\0").encode()
    m = re.search(re.escape(tag), data)
    if not m: return None
    td_va = IB + o2r(m.start()-8)                       # TypeDescriptor
    col = struct.pack("<IIII", 0, 0, 0, td_va)          # COL: sig,off,cd,pTD
    cm = re.search(re.escape(col), data)
    if not cm: return None
    col_va = IB + o2r(cm.start())
    vm = re.search(re.escape(struct.pack("<I", col_va)), data)  # meta ptr
    if not vm: return None
    vt = o2r(vm.start()+4)
    slot8 = ptr(vt+8*4)                                 # should be isCreature true
    return vt, slot8
print("\n## vtables (RTTI)  [slot8 should match for creatures]")
for n in ("Player","LocalPlayer","Monster","Npc","Tile","Item","ThingType","ProtocolGame"):
    r = vtable_for(n)
    if r: print(f"  {n:12} vtable=0x{r[0]:X}  slot8=0x{r[1]:X}")
h = vtable_for("holder@G@any@stdext")
if h: print(f"  u16_holder   vtable=0x{h[0]:X}")

# ---- 2. singletons via Lua-binding strings --------------------------------
print("\n## singletons (Lua-binding xref)")
text_rva, text_sz, text_rp = next((va,sz,rp) for va,sz,rp in secs)  # first = .text
text = data[text_rp:text_rp+text_sz]
def strva(s):
    m = re.search(b"\0"+s+b"\0", data)
    return IB + o2r(m.start()+1) if m else None
for sing, binding in (("g_map","getCentralPosition"),("g_game","getAttackingCreature"),
                      ("g_things","getThingType")):
    va = strva(binding.encode())
    if not va: continue
    cands = {}
    for m in re.finditer(re.escape(b"\x68"+struct.pack("<I", va)), text):  # push <string>
        win = text[m.start()-96:m.start()+96]
        for mm in re.finditer(rb"\x68([\x00-\xff]{4})", win):              # nearby push imm32
            v = struct.unpack("<I", mm.group(1))[0]
            if IB+0x9a0000 <= v < IB+0xb84000: cands[v-IB] = cands.get(v-IB,0)+1
    best = sorted(cands.items(), key=lambda kv:-kv[1])[:3]
    print(f"  {sing:9} {binding:22} -> {[hex(a) for a,_ in best]}")

# ---- 3. getter candidates (match to names in a disassembler) --------------
print("\n## getter instruction candidates (displacement = field offset)")
shapes = [("mov eax,[ecx+d32];ret", rb"\x8b\x81(....)\xc3", 4, "<I"),
          ("fld qword[ecx+d32];ret", rb"\xdd\x81(....)\xc3", 4, "<I"),
          ("movzx eax,word[ecx+d8];ret", rb"\x0f\xb7\x41(.)\xc3", 1, "<B"),
          ("mov al,[ecx+d8];ret", rb"\x8a\x41(.)\xc3", 1, "<B"),
          ("mov eax,[ecx+d8];ret", rb"\x8b\x41(.)\xc3", 1, "<B")]
for label, pat, w, fmt in shapes:
    hits = [(0x1000+m.start(), struct.unpack(fmt, m.group(1))[0]) for m in re.finditer(pat, text)]
    hits = [h for h in hits if h[1] >= 0x30]            # ignore tiny/irrelevant
    print(f"  {label:26} " + ", ".join(f"0x{r:X}:+0x{d:X}" for r,d in hits[:10]))

# ---- 4. PeekMessageA IAT slot + call sites (marker DLL) -------------------
print("\n## marker anchors")
imp_rva = struct.unpack_from("<I", data, opt+96+1*8)[0]
o = r2o(imp_rva)
while True:
    oft, _, _, name_rva, iat_rva = struct.unpack_from("<IIIII", data, o)
    if not iat_rva: break
    if data[r2o(name_rva):r2o(name_rva)+11].lower().startswith(b"user32.dll"):
        t = r2o(oft or iat_rva); i = 0
        while (e := struct.unpack_from("<I", data, t+i*4)[0]):
            if not e & 0x80000000 and data[r2o(e)+2:r2o(e)+14] == b"PeekMessageA":
                slot = iat_rva+i*4
                print(f"  PeekMessageA IAT=0x{slot:X}")
                sites = [0x1000+m.start() for m in re.finditer(
                         re.escape(b"\xff\x15"+struct.pack("<I", IB+slot)), text)]
                print(f"  PeekMessageA call sites: {[hex(s) for s in sites]}")
            i += 1
    o += 20
```

### Nov 26 reference output (what "correct" looks like)

```
## vtables (RTTI)
  Player       vtable=0x8BE824  slot8=0x6D6C0
  LocalPlayer  vtable=0x8BE900  slot8=0x6D6C0
  Monster      vtable=0x8C4C3C  slot8=0x6D6C0
  Npc          vtable=0x8C4CFC  slot8=0x6D6C0
  Tile         vtable=0x8C4F4C
  Item         vtable=0x8BE72C
  ThingType    vtable=0x8C4F18
  ProtocolGame vtable=0x8BE5FC
  u16_holder   vtable=0x8BE7B0
## singletons
  g_map    getCentralPosition   -> ['0xb79e60']
  g_game   getAttackingCreature -> ['0xb79b50']
  g_things getThingType         -> ['0xb7a4b0']
## marker anchors
  PeekMessageA IAT=0x7AE3A4
  PeekMessageA call sites: ['0x405455', '0x405480']
```

The four creature vtables must all share the same `slot8` (that's the
constant‑true `isCreature` getter — it becomes `true_getter` in the profile).
If one differs, your vtable match is wrong.

---

## 3. Anchor A — vtables (RTTI), in detail

The script does this automatically, but understand it so you can fix a miss:

1. Find the class name string, e.g. `.?AVMonster@@`. Classes are `.?AV…`;
   the `holder<unsigned short>` struct is `.?AU?$holder@G@any@stdext@@`.
2. The **TypeDescriptor** starts 8 bytes before that string.
3. Find the **CompleteObjectLocator** — a 20‑byte record `{0,0,0,&TypeDescriptor,…}`;
   i.e. search for four little‑endian dwords `0,0,0,<TD virtual address>`.
4. Find the dword that points at that COL. The **vtable starts 4 bytes after it**
   (that dword is the vtable's `-1` meta pointer).
5. Cross‑check: read vtable slot 8 (`vtable + 0x20`). For the four creature
   classes it must be the same address — the `isCreature` true‑getter (`b0 01 c3`).

`ProtocolGame`'s vtable is what `client_protocol.PROTOCOL_VTABLE` pins
(`0x8BE5FC` on Nov 26).

---

## 4. Anchor B — singletons (g_game / g_map / g_things)

OTClient registers Lua bindings like `g_lua.bindClassStaticFunction("g_map",
"getCentralPosition", …)`. Near the code that references the `"getCentralPosition"`
string there is a `push <singleton address>` (`68 xx xx xx xx`) into `.data`.
The script picks the `.data` address that appears most often around those
binding strings. Confirmed Nov 26: `g_map 0xB79E60`, `g_game 0xB79B50`,
`g_things 0xB7A4B0`.

From the singletons you get several profile fields as **fixed sub‑offsets** — but
verify each, because a struct can grow:

| Profile field | Where | Nov 26 | Verify with |
| --- | --- | --- | --- |
| `map_blocks` | `g_map + 0x0` | `0xB79E60` | tree walk reads sane blocks |
| `map_center` | `g_map + 0x1AC` | `0xB7A00C` | `check` signature `f3 0f 7e 81 ac 01 00 00` at `getCentralPosition` |
| `item_types` | `g_things + 0x0` | `0xB7A4B0` | catalogue count > 10161 |
| container map | `g_game + 0x2C` | — | `check` signature `8b 46 2c` |
| attacking creature | `g_game + 0x14` | — | used by `target_marker.h` / `client_protocol` (`g_game+0x24` link) |

---

## 5. Anchor C — field offsets (the part that needs Ghidra)

The getters are tiny leaf functions. The script lists all candidates with their
displacements; you confirm **which field each is** by opening it in Ghidra and
reading the Lua binding name that resolves to it. Match these for Nov 26:

| Field | Getter shape | RVA | Offset |
| --- | --- | --- | --- |
| Creature id (`getId`) | `mov eax,[ecx+0x38]; ret` | `0x6D3E0` | `+0x38` |
| Creature health % (`getHealthPercent`) | `mov al,[ecx+0x54]; ret` | `0xBAE30` | `+0x54` (BYTE) |
| Direction (`getDirection`) | `mov eax,[ecx+0x58]; ret` | `0x737D0` | `+0x58` |
| Speed (`getSpeed`) | `movzx eax,word[ecx+0xD4]; ret`* | `0xBAEC0` | `+0xD4` (U16) |
| Conditions/states (`getStates`) | `mov eax,[ecx+0x548]; ret` | `0xBB310` | `+0x548` |
| HP (`getHealth`) | `fld qword[ecx+0x558]; ret` | `0xBB3C0` | `+0x558` (double) |
| Max HP | `fld qword[ecx+0x560]; ret` | `0xBB3D0` | `+0x560` (double) |
| Level | `fld qword[ecx+0x580]; ret` | `0xBB410` | `+0x580` (double) |
| Mana | `fld qword[ecx+0x590]; ret` | `0x73B40` | `+0x590` (double) |
| Magic level | `fld qword[ecx+0x5A0]; ret` | `0xBB430` | `+0x5A0` (double) |
| Inventory vector | `mov ecx,[edx+0x484]` | (in `getInventoryItem`) | `+0x484` |
| Item id (`Item::getId`) | `movzx eax,word[ecx+0x35]; ret` | `0xADB60` | `+0x35` (U16) |

\* `getSpeed` is `66 8b 81 d4 00 00 00 c3` (a U16 `mov ax,…`); the plain‑shape
scanner shows the body one byte in at `0xBAEC1`. Both point at `+0xD4`.

These populate `PlayerLayout(stats, conditions, inventory, game_singleton,
speed_getter, states_getter)`. Note `stats` is the **base** of the stat block
(`+0x558`); the reader in `client_player_state.py` knows the field strides from
there, so confirm the *strides* (hp→maxhp = +8, but maxhp→level = +0x20) match
what you read, not just the base.

**Widths matter.** HP/mana/level/magic are **doubles** (`fld`), creature health %
is a **byte**, speed and item id are **U16**, id/direction/conditions are **U32**.
Getting the width wrong gives plausible‑looking garbage. This is exactly why the
derivation stays human‑assisted at this step.

---

## 6. Anchor D — the marker DLL (only if you keep the red square)

The visual target square needs four things, all in `dll/observer_layout.h`:

- `PEEK_IAT` — the `PeekMessageA` import slot (script: `0x7AE3A4`).
- `PEEK_SITE_1/2` — the two call sites into it from the main loop
  (script: `0x405455`, `0x405480`). The hook in `target_marker.h` checks the
  **return address** (`site + 6`) so it only acts at the game‑loop boundary.
- `SHOW_SQUARE` / `HIDE_SQUARE` — `Creature::showStaticSquare` / `hideStaticSquare`.
  Find them by their full body signatures (in `check_client_build.py`); the two
  displacements inside `show` are `SQUARE_VISIBLE` (`0x15D`) and `SQUARE_COLOR`
  (`0x13C`). On Nov 26 they are `0xBA920` / `0xBA960`.

If any marker signature doesn't match byte‑for‑byte, **do not** ship the marker
for that build — fall back to the plain read‑only observer (`observer.cpp`) and
revisit later. This is the containment the Nov 17 record already chose once.

---

## 7. The protocol session key (the thing that changed Nov 26)

Older builds leaked the XTEA key via the RSA login block. The Nov 26 build
stopped, so the key is now read from the live `ProtocolGame` object
(`client_protocol.py`). To re‑derive for a future build:

1. `PROTOCOL_VTABLE` = the ProtocolGame vtable from Anchor A (`0x8BE5FC`).
2. Confirm three accessor signatures still match (in `client_protocol.SIGNATURES`):
   the `getProtocolGame` getter, `Protocol::setXteaKey`, and
   `Protocol::enableXteaEncryption`. If these moved, relocate them by signature;
   if the *code* changed shape, stop and treat it as a mechanism change.
3. The live protocol pointer is at `g_game + 0x24` (an `{ptr, control}` shared‑ptr
   link). The key is **16 bytes at `protocol + 0x18`**; the encryption‑enabled
   flag is the byte at `protocol + 0x3E`.
4. You never trust this blindly: `proxy._confirm_client_key` validates the read
   key against **two real server frames** (checksum + inner opcode) before any
   automation turns on. If it can't confirm after 8 tries it logs
   "could not be confirmed" and runs as a dumb relay — which is the correct
   failure, not a bug to work around.

> If a future build's login envelope differs, update the recognised envelope in
> `proxy._process_login_packet` (Nov 26: checksummed, 137 bytes, opcode 10,
> OS 10, version 372). These are observed facts for this build, not constants.

---

## 8. Write the profile and prove it

1. **Add** a new `ClientProfile` (+ `PlayerLayout`) to `client_profiles.py`;
   append to `PROFILES`. Never edit an existing profile — keep old ones for
   rollback. Give it a new `observer_dll` name and `observer_pipe`.
2. Add the `#if defined(DBV_CLIENT_YYYYMMDD)` branch to `dll/observer_layout.h`
   and extend `build_dll.ps1`'s `ValidateSet` + define + output name.
3. Add the build's signature block to `check_client_build.py` (copy the Nov 26
   block, swap values). **This is your acceptance test** — it re‑proves every
   number against bytes + RTTI. A profile with zero static checks is not verified.
4. Add the native‑key wiring in `mcp_server.py` (`client_key_reader = lambda:
   read_live_protocol_key(game_pid)`) and point `client_protocol.py` at the new
   profile + signatures.
5. Run the judge and the suite:

```powershell
.\build_dll.ps1 -ClientVersion YYYYMMDD
.\venv\Scripts\python.exe check_client_build.py 'D:\Gry\DBVClient_v5\dbvStart.exe' --dll '.\dll\dbvobserver_marker_YYYYMMDD.dll'
.\venv\Scripts\python.exe -m pytest -q
```

If `check_client_build.py` prints `static_checks` > 0 and no mismatch, your
addresses are internally consistent. If it throws `Static signature mismatch at
RVA …`, that exact field is wrong — go back to its anchor.

6. Then follow `CLIENT_UPDATE_GUIDE.md` §6–8 for the offline suite, install in a
   maintenance window, and the manual fresh‑login acceptance.

---

## 9. When to stop and not force it

The anchor method handles a **relocation‑only** update (the common case). Stop
and get help — don't bypass a check — when:

- A getter's **code shape** changed, not just its displacement (a field changed
  type, or a struct was reorganised). `check_client_build.py` will mismatch.
- The **key mechanism** changed again (the handshake no longer exposes the key
  where §7 expects). `_confirm_client_key` simply never confirms.
- The **protocol/opcodes** changed — you'll see `parse message exception` in the
  client's `packet.log` or observer `failures > 0`, not a memory error.
- RTTI is stripped (the class‑name strings vanish) — then vtables need a
  signature fallback instead of Anchor A.

In every one of these, the existing guards fail **safely**: the bot refuses to
start or drops to a dumb relay rather than acting on bad data. That is the
behaviour to preserve. Never weaken a hash/signature check or re‑enable a hook to
make an unknown build start.

---

## 10. One‑line summary

Hash → run `derive.py` → confirm vtables/singletons/marker automatically, match
the dozen field getters in Ghidra by name+width → write a new profile → let
`check_client_build.py` re‑prove it → build DLL, test, install, fresh‑login.
Anchors, never deltas; fail safe, never force.
