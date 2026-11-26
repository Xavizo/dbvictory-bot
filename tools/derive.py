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
