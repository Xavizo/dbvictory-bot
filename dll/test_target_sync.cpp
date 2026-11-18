// Synthetic 32-bit test process only. Does not open a game process or install hooks.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <cstring>
#include <string>
#include <map>
#include <cassert>
#include <iostream>
static uintptr_t base=0x400000;
static std::map<uintptr_t,unsigned char> mem;
static bool read_mem(uintptr_t p,void* out,size_t n){
    auto b=(unsigned char*)out;
    for(size_t i=0;i<n;++i){auto it=mem.find(p+i);if(it==mem.end())return false;b[i]=it->second;}
    return true;
}
static void put(uintptr_t p,uint32_t v){for(unsigned i=0;i<4;++i)mem[p+i]=(unsigned char)(v>>(8*i));}
static uint32_t u32(const unsigned char* p){uint32_t n;std::memcpy(&n,p,4);return n;}
static const char* creature_kind(uint32_t v){return v==base+0x8C0CB4?"monster":nullptr;}
static uintptr_t block_at(uint32_t z,uint32_t key){return z==7&&key==0?0x110000:0;}
#include "target_sync.h"
static int calls=0;
static void __attribute__((thiscall)) setter(void*,const uint32_t* ptr){++calls;put(base+0xB73880+0x14,ptr[0]);}
static void clear_request(){reset_target_requests();target_result=0;}
int main(){
    InitializeCriticalSection(&target_lock);set_target=setter;target_ready=1;
    put(base+0xB73880+0x14,0);
    put(base+0xB73D3C,10);put(base+0xB73D3C+4,10);put(base+0xB73D3C+8,7);
    put(0x110000+(10*32+11)*8,0x120000);
    for(unsigned i=0;i<84;++i)mem[0x120000+i]=0;
    put(0x120000,base+0x8C0FC4);put(0x120000+56,11);put(0x120000+60,10);put(0x120000+64,7);
    put(0x120000+44,0x130000);put(0x120000+48,0x130008);put(0x120000+52,0x130008);
    put(0x130000,0x140000);put(0x130004,0x150000);
    for(unsigned i=0;i<88;++i)mem[0x140000+i]=0;
    put(0x140000,base+0x8C0CB4);put(0x140000+0x38,0x40000001);put(0x140000+0x54,100);
    receive_target("S 1073741825 0 1");apply_target();assert(calls==1&&current_target()==0x40000001&&target_result==1);
    // A manual change after the request was queued wins.
    receive_target("S 0 1073741825 2");put(base+0xB73880+0x14,0);apply_target();assert(calls==1&&target_result==3);
    clear_request();receive_target("S 1073741825 0 1");target_request.expires=GetTickCount()-1;apply_target();assert(calls==1&&target_result==2);
    clear_request();receive_target("S 1073741826 0 1");apply_target();assert(calls==1&&target_result==4);
    clear_request();put(0x140000+0x54,0);receive_target("S 1073741825 0 1");apply_target();assert(calls==1&&target_result==4);
    put(0x140000+0x54,100);clear_request();receive_target("S 1073741825 0 1");apply_target();assert(calls==2);
    receive_target("S 0 1073741825 2");apply_target();assert(calls==3&&current_target()==0);
    clear_request();receive_target("S 268435457 0 1");apply_target();assert(calls==3);
    receive_target("S 1073741825 0 1 garbage");apply_target();assert(calls==3);
    receive_target("S 1073741825 0 4");receive_target("S 1073741826 0 3");apply_target();assert(calls==4&&current_target()==0x40000001);
    // A target not owned by this bridge cannot be cleared.
    owned_target=0;receive_target("S 0 1073741825 5");apply_target();assert(calls==4&&target_result==3);
    clear_request();target_ready=0;receive_target("S 0 1073741825 1");apply_target();assert(calls==4);
    std::cout<<"Native target guards passed (synthetic process; no game hook).\n";
}
