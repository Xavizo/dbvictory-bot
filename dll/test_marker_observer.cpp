// Synthetic process-local memory. Never opens or attaches to the game.
#include "marker_observer.cpp"
#include <cassert>
#include <iostream>
static void put(uintptr_t address,uint32_t value) { std::memcpy((void*)address,&value,4); }
int main() {
    base=(uintptr_t)VirtualAlloc(nullptr,0xC00000,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    assert(base);
    uintptr_t head=base+0x1000,node=base+0x2000,tile=base+0x5000,things=base+0x6000,c=base+0x7000;
    put(base+MAP_CENTER,100);put(base+MAP_CENTER+4,100);put(base+MAP_CENTER+8,7);
    put(base+MAP_BLOCKS+7*8,head);put(base+MAP_BLOCKS+7*8+4,1);
    put(head+4,node);*(unsigned char*)(head+13)=1;
    put(node,head);put(node+8,head);put(node+16,3*2048+3);
    put(node+20+(4*32+4)*8,tile);
    put(tile,base+VT_TILE);put(tile+44,things);put(tile+48,things+8);put(tile+52,things+8);
    put(tile+56,100);put(tile+60,100);put(tile+64,7);put(things,c);
    put(c,base+VT_MONSTER);put(c+4,5);put(c+0x38,0x40000123);put(c+0x54,78);
    *(unsigned char*)(c+0x55)=255;
    std::memcpy((void*)(c+0x3C),"Wolf",4);put(c+0x3C+16,4);put(c+0x3C+20,15);
    auto good=snapshot();
    assert(good.find("\"name\":\"Wolf\"")!=std::string::npos);
    assert(good.find("\"hp\":78")!=std::string::npos);
    assert(good.find("\"kind\":\"monster\"")!=std::string::npos);
    assert(good.find("\"x\":100,\"y\":100,\"z\":7")!=std::string::npos);
    put(c,base+0x1000);assert(snapshot().find("Wolf")==std::string::npos);
    put(c,base+VT_MONSTER);put(c+0x54,101);assert(snapshot().find("Wolf")==std::string::npos);
    put(c+0x54,78);put(tile+64,8);assert(snapshot().find("Wolf")==std::string::npos);
    put(tile+64,7);put(node,node);put(node+16,9999);
    assert(snapshot().find("Wolf")==std::string::npos);
    assert(!install_target_sync()); // Empty synthetic code cannot pass the hook checks.
    VirtualFree((void*)base,0,MEM_RELEASE);
    std::cout << "Marker observer synthetic checks passed\n";
}
