#include "phone_receiver.hpp"
#include <iostream>
#include <string>

using namespace huoguo::android_udp;
namespace {
unsigned checks=0;
void check(bool okay,const char* name){if(!okay)throw std::runtime_error(name);checks++;}
Bytes body(bool key){
    Bytes nal(14000,0x7f);nal[0]=0;nal[1]=0;nal[2]=0;nal[3]=1;nal[4]=key?0x65:0x41;
    return serializeLogical({540,960,123|(key?IdrFlag:0),{},nal});
}
std::vector<Bytes> packets(uint64_t id,bool key,uint64_t capture=777777777){
    return packetize(body(key),id,capture,uint8_t(Reference|(key?Keyframe:0)),key?0:id-1);
}
using Details=PhoneReceiver::MappingDetails;
using Event=PhoneReceiver::MappingDetailEvent;
Event row(const Details& values,size_t index){
    Event out{};
    std::copy_n(values.begin()+PhoneReceiver::MappingDetailHeaderColumns+
        index*PhoneReceiver::MappingDetailEventColumns,out.size(),out.begin());return out;
}
void contract(const Details& d){
    check(d.size()==292&&d[0]==1&&d[1]<=1&&d[2]==15,"version, enabled and installed hook shape");
    check(d[3]==d[4]+d[5]+d[6]&&d[3]+d[7]==d[34],"observed reason partition and explicit off coverage");
    check(d[17]==d[18]+d[19]+d[35]&&d[18]<=32&&d[20]==32&&d[21]==8,"ring retention/eviction/clear conservation");
    check(d[8]<=8&&d[9]<=8&&d[11]<=8&&d[12]<=8&&d[14]<=8&&d[15]<=8&&d[16]<=8,"adapter/core depth remains bounded");
    check(d[29]<=d[28]&&d[30]<=32&&d[32]==32,"bounded later-admission association cache");
    check(d[18]?(d[22]==d[17]-d[18]+1&&d[23]==d[17]):(d[22]==0&&d[23]==0&&d[24]==0&&d[25]==0),
        "retained sequence and empty-window coverage");
    for(size_t index=0;index<32;index++){
        const auto e=row(d,index);
        if(index>=d[18]){
            check(std::all_of(e.begin(),e.end(),[](auto value){return value==0;}),"unused records are zero, no stale tail");
        }else{
            check(e[0]==d[22]+index&&e[1]>0&&e[2]>0&&e[2]<=d[33]&&e[3]>=1&&e[3]<=4&&e[4]<=8&&e[5]<=8,
                "event sequence, caller time, known reason and bounded depths");
            check(index==0||row(d,index-1)[2]<=e[2],"events use monotonic caller arrival order");
            check(e[3]==2||e[3]==4?e[7]>0&&e[7]<=e[2]:e[7]==0,"capacity association only, never a grant");
        }
    }
    if(d[18])check(row(d,0)[2]==d[24]&&row(d,size_t(d[18]-1))[2]==d[25],"retained first/last time envelope");
}
void oldFrame(){
    PhoneReceiver r;r.setMappingDiagnostics(true);
    r.accept(packets(300,true)[0],1000000);
    r.accept(packets(45,true)[0],1000001); // Difference 255 is admissible.
    auto old=packets(44,true);r.accept(old[0],1000002);r.accept(old[1],1000003);
    const auto d=r.mappingDetails();contract(d);
    check(d[3]==2&&d[4]==2&&d[5]==0&&d[6]==0&&d[34]==2,"256 boundary rejects datagrams, not unique frames");
    check(d[15]==2&&d[16]==2&&d[28]==2,"255 boundary retains original mapping admission");
    check(row(d,0)[1]==44&&row(d,1)[1]==44&&row(d,0)[3]==1,"old-frame branch identity and reason");
    auto malformed=old[0];malformed[0]='X';r.accept(malformed,1000004);
    check(r.mappingDetails()[3]==2&&r.stats()[2]==1,"invalid header is outside three mapping branches");
    for(const auto& p:packets(301,true))r.accept(p,1000100);
    r.accept(packets(300,true)[1],1000101);
    check(r.mappingDetails()[3]==2&&r.stats()[4]>0,"retired/settled branch retains precedence");
}
void headerMismatch(){
    PhoneReceiver r;r.setMappingDiagnostics(true);const auto p=packets(1,true);
    r.accept(p[0],2000000);auto capture=p[1];put(capture,24,123,8);r.accept(capture,2000001);
    auto lifetime=p[1];put(lifetime,40,60000,4);r.accept(lifetime,2000002);
    const auto d=r.mappingDetails();contract(d);
    check(d[3]==2&&d[4]==0&&d[5]==0&&d[6]==2,"valid but original metadata mismatch independent counter");
    check(row(d,0)[3]==3&&row(d,1)[3]==3&&d[30]==0,"mismatch never creates a capacity association");
    r.expire(2080000);for(const auto& part:p)r.accept(part,2080001);
    check(r.stats()[16]==1&&r.stats()[18]==0&&r.stats()[14]==0,"same mapping keeps original 80ms grant, cannot restart");
}
void capacity(){
    PhoneReceiver r;r.setMappingDiagnostics(true);
    for(uint64_t id=1;id<=8;id++)r.accept(packets(id,true)[0],3000000+id-1);
    const auto ninth=packets(9,true);r.accept(ninth[0],3000008);r.accept(ninth[1],3000009);
    r.accept(packets(10,true)[0],3000010);
    const auto d=r.mappingDetails();contract(d);
    check(d[3]==3&&d[4]==0&&d[5]==3&&d[6]==0,"capacity counter counts each rejected shard");
    check(d[8]==8&&d[9]==8&&d[10]==10&&d[11]==8&&d[12]==8&&d[13]==10&&d[14]==8,
        "capacity decision records active adapter/core and oldest first-grant age");
    check(row(d,0)[7]==3000008&&row(d,1)[7]==3000008&&row(d,2)[7]==3000010&&d[30]==2,
        "repeated rejection does not extend its first observed rejection association");
    const auto again=r.mappingDetails();check(d==again,"poll is non-destructive and does not advance caller clock");
    r.setDiagnostics(true);r.setDiagnostics(false);
    check(r.mappingDetails()==d,"legacy frame diagnostics are independent of detail diagnostics");
}
uint64_t fillDroppedPredictives(PhoneReceiver& r,uint64_t at){
    for(uint64_t id=2;id<=9;id++)for(const auto& p:packets(id,false)){
        check(r.accept(p,at++).empty(),"predictive without IDR is not delivered");
    }
    return at;
}
void droppedCoreAndReadmission(){
    PhoneReceiver r;r.setMappingDiagnostics(true);uint64_t at=fillDroppedPredictives(r,4000000);
    check(r.stats()[11]==8&&r.stats()[14]==8&&r.mappingDetails()[16]==0,
        "core dependency drops settle frames while adapter mappings retain all eight slots");
    const auto next=packets(10,true);const auto rejectedAt=at;r.accept(next[0],at++);
    auto d=r.mappingDetails();contract(d);
    check(d[5]==1&&d[8]==8&&d[9]==0&&d[10]==rejectedAt-4000000,
        "capacity rejection can have eight adapter slots but no pending core frames");
    const auto expiredAt=at+80000;r.expire(expiredAt);
    check(r.stats()[14]==0&&r.stats()[16]==8&&r.stats()[6]==0,
        "adapter expiry cleans already-core-dropped mappings, no false core expiry count");
    const auto admittedAt=expiredAt+1;r.accept(next[0],admittedAt);
    d=r.mappingDetails();contract(d);
    const auto e=row(d,1);
    check(d[29]==1&&d[30]==0&&d[28]==9&&e[1]==10&&e[2]==admittedAt&&e[3]==4&&e[7]==rejectedAt,
        "later successful admission links exact rejected frame and original association time");
    check(e[4]==1&&e[5]==0&&e[6]==0,"admission event point is after emplace and before core receive");
    size_t delivered=0;
    for(size_t i=1;i<next.size();i++)delivered+=r.accept(next[i],admittedAt+79999).size();
    check(delivered==1&&r.stats()[20]==79999&&r.stats()[14]==0,
        "grant starts at successful admission even when first rejected shard is more than 80ms earlier");
    r.accept(next[0],admittedAt+80001);
    check(r.mappingDetails()[29]==1&&r.stats()[18]==1,"settled replay cannot invent another admission association");

    PhoneReceiver expiry;expiry.setMappingDiagnostics(true);at=fillDroppedPredictives(expiry,5000000);
    expiry.accept(next[0],at);expiry.expire(at+80000);const auto grant=at+80001;
    expiry.accept(next[0],grant);expiry.expire(grant+80000);
    for(const auto& p:next)expiry.accept(p,grant+80001);
    check(expiry.stats()[14]==0&&expiry.stats()[16]==9&&expiry.stats()[18]==0&&expiry.mappingDetails()[29]==1,
        "partial later-admitted mapping still expires at its own unextended deadline");
}
void boundedCoverage(){
    PhoneReceiver r;r.setMappingDiagnostics(true);
    for(uint64_t id=1;id<=8;id++)r.accept(packets(id,true)[0],6000000+id-1);
    for(uint64_t id=9;id<=48;id++)r.accept(packets(id,true)[0],6000000+id);
    auto d=r.mappingDetails();contract(d);
    check(d[3]==40&&d[5]==40&&d[17]==40&&d[18]==32&&d[19]==8&&d[22]==9&&d[23]==40,
        "32-record ring wraps with precise retained and evicted coverage");
    check(d[30]==32&&d[31]==8&&row(d,0)[1]==17&&row(d,31)[1]==48,
        "capacity association cache independently evicts oldest 8 of 40 IDs");
    r.expire(6080100);r.accept(packets(9,true)[0],6080101);
    d=r.mappingDetails();check(d[29]==0&&d[17]==40,"lost cache entry does not falsely claim no prior capacity rejection");
    r.accept(packets(48,true)[0],6080102);d=r.mappingDetails();contract(d);
    check(d[29]==1&&d[17]==41&&d[19]==9&&d[30]==31&&row(d,31)[3]==4&&row(d,31)[7]==6000048,
        "retained cached ID admits with bounded association after eviction");
    r.setMappingDiagnostics(false);d=r.mappingDetails();contract(d);
    check(d[1]==0&&d[18]==0&&d[35]==32&&d[30]==0&&d[27]==1,
        "disable reports ring clear separately and drops incomplete association history");
    r.setMappingDiagnostics(false);check(r.mappingDetails()==d,"repeated setting is idempotent");
}
void disabledCoverage(){
    PhoneReceiver r;const auto p=packets(1,true);r.accept(p[0],7000000);
    auto bad=p[1];put(bad,24,123,8);r.accept(bad,7000001);
    auto d=r.mappingDetails();contract(d);
    check(d[1]==0&&d[3]==0&&d[7]==1&&d[17]==0&&d[26]==0&&d[28]==0,
        "default off has no reason/events collection and counts explicit missed rejection coverage");
    r.setMappingDiagnostics(true);r.accept(bad,7000002);
    d=r.mappingDetails();contract(d);
    check(d[3]==1&&d[6]==1&&d[7]==1&&d[34]==2&&d[26]==1&&d[14]==1,
        "enabling does not manufacture pre-enable detail or hide off coverage");
    r.setMappingDiagnostics(false);r.accept(bad,7000003);r.setMappingDiagnostics(true);
    r.accept(bad,7000004);d=r.mappingDetails();contract(d);
    check(d[3]==2&&d[6]==2&&d[7]==2&&d[34]==4&&d[17]==2&&d[18]==1&&d[35]==1&&d[22]==2&&d[26]==2&&d[27]==1,
        "reenable preserves cumulative counters and missing-window accounting");
    const auto snapshot=d[33];r.expire(1);
    check(r.mappingDetails()[33]==snapshot&&r.mappingDetails()[34]==4&&r.stats()[2]==1,
        "invalid caller time changes no detail clock or mapping partition");
}
void unchangedStrategy(){
    PhoneReceiver control,observed;observed.setMappingDiagnostics(true);uint64_t at=8000000;
    auto send=[&](const Bytes& packet,uint64_t when){
        check(control.accept(packet,when)==observed.accept(packet,when),"detail on/off exact delivered bodies unchanged");
        check(control.stats()==observed.stats(),"legacy 21 stats exactly unchanged after each packet");
    };
    for(uint64_t id=2;id<=10;id++)for(const auto& p:packets(id,false))send(p,at++);
    control.expire(at+80000);observed.expire(at+80000);
    check(control.stats()==observed.stats(),"expiry/dependency/recovery strategy unchanged");
    at+=80001;for(const auto& p:packets(300,true))send(p,at++);
    send(packets(20,true)[0],at++);auto mismatch=packets(301,true);send(mismatch[0],at++);
    put(mismatch[1],24,123,8);send(mismatch[1],at++);
    observed.setMappingDiagnostics(false);observed.setMappingDiagnostics(true);
    for(const auto& p:packets(302,true))send(p,at++);
    contract(control.mappingDetails());contract(observed.mappingDetails());
    check(control.stats()[13]>0&&observed.mappingDetails()[4]>0&&observed.mappingDetails()[5]>0&&observed.mappingDetails()[6]>0,
        "same three real native branches exercised without changed legacy result");
}
}
int main(int argc,char** argv){try{
    if(argc!=2)throw std::invalid_argument("fixed fixture case required");
    const std::string selected=argv[1];
    if(selected=="old")oldFrame();
    else if(selected=="mismatch")headerMismatch();
    else if(selected=="capacity")capacity();
    else if(selected=="lifecycle")droppedCoreAndReadmission();
    else if(selected=="bounded")boundedCoverage();
    else if(selected=="disabled")disabledCoverage();
    else if(selected=="unchanged")unchangedStrategy();
    else throw std::invalid_argument("unknown fixed fixture case");
    std::cout<<"{\"passed\":true,\"checks\":"<<checks<<",\"case\":\""<<selected
        <<"\",\"schema_version\":1,\"detail_values\":292,\"scope\":\"offline_actual_native_mapping_synthetic_bodies_no_phone_or_codec\"}\n";
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
