// Observer plus a visual-only red target square for the pinned client.
// Never assigns the Game target or calls Lua; no attack/movement function calls.
// The host verifies the executable digest before loading this separate DLL.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <cstring>
#include <string>
#include <sstream>
#include <set>

static_assert(sizeof(void*) == 4, "DBVictory observer requires 32-bit MinGW");
#include "observer_layout.h"
static volatile LONG running = 1;
static uintptr_t base;
static const char* PIPE = OBSERVER_PIPE;

static bool read_mem(uintptr_t address, void* out, size_t size) {
    SIZE_T got = 0;
    return address >= 0x10000 && address < 0x7FFE0000 && size <= 8192 &&
        size <= 0x7FFE0000 - address &&
        ReadProcessMemory(GetCurrentProcess(), (void*)address, out, size, &got) && got == size;
}
static uint32_t u32(const unsigned char* p) { uint32_t n; std::memcpy(&n,p,4); return n; }

static uintptr_t block_at(uint32_t z, uint32_t key) {
    uint32_t index[2]; unsigned char header[16];
    if (!read_mem(base+MAP_BLOCKS+z*8,index,8) || index[1]>256 ||
        !read_mem(index[0],header,16) || header[13]!=1) return 0;
    uintptr_t node=u32(header+4);
    for (unsigned visited=0; node!=index[0] && visited<index[1]; ++visited) {
        unsigned char raw[20];
        if (!read_mem(node,raw,20) || raw[13]) return 0;
        uint32_t k=u32(raw+16);
        if (k==key) return node+20;
        node=u32(raw+(key<k ? 0 : 8));
    }
    return 0;
}

static const char* creature_kind(uint32_t vtable) {
    switch (vtable-base) {
        case VT_PLAYER:return "player";
        case VT_SELF:return "self";
        case VT_MONSTER:return "monster";
        case VT_NPC:return "npc";
        default:return nullptr;
    }
}

#include "target_marker.h"

static std::string snapshot() {
    uint32_t pos[3], check[3];
    if (!read_mem(base+MAP_CENTER,pos,12) || pos[0]<8 || pos[0]>65525 ||
        pos[1]<6 || pos[1]>65527 || pos[2]>15)
        return "{\"mode\":\"observer\",\"creatures\":[]}\n";
    std::ostringstream out;
    out << "{\"mode\":\"observer\",\"px\":" << pos[0] << ",\"py\":" << pos[1]
        << ",\"pz\":" << pos[2] << ",\"target_marker\":" << (target_ready ? "true" : "false")
        << ",\"client_target\":" << current_target() << ",\"marked_target\":" << (uint32_t)marked_target << ",\"target_marker_result\":" << target_result << ",\"creatures\":[";
    std::set<uint32_t> ids;
    // Only the 18x14 visible cells; never scan arbitrary process memory.
    for (uint32_t y=pos[1]-6;y<pos[1]+8;++y) for (uint32_t x=pos[0]-8;x<pos[0]+10;++x) {
        uintptr_t block=block_at(pos[2],(y/32)*2048+x/32), tile=0;
        unsigned char raw[84];
        if (!block || !read_mem(block+((y%32)*32+x%32)*8,&tile,4) ||
            !read_mem(tile,raw,84) || u32(raw)!=base+VT_TILE ||
            u32(raw+56)!=x || u32(raw+60)!=y || u32(raw+64)!=pos[2]) continue;
        uint32_t first=u32(raw+44), last=u32(raw+48), cap=u32(raw+52);
        if (first<0x10000 || last<first || cap<last || cap>=0x7FFE0000 ||
            (cap-first)/8>64 || (last-first)%8) continue;
        uint32_t things[128];
        if (!read_mem(first,things,last-first)) continue;
        for (unsigned i=0;i<(last-first)/4;i+=2) {
            unsigned char creature[88], verify[4];
            if (!read_mem(things[i],creature,sizeof(creature))) continue;
            const char* kind=creature_kind(u32(creature));
            if (!kind) continue;
            uint32_t id=u32(creature+0x38), hp=creature[0x54];
            uint32_t length=u32(creature+0x3C+16), capacity=u32(creature+0x3C+20);
            if (id<0x10000000 || id>=0x80000000 || hp>100 || !length ||
                length>63 || capacity<length || capacity>=256 || ids.count(id)) continue;
            char name[64]={};
            if (capacity<16) std::memcpy(name,creature+0x3C,length);
            else if (!read_mem(u32(creature+0x3C),name,length)) continue;
            bool valid=true; std::string escaped;
            for (unsigned j=0;j<length;++j) {
                unsigned char c=name[j];
                if (c<32 || c>126) {valid=false;break;}
                if (c=='"' || c=='\\') escaped+='\\';
                escaped+=c;
            }
            if (!valid || !read_mem(things[i],verify,4) || u32(verify)!=u32(creature)) continue;
            // Recheck the tile vector and position to reject a moving snapshot.
            unsigned char tile_check[24];
            if (!read_mem(tile+44,tile_check,24) || std::memcmp(tile_check,raw+44,24)) continue;
            if (!ids.empty()) out << ',';
            ids.insert(id);
            out << "{\"id\":" << id << ",\"name\":\"" << escaped << "\",\"hp\":" << hp
                << ",\"x\":" << x << ",\"y\":" << y << ",\"z\":" << pos[2]
                << ",\"kind\":\"" << kind << "\"}";
        }
    }
    if (!read_mem(base+MAP_CENTER,check,12) || std::memcmp(pos,check,12)) return "";
    out << "]}\n";
    return out.str();
}

static DWORD WINAPI serve(void*) {
    base=(uintptr_t)GetModuleHandleW(nullptr);
    InitializeCriticalSection(&target_lock);
    // An installed IAT callback must never outlive its module. This DLL stays
    // loaded until client exit, including when the panel/pipe is disconnected.
    HMODULE pinned=nullptr;
    if(GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS|GET_MODULE_HANDLE_EX_FLAG_PIN,
                         (LPCSTR)&serve,&pinned)) install_target_sync();
    while (InterlockedCompareExchange(&running,1,1)) {
        HANDLE pipe=CreateNamedPipeA(PIPE,PIPE_ACCESS_DUPLEX,PIPE_TYPE_BYTE|PIPE_READMODE_BYTE|PIPE_WAIT|
            PIPE_REJECT_REMOTE_CLIENTS,1,65536,4096,0,nullptr);
        if (pipe==INVALID_HANDLE_VALUE) return 1;
        if (ConnectNamedPipe(pipe,nullptr) || GetLastError()==ERROR_PIPE_CONNECTED) {
            reset_target_requests();std::string input;
            while (InterlockedCompareExchange(&running,1,1)) {
                DWORD available=0,got=0;
                if (!PeekNamedPipe(pipe,nullptr,0,nullptr,&available,nullptr)) break;
                if (available) { // Fixed, bounded target-selection protocol; no general commands.
                    char ignored[4096];
                    if (!ReadFile(pipe,ignored,available>sizeof(ignored)?sizeof(ignored):available,&got,nullptr)) break;
                    input.append(ignored,got);
                    size_t end;
                    while((end=input.find('\n'))!=std::string::npos){receive_target(input.substr(0,end));input.erase(0,end+1);}
                    if(input.size()>96)input.clear();
                }
                std::string data=snapshot(); DWORD sent=0;
                if (!data.empty() && (!WriteFile(pipe,data.data(),(DWORD)data.size(),&sent,nullptr) || sent!=data.size())) break;
                Sleep(100);
            }
        }
        reset_target_requests();DisconnectNamedPipe(pipe); CloseHandle(pipe);
    }
    return 0;
}

extern "C" BOOL APIENTRY DllMain(HMODULE module,DWORD reason,LPVOID) {
    if (reason==DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(module);
        HANDLE thread=CreateThread(nullptr,0,serve,nullptr,0,nullptr);
        if (!thread) return FALSE;
        CloseHandle(thread);
    } else if (reason==DLL_PROCESS_DETACH) InterlockedExchange(&running,0);
    return TRUE;
}
