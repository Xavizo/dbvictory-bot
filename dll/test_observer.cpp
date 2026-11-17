// Synthetic in-process memory only. Does not open or attach to the game.
#include "observer.cpp"
#include <cassert>
#include <iostream>
static void put(uintptr_t address,uint32_t value) { std::memcpy((void*)address,&value,4); }
int main() {
    base=(uintptr_t)VirtualAlloc(nullptr,0xC00000,MEM_RESERVE|MEM_COMMIT,PAGE_READWRITE);
    assert(base);
    uintptr_t head=base+0x1000,node=base+0x2000,tile=base+0x5000,things=base+0x6000,c=base+0x7000;
    put(base+0xB73D3C,100);put(base+0xB73D3C+4,100);put(base+0xB73D3C+8,7);
    put(base+0xB73B90+7*8,head);put(base+0xB73B90+7*8+4,1);
    put(head+4,node);*(unsigned char*)(head+13)=1;
    put(node,head);put(node+8,head);put(node+16,3*2048+3);
    put(node+20+(4*32+4)*8,tile);
    put(tile,base+0x8C0FC4);put(tile+44,things);put(tile+48,things+8);put(tile+52,things+8);
    put(tile+56,100);put(tile+60,100);put(tile+64,7);put(things,c);
    put(c,base+0x8C0CB4);put(c+4,5);put(c+0x38,0x40000123);put(c+0x54,78);
    std::memcpy((void*)(c+0x3C),"Wolf",4);put(c+0x3C+16,4);put(c+0x3C+20,15);
    auto good=snapshot();
    assert(good.find("\"name\":\"Wolf\"")!=std::string::npos);
    assert(good.find("\"hp\":78")!=std::string::npos);
    assert(good.find("\"kind\":\"monster\"")!=std::string::npos);
    assert(good.find("\"x\":100,\"y\":100,\"z\":7")!=std::string::npos);
    put(c,base+0x8BA7F0);assert(snapshot().find("Wolf")==std::string::npos);
    put(c,base+0x8C0CB4);put(c+0x54,101);assert(snapshot().find("Wolf")==std::string::npos);
    put(c+0x54,78);put(tile+64,8);assert(snapshot().find("Wolf")==std::string::npos);
    put(tile+64,7);put(node, node);put(node+16,9999); // corrupt cycle is bounded
    assert(snapshot().find("Wolf")==std::string::npos);
    VirtualFree((void*)base,0,MEM_RELEASE);
    std::cout << "Observer synthetic memory checks passed\n";
}
