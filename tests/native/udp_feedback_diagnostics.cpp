#include "phone_receiver.hpp"
#include <iostream>
using namespace huoguo::android_udp;
namespace {
unsigned checks=0;
void check(bool okay,const char* name){if(!okay)throw std::runtime_error(name);checks++;}
Bytes body(bool key,uint64_t pts=123,size_t size=14000){
    Bytes nal(size,0x7f);nal[0]=0;nal[1]=0;nal[2]=0;nal[3]=1;nal[4]=key?0x65:0x41;
    return serializeLogical({540,960,pts|(key?IdrFlag:0),{},nal});
}
std::vector<Bytes> packets(uint64_t id,bool key,uint64_t capture=777777777){
    return packetize(body(key),id,capture,uint8_t(Reference|(key?Keyframe:0)),key?0:id-1);
}
}
int main(){try{
    PhoneReceiver disabled;uint64_t at=1000000;size_t delivered=0;
    for(const auto& p:packets(1,true))delivered+=disabled.accept(p,at++).size();
    check(delivered==1&&disabled.drainEvents().empty()&&disabled.eventStats()[0]==0,"default no events, same delivered body");
    PhoneReceiver exact;exact.setDiagnostics(true);auto fixture=packets(1,true);
    std::reverse(fixture.begin(),fixture.end());at=2000000;delivered=0;uint64_t last=at;
    for(const auto& p:fixture)if(Header::parse(p).shard>=2){last=at;delivered+=exact.accept(p,at++).size();}
    auto events=exact.drainEvents();
    check(delivered==1&&exact.stats()[8]>=4&&events.size()==2,"exact FEC recovery emits bounded quorum and delivery");
    check(events[0][0]==1&&events[1][0]==2&&events[0][1]==1&&events[1][13]==123,"frame identity and logical PTS");
    check(events[0][2]==777777777&&events[0][5]==2000000&&events[0][6]<=last&&events[0][7]==events[0][8],"host token distinct from phone quorum arrival");
    check(events[1][8]>=events[0][8]&&events[1][9]==2080000&&events[1][11]==1,"delivery decision/deadline/reason");
    check(events[1][12]>events[0][12]&&exact.eventStats()[1]==0,"ordered sequence and destructive drain");
    PhoneReceiver expire;expire.setDiagnostics(true);expire.accept(packets(1,true)[0],3000000);expire.expire(3080000);
    auto expired=expire.drainEvents();
    check(expired.size()==1&&expired[0][0]==3&&expired[0][7]==0&&expired[0][8]==3080000&&expired[0][11]==3,"first arrival plus fixed 80ms expiry event");
    PhoneReceiver settled;settled.setDiagnostics(true);settled.accept(packets(1,true)[0],4000000);
    for(const auto& p:packets(2,true))settled.accept(p,4000010);
    bool older=false;for(const auto& e:settled.drainEvents())if(e[0]==4&&e[1]==1&&e[11]==4)older=true;
    check(older&&settled.stats()[14]==0,"older unresolved mapping settlement reason");
    PhoneReceiver bounded;bounded.setDiagnostics(true);at=5000000;
    for(uint64_t id=1;id<=200;id++)for(const auto& p:packets(id,true))bounded.accept(p,at++);
    auto summary=bounded.eventStats();
    check(summary[1]==256&&summary[2]==144&&summary[3]==400,"native capacity and eviction counts exact");
    auto batch=bounded.drainEvents();check(batch.size()==64&&bounded.eventStats()[1]==192,"JNI-sized batch never exceeds 64 events");
    check(batch.front()[12]==145&&batch.back()[12]==208,"oldest bounded records evicted, sequence retained");
    bounded.setDiagnostics(false);check(bounded.drainEvents().empty()&&bounded.eventStats()[0]==0,"disable clears metadata only");
    std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"scope\":\"offline_actual_native_metadata_FEC_synthetic_bodies_no_phone_or_codec\"}\n";
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
