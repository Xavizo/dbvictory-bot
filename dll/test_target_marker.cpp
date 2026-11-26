// Synthetic visual-marker tests. No game process or live hook.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <cstring>
#include <string>
#include <map>
#include <cassert>
#include <iostream>
static uintptr_t base=0x400000;
#include "observer_layout.h"
static std::map<uintptr_t,unsigned char> mem;
static bool read_mem(uintptr_t p,void* out,size_t n){
    auto b=(unsigned char*)out;
    for(size_t i=0;i<n;++i){auto it=mem.find(p+i);if(it==mem.end())return false;b[i]=it->second;}
    return true;
}
static void put(uintptr_t p,uint32_t v){for(unsigned i=0;i<4;++i)mem[p+i]=(unsigned char)(v>>(8*i));}
static uint32_t u32(const unsigned char* p){uint32_t n;std::memcpy(&n,p,4);return n;}
static const char* creature_kind(uint32_t v){return v==base+VT_MONSTER?"monster":nullptr;}
static uintptr_t block_at(uint32_t z,uint32_t key){return z==7&&key==0?0x110000:0;}

#include "target_marker.h"
static int shows=0,hides=0;
static void __attribute__((thiscall)) show(void* ptr,const float* color){
    ++shows;auto address=(uintptr_t)ptr;mem[address+SQUARE_VISIBLE]=1;
    for(unsigned i=0;i<16;++i)mem[address+SQUARE_COLOR+i]=((const unsigned char*)color)[i];
}
static void __attribute__((thiscall)) hide(void* ptr){++hides;mem[(uintptr_t)ptr+SQUARE_VISIBLE]=0;}
static void setup(){
    reset_target_requests();owned_target=owned_pointer=0;marked_target=0;shows=hides=0;target_ready=1;target_result=0;
    put(base+GAME+0x14,0);put(base+MAP_CENTER,10);put(base+MAP_CENTER+4,10);put(base+MAP_CENTER+8,7);
    put(0x110000+(10*32+11)*8,0x120000);
    for(unsigned i=0;i<84;++i)mem[0x120000+i]=0;
    put(0x120000,base+VT_TILE);put(0x120000+56,11);put(0x120000+60,10);put(0x120000+64,7);
    put(0x120000+44,0x130000);put(0x120000+48,0x130008);put(0x120000+52,0x130008);
    put(0x130000,0x140000);put(0x130004,0x150000);
    for(unsigned i=0;i<0x170;++i)mem[0x140000+i]=0;
    put(0x140000,base+VT_MONSTER);put(0x140000+0x38,0x40000001);put(0x140000+0x54,100);
    mem[0x140055]=255; // Adjacent fields must not become part of the BYTE health value.
}
int main(){
    InitializeCriticalSection(&target_lock);show_square=show;hide_square=hide;
    setup();receive_target("M 1073741825 0 1");apply_target();
    assert(shows==1 && marked_target==0x40000001 && current_target()==0); // native target never assigned
    receive_target("M 1073741825 0 2");apply_target();assert(shows==1 && hides==0); // renew without flashing
    receive_target("M 0 0 3");apply_target();assert(hides==1 && marked_target==0 && current_target()==0);
    setup();receive_target("M 1073741825 0 1");apply_target();lease_until=GetTickCount()-1;apply_target();assert(hides==1 && !marked_target);
    setup();receive_target("M 1073741825 0 1");apply_target();reset_target_requests();apply_target();assert(hides==1 && !marked_target);
    setup();receive_target("M 1073741825 0 1");target_request.expires=GetTickCount()-1;apply_target();assert(!shows && target_result==2);
    setup();receive_target("M 1073741825 0 1");put(base+GAME+0x14,0x140000);apply_target();assert(!shows && target_result==3);
    setup();receive_target("M 1073741825 0 1");apply_target();put(base+GAME+0x14,0x140000);
    receive_target("M 0 1073741825 2");apply_target();assert(!hides && mem[0x140000+SQUARE_VISIBLE]==1); // native/manual owns square now
    setup();mem[0x140000+SQUARE_VISIBLE]=1;receive_target("M 1073741825 0 1");apply_target();assert(!shows && target_result==6);
    setup();receive_target("M 1073741825 0 1");apply_target();put(0x140000+SQUARE_COLOR,0);receive_target("M 0 0 2");apply_target();assert(!hides); // changed mark is native
    setup();put(0x140000+0x54,0);receive_target("M 1073741825 0 1");apply_target();assert(!shows && target_result==4);
    setup();put(base+MAP_CENTER+8,8);receive_target("M 1073741825 0 1");apply_target();assert(!shows && target_result==4);
    setup();receive_target("M 268435457 0 1");receive_target("M 1073741825 0 1 garbage");receive_target("S 1073741825 0 1");apply_target();assert(!shows);
    receive_target("M 1073741825 0 4");receive_target("M 1073741826 0 3");apply_target();assert(shows==1 && marked_target==0x40000001);
    setup();target_ready=0;receive_target("M 1073741825 0 1");apply_target();assert(!shows);
    assert(!install_target_sync()); // mismatched bytes never install a hook
    std::cout<<"Visual marker guards passed; native target unchanged; no live hook.\n";
}
