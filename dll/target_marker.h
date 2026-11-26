// Visual-only target marker, selected by the pinned observer build layout.
// The entire bodies of showStaticSquare/hideStaticSquare are verified below:
// they only store a flag and RGBA floats. No Game target/shared_ptr assignment,
// Lua callbacks, input, movement or attack calls occur in this module.
#include <cstdio>
#include "observer_layout.h"
struct TargetRequest { uint32_t id,expected,sequence; DWORD expires; bool pending; };
static CRITICAL_SECTION target_lock;
static TargetRequest target_request={};
static volatile LONG target_ready=0,target_result=0,marked_target=0,marker_cancel=0;
static uint32_t owned_target=0,owned_pointer=0;
static DWORD game_thread=0,lease_until=0;
typedef BOOL (WINAPI *PeekFn)(LPMSG,HWND,UINT,UINT,UINT);
static PeekFn original_peek=nullptr;
typedef void (__attribute__((thiscall)) *ShowFn)(void*,const float*);
typedef void (__attribute__((thiscall)) *HideFn)(void*);
static ShowFn show_square=nullptr;
static HideFn hide_square=nullptr;
static const float red[4]={1.f,0.f,0.f,1.f};
static uint32_t current_target() {
    uint32_t ptr=0,vt=0,id=0;
    if (!read_mem(base+GAME+0x14,&ptr,4)) return UINT32_MAX;
    if (!ptr) return 0;
    if (!read_mem(ptr,&vt,4) || !creature_kind(vt) || !read_mem(ptr+0x38,&id,4)) return UINT32_MAX;
    return id;
}
static bool find_target(uint32_t id,uint32_t* shared) {
    uint32_t pos[3];
    if (!read_mem(base+MAP_CENTER,pos,12) || pos[0]<8 || pos[0]>65525 || pos[1]<6 || pos[1]>65527 || pos[2]>15) return false;
    for (uint32_t y=pos[1]-6;y<pos[1]+8;++y) for(uint32_t x=pos[0]-8;x<pos[0]+10;++x) {
        uintptr_t block=block_at(pos[2],(y/32)*2048+x/32),tile=0;unsigned char raw[84];
        if (!block || !read_mem(block+((y%32)*32+x%32)*8,&tile,4) || !read_mem(tile,raw,84) ||
            u32(raw)!=base+VT_TILE || u32(raw+56)!=x || u32(raw+60)!=y || u32(raw+64)!=pos[2]) continue;
        uint32_t first=u32(raw+44),last=u32(raw+48),cap=u32(raw+52),things[128];
        if(first<0x10000 || last<first || cap<last || cap>=0x7FFE0000 || (cap-first)/8>64 || (last-first)%8 || !read_mem(first,things,last-first))continue;
        for(unsigned i=0;i<(last-first)/4;i+=2) {
            unsigned char c[88];
            if(!read_mem(things[i],c,88) || u32(c)!=base+VT_MONSTER || u32(c+0x38)!=id || !c[0x54] || c[0x54]>100 || !things[i+1])continue;
            shared[0]=things[i];shared[1]=things[i+1];return true;
        }
    }
    return false;
}

static bool square_state(uint32_t ptr,uint32_t id,unsigned char& visible,float* color) {
    uint32_t vt=0,actual_id=0;
    return read_mem(ptr,&vt,4) && vt==base+VT_MONSTER &&
        read_mem(ptr+0x38,&actual_id,4) && actual_id==id &&
        read_mem(ptr+SQUARE_VISIBLE,&visible,1) && visible<=1 && read_mem(ptr+SQUARE_COLOR,color,16);
}
static void clear_owned() {
    unsigned char visible=0;float color[4];
    // Native/manual selection owns its square. Never erase its presentation.
    if(owned_target && current_target()!=owned_target &&
       square_state(owned_pointer,owned_target,visible,color) && visible &&
       !std::memcmp(color,red,16)) hide_square((void*)owned_pointer);
    owned_target=owned_pointer=0;InterlockedExchange(&marked_target,0);
}
static void apply_target() {
    if(InterlockedExchange(&marker_cancel,0))clear_owned();
    if(owned_target && (LONG)(GetTickCount()-lease_until)>0)clear_owned();
    TargetRequest req;
    EnterCriticalSection(&target_lock);req=target_request;target_request.pending=false;LeaveCriticalSection(&target_lock);
    if(!req.pending)return;
    if((LONG)(GetTickCount()-req.expires)>0){InterlockedExchange(&target_result,2);return;}
    uint32_t current=current_target(),shared[2]={0,0};
    if(current==UINT32_MAX || current!=req.expected || (current && current!=req.id)) {
        clear_owned();InterlockedExchange(&target_result,3);return;
    }
    if(!req.id){clear_owned();InterlockedExchange(&target_result,1);return;}
    if(req.id==current){clear_owned();InterlockedExchange(&target_result,1);return;}
    if(!find_target(req.id,shared)){clear_owned();InterlockedExchange(&target_result,4);return;}
    if(owned_target && (owned_target!=req.id || owned_pointer!=shared[0]))clear_owned();
    unsigned char visible=0;float color[4];
    if(!square_state(shared[0],req.id,visible,color)) {clear_owned();InterlockedExchange(&target_result,4);return;}
    if(visible && !(owned_target==req.id && !std::memcmp(color,red,16))) {
        // Existing native marks (red, green or other) are not ours to replace.
        owned_target=owned_pointer=0;InterlockedExchange(&marked_target,0);
        InterlockedExchange(&target_result,6);return;
    }
    if(!visible)show_square((void*)shared[0],red);
    owned_target=req.id;owned_pointer=shared[0];lease_until=GetTickCount()+1200;
    InterlockedExchange(&marked_target,req.id);InterlockedExchange(&target_result,1);
}
static BOOL WINAPI target_peek(LPMSG msg,HWND hwnd,UINT first,UINT last,UINT remove) {
    uintptr_t caller=(uintptr_t)__builtin_return_address(0);
    BOOL result=original_peek(msg,hwnd,first,last,remove);
    if(!result && remove==PM_REMOVE && !hwnd && first==0 && last==0 &&
       (caller==base+PEEK_SITE_1+6 || caller==base+PEEK_SITE_2+6) && InterlockedCompareExchange(&target_ready,1,1)) {
        DWORD tid=GetCurrentThreadId();
        if(!game_thread)game_thread=tid;
        static thread_local bool applying=false;
        if(tid==game_thread && !applying){applying=true;apply_target();applying=false;}
    }
    return result;
}
static bool install_target_sync() {
    unsigned char show[]={0x55,0x8b,0xec,0x8b,0x55,0x08,0xc6,0x81,0x45,0x01,0x00,0x00,0x01,0x8b,0x02,0x89,0x81,0x24,0x01,0x00,0x00,0x8b,0x42,0x04,0x89,0x81,0x28,0x01,0x00,0x00,0x8b,0x42,0x08,0x89,0x81,0x2c,0x01,0x00,0x00,0x8b,0x42,0x0c,0x89,0x81,0x30,0x01,0x00,0x00,0x5d,0xc2,0x04,0x00};
    unsigned char hide[]={0xc6,0x81,0x45,0x01,0x00,0x00,0x00,0xc3};
    std::memcpy(show+8,&SQUARE_VISIBLE,4);std::memcpy(hide+2,&SQUARE_VISIBLE,4);
    for(unsigned i=0;i<4;++i){uint32_t offset=SQUARE_COLOR+i*4;std::memcpy(show+17+i*9,&offset,4);}
    unsigned char actual[sizeof(show)];uint32_t iat=0;
    if(!read_mem(base+SHOW_SQUARE,actual,sizeof(show)) || std::memcmp(actual,show,sizeof(show)) ||
       !read_mem(base+HIDE_SQUARE,actual,sizeof(hide)) || std::memcmp(actual,hide,sizeof(hide)))return false;
    for(uintptr_t site:{base+PEEK_SITE_1,base+PEEK_SITE_2}) {
        unsigned char call[6];
        if(!read_mem(site,call,6) || call[0]!=0xFF || call[1]!=0x15 || u32(call+2)!=base+PEEK_IAT)return false;
    }
    PeekFn expected=(PeekFn)GetProcAddress(GetModuleHandleA("user32.dll"),"PeekMessageA");
    if(!expected || !read_mem(base+PEEK_IAT,&iat,4) || iat!=(uintptr_t)expected)return false;
    original_peek=expected;show_square=(ShowFn)(base+SHOW_SQUARE);hide_square=(HideFn)(base+HIDE_SQUARE);
    DWORD previous=0,unused=0;
    if(!VirtualProtect((void*)(base+PEEK_IAT),4,PAGE_READWRITE,&previous))return false;
    InterlockedExchangePointer((PVOID volatile*)(base+PEEK_IAT),(PVOID)target_peek);
    VirtualProtect((void*)(base+PEEK_IAT),4,previous,&unused);
    InterlockedExchange(&target_ready,1);return true;
}
static void receive_target(const std::string& line) {
    unsigned id=0,expected=0,seq=0;char extra=0;
    if(!InterlockedCompareExchange(&target_ready,1,1) || line.size()>96 ||
       std::sscanf(line.c_str(),"M %u %u %u %c",&id,&expected,&seq,&extra)!=3 ||
       (id && (id<0x40000000 || id>=0x80000000)) || expected==UINT32_MAX || !seq)return;
    EnterCriticalSection(&target_lock);
    if(seq>target_request.sequence)target_request={id,expected,seq,GetTickCount()+350,true};
    LeaveCriticalSection(&target_lock);
}
static void reset_target_requests() {
    EnterCriticalSection(&target_lock);target_request={};LeaveCriticalSection(&target_lock);
    InterlockedExchange(&marker_cancel,1);
}
