#include "android_touch.hpp"
#include <cassert>
#include <iostream>
#include <limits>
#include <string>
using namespace huoguo;
static uint64_t read(const ControlPacket& p,size_t at,size_t bytes) {
    uint64_t v=0;for(size_t i=0;i<bytes;i++)v=(v<<8)|p[at+i];return v;
}
int main(int argc,char** argv) {
    if(argc==2&&std::string(argv[1])=="--emit") {
        AndroidTouch touch(540,1200);unsigned kind,id;float x,y,pressure;
        // Binary scrcpy control packets on stdout; errors only on stderr.
        try { while(std::cin>>kind>>id>>x>>y>>pressure)
            for(const auto& p:touch.event(static_cast<TouchType>(kind),id,{x,y,pressure})) {
                std::cout.write(reinterpret_cast<const char*>(p.data()),p.size());std::cout.flush();
            }
        } catch(const std::exception& e) { std::cerr<<e.what()<<'\n';return 2; }
        return 0;
    }
    AndroidTouch touch(540,1200);
    auto down=touch.event(TouchType::Down,11,{.25f,.5f,.8f})[0];
    assert(down[0]==2&&down[1]==0&&read(down,2,8)==11);
    assert(read(down,10,4)==135&&read(down,14,4)==600&&read(down,22,2)==52428);
    touch.event(TouchType::Down,22,{.75f,.5f,.7f});assert(touch.activeCount()==2);
    auto move=touch.event(TouchType::Move,11,{.1f,.5f,.8f})[0];assert(move[1]==2&&read(move,10,4)==54);
    auto releases=touch.resize(1200,540);assert(releases.size()==2&&touch.activeCount()==0);
    for(const auto& p:releases)assert(p[1]==1&&read(p,22,2)==0);
    auto edge=touch.event(TouchType::Down,42,{1,1,1})[0];
    assert(read(edge,10,4)==1199&&read(edge,14,4)==539);
    assert(read(edge,24,8)==0); // Never emit Mac mouse buttons.
    touch.event(TouchType::Cancel,42);assert(touch.activeCount()==0);
    for(int i=0;i<10;i++)touch.event(TouchType::Down,i,{.5f,.5f,1});
    bool rejected=false;try { touch.event(TouchType::Down,10,{.5f,.5f,1}); }catch(const std::invalid_argument&) { rejected=true; }
    assert(rejected);assert(touch.event(TouchType::CancelAll,0).size()==10);
    rejected=false;try {touch.event(TouchType::Move,1,{.5f,.5f,1});}catch(const std::invalid_argument&) {rejected=true;}assert(rejected);
    rejected=false;try {touch.event(TouchType::Down,1,{std::numeric_limits<float>::quiet_NaN(),.5f,1});}catch(const std::invalid_argument&) {rejected=true;}assert(rejected);
    std::cout<<"PASS native Android multi-touch: IDs, pinch packets, bounds, pressure, resize cancellation, no mouse fallback\n";
}
