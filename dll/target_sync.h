// Current-build client target synchronization only. No attack/move packets.
// Static verification: Game::getAttackingCreature binding at RVA C2CCE binds
// Game B73880 and getter BAAC0 (+14/+18 shared_ptr). Setter 9D6D0 assigns that
// shared_ptr and invokes onAttackingCreatureChange. Never call from pipe/WndProc.
#include <cstdio>
struct TargetRequest { uint32_t id,expected,sequence; DWORD expires; bool pending; };
static CRITICAL_SECTION target_lock;
static TargetRequest target_request={};
static volatile LONG target_ready=0,target_result=0;
static uint32_t owned_target=0;
static DWORD game_thread=0;
typedef BOOL (WINAPI *PeekFn)(LPMSG,HWND,UINT,UINT,UINT);
static PeekFn original_peek=nullptr;
typedef void (__attribute__((thiscall)) *SetTargetFn)(void*,const uint32_t*);
static SetTargetFn set_target=nullptr;
static uint32_t current_target() {
    uint32_t ptr=0,vt=0,id=0;
    if (!read_mem(base+0xB73880+0x14,&ptr,4)) return UINT32_MAX;
    if (!ptr) return 0;
    if (!read_mem(ptr,&vt,4) || !creature_kind(vt) || !read_mem(ptr+0x38,&id,4)) return UINT32_MAX;
    return id;
}
static bool find_target(uint32_t id,uint32_t* shared) {
    uint32_t pos[3];
    if (!read_mem(base+0xB73D3C,pos,12) || pos[0]<8 || pos[0]>65525 || pos[1]<6 || pos[1]>65527 || pos[2]>15) return false;
    for (uint32_t y=pos[1]-6;y<pos[1]+8;++y) for(uint32_t x=pos[0]-8;x<pos[0]+10;++x) {
        uintptr_t block=block_at(pos[2],(y/32)*2048+x/32),tile=0;unsigned char raw[84];
        if (!block || !read_mem(block+((y%32)*32+x%32)*8,&tile,4) || !read_mem(tile,raw,84) ||
            u32(raw)!=base+0x8C0FC4 || u32(raw+56)!=x || u32(raw+60)!=y || u32(raw+64)!=pos[2]) continue;
        uint32_t first=u32(raw+44),last=u32(raw+48),cap=u32(raw+52),things[128];
        if(first<0x10000 || last<first || cap<last || cap>=0x7FFE0000 || (cap-first)/8>64 || (last-first)%8 || !read_mem(first,things,last-first))continue;
        for(unsigned i=0;i<(last-first)/4;i+=2) {
            unsigned char c[88];
            if(!read_mem(things[i],c,88) || u32(c)!=base+0x8C0CB4 || u32(c+0x38)!=id || !u32(c+0x54) || u32(c+0x54)>100 || !things[i+1])continue;
            shared[0]=things[i];shared[1]=things[i+1];return true;
        }
    }
    return false;
}
static void apply_target() {
    TargetRequest req;
    EnterCriticalSection(&target_lock);req=target_request;target_request.pending=false;LeaveCriticalSection(&target_lock);
    if(!req.pending)return;
    if((LONG)(GetTickCount()-req.expires)>0){InterlockedExchange(&target_result,2);return;}
    uint32_t current=current_target(),shared[2]={0,0};
    // A newer native/manual target always wins over an older queued request.
    if(current==UINT32_MAX || current!=req.expected){InterlockedExchange(&target_result,3);return;}
    if(req.id==current){InterlockedExchange(&target_result,1);return;}
    if(!req.id && current!=owned_target){InterlockedExchange(&target_result,3);return;}
    if(req.id && !find_target(req.id,shared)){InterlockedExchange(&target_result,4);return;}
    set_target((void*)(base+0xB73880),shared);
    owned_target=req.id;InterlockedExchange(&target_result,current_target()==req.id ? 1 : 5);
}
static BOOL WINAPI target_peek(LPMSG msg,HWND hwnd,UINT first,UINT last,UINT remove) {
    uintptr_t caller=(uintptr_t)__builtin_return_address(0);
    BOOL result=original_peek(msg,hwnd,first,last,remove);
    // Verified Win32Window::pollEvents loop after the message queue is drained.
    // No arbitrary PeekMessage caller, nested message dispatch or pipe thread.
    if(!result && remove==PM_REMOVE && !hwnd && first==0 && last==0 &&
       (caller==base+0x40053B || caller==base+0x400566) && InterlockedCompareExchange(&target_ready,1,1)) {
        DWORD tid=GetCurrentThreadId();
        if(!game_thread)game_thread=tid;
        static thread_local bool applying=false;
        if(tid==game_thread && !applying){applying=true;apply_target();applying=false;}
    }
    return result;
}
static bool install_target_sync() {
    // Host pins the full executable SHA256; verify the exact ABI/call sites too.
    const unsigned char setter[]={0x53,0x8B,0xDC,0x83,0xEC,0x08,0x83,0xE4,0xF8,0x83,0xC4,0x04};
    unsigned char actual[sizeof(setter)];uint32_t iat=0;
    if(!read_mem(base+0x9D6D0,actual,sizeof(actual)) || std::memcmp(actual,setter,sizeof(actual)))return false;
    for(uintptr_t site:{base+0x400535,base+0x400560}) {
        unsigned char call[6];
        if(!read_mem(site,call,6) || call[0]!=0xFF || call[1]!=0x15 || u32(call+2)!=base+0x7A939C)return false;
    }
    PeekFn expected=(PeekFn)GetProcAddress(GetModuleHandleA("user32.dll"),"PeekMessageA");
    if(!expected || !read_mem(base+0x7A939C,&iat,4) || iat!=(uintptr_t)expected)return false;
    original_peek=expected;set_target=(SetTargetFn)(base+0x9D6D0);
    DWORD previous=0,unused=0;
    if(!VirtualProtect((void*)(base+0x7A939C),4,PAGE_READWRITE,&previous))return false;
    InterlockedExchangePointer((PVOID volatile*)(base+0x7A939C),(PVOID)target_peek);
    VirtualProtect((void*)(base+0x7A939C),4,previous,&unused);
    InterlockedExchange(&target_ready,1);return true;
}
static void receive_target(const std::string& line) {
    unsigned id=0,expected=0,seq=0;char extra=0;
    if(!InterlockedCompareExchange(&target_ready,1,1) || line.size()>96 ||
       std::sscanf(line.c_str(),"S %u %u %u %c",&id,&expected,&seq,&extra)!=3 ||
       (id && (id<0x40000000 || id>=0x80000000)) || expected==UINT32_MAX || !seq)return;
    EnterCriticalSection(&target_lock);
    if(seq>target_request.sequence)target_request={id,expected,seq,GetTickCount()+350,true};
    LeaveCriticalSection(&target_lock);
}
static void reset_target_requests() {
    EnterCriticalSection(&target_lock);target_request={};LeaveCriticalSection(&target_lock);
}
