#include "phone_receiver.hpp"
#include <iostream>
#include <string>
#include <type_traits>

using namespace huoguo::android_udp;
namespace {
unsigned checks=0;
void check(bool value,const char* label){if(!value)throw std::runtime_error(label);checks++;}
Bytes body(bool key){
    Bytes nal(14000,0x7f);nal[0]=0;nal[1]=0;nal[2]=0;nal[3]=1;nal[4]=key?0x65:0x41;
    return serializeLogical({540,960,123|(key?IdrFlag:0),{},nal});
}
std::vector<Bytes> packets(uint64_t id,bool key,uint64_t reference=UINT64_MAX){
    return packetize(body(key),id,777777777,uint8_t(Reference|(key?Keyframe:0)),
        reference==UINT64_MAX?(key?0:id-1):reference);
}
using Details=PhoneReceiver::LifecycleDetails;
using Settle=PhoneReceiver::LifecycleSettleEvent;
using Snapshot=PhoneReceiver::LifecycleCapacitySnapshot;
using Occupant=std::array<uint64_t,PhoneReceiver::LifecycleOccupantColumns>;
Settle settleRow(const Details& d,size_t row){
    Settle result{};std::copy_n(d.begin()+PhoneReceiver::LifecycleHeaderColumns+
        row*PhoneReceiver::LifecycleSettleColumns,result.size(),result.begin());return result;
}
Snapshot snapshotRow(const Details& d,size_t row){
    Snapshot result{};
    constexpr auto begin=PhoneReceiver::LifecycleHeaderColumns+
        PhoneReceiver::LifecycleSettleCapacity*PhoneReceiver::LifecycleSettleColumns;
    std::copy_n(d.begin()+begin+row*PhoneReceiver::LifecycleSnapshotColumns,result.size(),result.begin());return result;
}
Occupant occupantRow(const Snapshot& d,size_t row){
    Occupant result{};std::copy_n(d.begin()+PhoneReceiver::LifecycleCapacityHeaderColumns+
        row*PhoneReceiver::LifecycleOccupantColumns,result.size(),result.begin());return result;
}
bool zero(auto row){return std::all_of(row.begin(),row.end(),[](auto n){return n==0;});}
void contract(const Details& d){
    check(d.size()==240&&d[0]==1&&d[1]<=1&&d[2]==15,"independent 240 version/enabled/support-mask shape");
    check(d[3]==d[5]+d[6]+d[7]+d[8]&&d[3]==d[9],"settle reasons partition actual observed notifications");
    check(d[9]==d[10]+d[11]+d[12]&&d[10]<=16&&d[13]==16&&d[14]==4,"settle retained/evicted/cleared conservation");
    check(d[15]==d[17]+d[21],"capacity calls partition captures and duplicate suppression");
    check(d[17]==d[18]+d[19]+d[20]&&d[18]<=2&&d[22]==2&&d[23]==8&&d[24]==8&&d[25]==8,
        "capacity retained/evicted/cleared conservation and fixed columns");
    check(d[29]==32&&d[31]<=d[28],"independent dedup and caller-local enable time");
    for(size_t i=0;i<16;i++){
        const auto row=settleRow(d,i);
        if(i>=d[10]){check(zero(row),"unused settle padding is zero");continue;}
        check(row[0]==d[9]-d[10]+i+1&&row[1]>0&&row[2]>=1&&row[2]<=4&&row[3]>0&&row[3]<=d[28],
            "settle sequence/reason/id/time valid");
        if(i)check(settleRow(d,i-1)[3]<=row[3],"settle chronological tail");
    }
    for(size_t i=0;i<2;i++){
        const auto row=snapshotRow(d,i);
        if(i>=d[18]){check(zero(row),"unused capacity padding is zero");continue;}
        check(row[0]==d[17]-d[18]+i+1&&row[1]>0&&row[2]>0&&row[2]<=d[28]&&row[3]<=3&&
            row[5]==8&&row[6]<=8&&row[7]==8,"decision snapshot sequence/header/bounded depth");
        if(i)check(snapshotRow(d,i-1)[2]<=row[2],"capacity chronological tail");
        for(size_t j=0;j<8;j++){
            const auto slot=occupantRow(row,j);
            check(slot[0]>0&&slot[1]>0&&slot[2]>slot[1]&&slot[2]-slot[1]>=1000&&slot[2]-slot[1]<=80000,
                "occupant exact first grant and original lifetime");
            check(slot[1]<=row[2]&&row[2]<slot[2]&&slot[3]<=3&&slot[5]<=4,"occupant present before deadline with bounded core state");
            check((slot[6]==0&&slot[7]==0)||(slot[6]>=1&&slot[6]<=4&&slot[1]<=slot[7]&&slot[7]<=row[2]),
                "observed settle reason/time pair, missing stays zero");
            if(j)check(occupantRow(row,j-1)[0]<slot[0],"occupants preserve exact ordered map identities");
        }
    }
}
uint64_t send(PhoneReceiver& r,const std::vector<Bytes>& p,uint64_t at){for(const auto& b:p)r.accept(b,at++);return at;}
uint64_t fillDropped(PhoneReceiver& r,uint64_t at,uint64_t first=2){
    for(uint64_t id=first;id<first+8;id++)at=send(r,packets(id,false),at);return at;
}
struct CoreEvents {
    std::array<std::array<uint64_t,3>,16> rows{};size_t count=0;bool overflow=false;
    static void observe(void* context,Receiver::SettleReason reason,uint64_t id,uint64_t at) noexcept {
        auto& self=*static_cast<CoreEvents*>(context);
        if(self.count==self.rows.size()){self.overflow=true;return;}
        self.rows[self.count++]={uint64_t(reason),id,at};
    }
};
static_assert(std::is_nothrow_invocable_v<Receiver::SettleObserver,void*,Receiver::SettleReason,uint64_t,uint64_t>);
void coreReasons(){
    initializeFec();CoreEvents events;size_t delivered=0;
    Receiver r([&](uint64_t,uint64_t,const Bytes&){delivered++;});r.setSettleObserver(&events,CoreEvents::observe);
    uint64_t at=1000000;
    auto add=[&](auto values){for(const auto& p:values)r.receive(p,at++);};
    // Generic core clocks already share this caller-local domain.
    auto local=[&](uint64_t id,bool key,uint64_t ref){return packetize(body(key),id,at,uint8_t(Reference|(key?Keyframe:0)),ref);};
    add(local(1,true,0));check(events.count==1&&events.rows[0][0]==1&&events.rows[0][1]==1,"actual delivery settle reason");
    auto incomplete=local(2,false,1);r.receive(incomplete[0],at++);check(r.frameState(2)==1,"incomplete pending state");
    add(local(3,true,0));r.expire(at++);
    check(events.count==3&&events.rows[1][0]==1&&events.rows[2][0]==2&&events.rows[2][1]==2,"superseded incomplete clears at expire");
    auto expiring=local(4,false,3);const auto grant=at;r.receive(expiring[0],at++);r.expire(grant+80000);at=grant+80001;
    check(events.count==4&&events.rows[3][0]==3&&events.rows[3][1]==4&&events.rows[3][2]==grant+80000,"exact original deadline settle reason");
    add(local(5,false,4));check(events.count==5&&events.rows[4][0]==4&&events.rows[4][1]==5,"complete dependency rejection reason");
    check(delivered==2&&r.settleCalls()==5&&!events.overflow,"one observer notification per actual settle");
    r.setSettleObserver(nullptr,nullptr);add(local(6,true,0));check(r.settleCalls()==6&&events.count==5,"uninstalled observer not called");
    PhoneReceiver schema;contract(schema.mappingLifecycle());
}
void coreStates(){
    initializeFec();Receiver r([](uint64_t,uint64_t,const Bytes&){});uint64_t at=2000000;
    auto add=[&](uint64_t id,bool key,uint64_t reference){auto p=packetize(body(key),id,at,uint8_t(Reference|(key?Keyframe:0)),reference);for(const auto& x:p)r.receive(x,at++);};
    check(r.frameState(0)==0&&r.frameState(99)==0,"absent or invalid ID is not inferred settled");
    add(1,true,0);check(r.frameState(1)==3,"retained delivered tombstone state");
    add(3,false,2);check(r.frameState(3)==2,"complete frame waits for missing valid reference");
    const auto p=packetize(body(false),2,at,Reference,1);r.receive(p[0],at++);check(r.frameState(2)==1,"partial prior dependency retained");
    for(size_t i=1;i<p.size();i++)r.receive(p[i],at++);
    check(r.frameState(2)==3&&r.frameState(3)==3,"valid chain delivers without diagnostic mutation");
    for(uint64_t id=4;id<=270;id++)add(id,true,0);
    check(r.frameState(1)==4&&r.frameState(271)==0,"delivered advance after bounded tombstone eviction is separate from absent");
    // A complete blocked frame is superseded inside drain, not just expire.
    CoreEvents e;Receiver s([](uint64_t,uint64_t,const Bytes&){});s.setSettleObserver(&e,CoreEvents::observe);
    at=3000000;auto input=[&](uint64_t id,bool key,uint64_t ref){for(auto& x:packetize(body(key),id,at,uint8_t(Reference|(key?Keyframe:0)),ref))s.receive(x,at++);};
    input(1,true,0);input(3,false,2);input(4,true,0);
    check(e.count==3&&e.rows[2][0]==2&&e.rows[2][1]==3,"drain superseded branch has its own actual reason");
    PhoneReceiver schema;contract(schema.mappingLifecycle());
}
void capacityIdentity(){
    PhoneReceiver r;r.setMappingLifecycleDiagnostics(true);uint64_t at=fillDropped(r,4000000);
    check(r.stats()[11]==8&&r.stats()[14]==8&&r.mappingDetails()[16]==0,"unchanged core-dropped adapter occupancy");
    const auto rejected=packets(10,true);const auto capturedAt=at;r.accept(rejected[0],at++);r.accept(rejected[1],at++);
    const auto d=r.mappingLifecycle();contract(d);const auto snapshot=snapshotRow(d,0);
    check(d[3]==8&&d[8]==8&&d[4]==0&&d[15]==2&&d[17]==1&&d[21]==1,"exact settle identity and per-ID duplicate suppression");
    check(snapshot[1]==10&&snapshot[2]==capturedAt&&snapshot[3]==3&&snapshot[4]==0&&snapshot[5]==8&&snapshot[6]==0,"rejected IDR captured at decision, before core admission");
    for(size_t i=0;i<8;i++){
        const auto slot=occupantRow(snapshot,i);
        check(slot[0]==i+2&&slot[3]==Reference&&slot[4]==i+1&&slot[5]==3&&slot[6]==4,"eight exact settled predictive occupant IDs/references/reasons");
    }
    check(r.mappingLifecycle()==d,"snapshot non-destructive, no clock advance or retirement");
    r.expire(at+80000);const auto after=r.mappingLifecycle();contract(after);
    check(snapshotRow(after,0)==snapshot&&r.stats()[14]==0&&r.stats()[16]==8,"historical capacity snapshot survives live map expiry");
    const auto admittedAt=at+80001;r.accept(rejected[0],admittedAt);const auto admitted=r.mappingLifecycle();contract(admitted);
    check(snapshotRow(admitted,0)==snapshot&&r.stats()[14]==1,"later mapping cannot rewrite historical grant snapshot");
}
void livePending(){
    PhoneReceiver r;r.setMappingLifecycleDiagnostics(true);uint64_t at=5000000;
    for(uint64_t id=1;id<=8;id++)r.accept(packets(id,true)[0],at++);
    r.accept(packets(9,true)[0],at++);const auto d=r.mappingLifecycle();contract(d);const auto s=snapshotRow(d,0);
    check(s[6]==8&&d[3]==0,"ordinary in-flight core peak is distinguishable from settled adapter slots");
    for(size_t i=0;i<8;i++){const auto o=occupantRow(s,i);check(o[0]==i+1&&o[5]==1&&o[6]==0&&o[7]==0,"partial live frame never inferred settled");}
    const auto before=r.stats();check(r.mappingLifecycle()==d&&r.stats()==before,"query does not expire, drain or modify reference state");
}
void originalDeadlines(){
    for(const uint64_t lifetime:{uint64_t(1000),uint64_t(80000)}){
        PhoneReceiver r;r.setMappingLifecycleDiagnostics(true);uint64_t at=5500000;
        const auto firstGrant=at;
        for(uint64_t id=2;id<=9;id++){
            auto p=packets(id,false);
            for(auto& b:p){put(b,40,lifetime,4);r.accept(b,at++);}
        }
        const auto rejected=packets(10,true);
        r.accept(rejected[0],firstGrant+lifetime-1);const auto before=r.mappingLifecycle();contract(before);
        const auto captured=snapshotRow(before,0);check(captured[2]==firstGrant+lifetime-1,"decision captured one microsecond before oldest original expiry");
        for(size_t i=0;i<8;i++){
            const auto o=occupantRow(captured,i);check(o[2]-o[1]==lifetime,"each legal short or80ms grant stays exact");
        }
        // At the exact earliest deadline expireInternal removes that mapping
        // before the capacity check, allowing the new frame to enter core.
        r.accept(packets(11,true)[0],firstGrant+lifetime);const auto after=r.mappingLifecycle();contract(after);
        check(r.stats()[16]==1&&r.stats()[14]==8&&r.mappingDetails()[16]==1,"exact deadline frees only expired slot then normal new mapping admission");
        check(after[17]==1&&snapshotRow(after,0)==captured,"grant expiry does not renew or rewrite historical snapshot");
    }
}
void bounded(){
    PhoneReceiver r;r.setMappingLifecycleDiagnostics(true);uint64_t at=6000000;
    for(uint64_t id=1;id<=40;id++)at=send(r,packets(id,true),at);
    auto d=r.mappingLifecycle();contract(d);check(d[9]==40&&d[10]==16&&d[11]==24&&settleRow(d,0)[1]==25&&settleRow(d,15)[1]==40,"settle chronological tail wraps independently");
    PhoneReceiver c;c.setMappingLifecycleDiagnostics(true);at=fillDropped(c,7000000,100);
    for(uint64_t id=200;id<240;id++)c.accept(packets(id,true)[0],at++);
    d=c.mappingLifecycle();contract(d);check(d[15]==40&&d[17]==40&&d[18]==2&&d[19]==38&&d[30]==8,"two snapshots and independent32-ID dedup eviction");
    c.accept(packets(239,true)[1],at++);auto repeat=c.mappingLifecycle();check(repeat[21]==1&&repeat[17]==40,"retained dedup suppresses another shard");
    c.accept(packets(200,true)[1],at++);d=c.mappingLifecycle();contract(d);
    check(d[17]==41&&d[30]==9&&snapshotRow(d,0)[1]==239&&snapshotRow(d,1)[1]==200,"evicted ID can recapture with explicit eviction history");
    const auto old=c.mappingDetails();c.setMappingLifecycleDiagnostics(false);auto off=c.mappingLifecycle();contract(off);
    check(off[12]==8&&off[20]==2&&off[10]==0&&off[18]==0&&c.mappingDetails()==old,"disable clears only new rings, no old292 mutation");
    c.setMappingLifecycleDiagnostics(true);c.accept(packets(240,true)[0],at++);d=c.mappingLifecycle();contract(d);
    const auto fresh=snapshotRow(d,0);check(d[26]==2&&d[27]==1&&d[31]>0,"enable transitions use existing valid caller time");
    for(size_t i=0;i<8;i++){const auto o=occupantRow(fresh,i);check(o[5]==3&&o[6]==0&&o[7]==0,"reenable observes state but does not manufacture old settle notifications");}
}
void offCoverage(){
    PhoneReceiver r;uint64_t at=fillDropped(r,8000000);r.accept(packets(10,true)[0],at++);
    auto off=r.mappingLifecycle();contract(off);
    check(off[1]==0&&off[3]==0&&off[4]==8&&off[15]==0&&off[16]==1&&off[17]==0&&off[26]==0&&off[31]==0,"OFF counts missed actual settlements without installing observer");
    r.setMappingLifecycleDiagnostics(false);check(r.mappingLifecycle()==off,"repeated OFF does not clear or transition");
    r.setMappingLifecycleDiagnostics(true);r.accept(packets(11,true)[0],at++);auto on=r.mappingLifecycle();contract(on);
    check(on[3]==0&&on[4]==8&&on[15]==1&&on[16]==1,"enable preserves missed-settle and capacity coverage");
    for(size_t i=0;i<8;i++){const auto o=occupantRow(snapshotRow(on,0),i);check(o[6]==0&&o[7]==0,"pre-enable terminations keep missing reason/time");}
    const auto prior=on;r.setMappingDiagnostics(true);r.setDiagnostics(true);r.setDiagnostics(false);r.setMappingDiagnostics(false);
    check(r.mappingLifecycle()==prior,"legacy diagnostic switches independent of lifecycle collection");
}
void logicalReject(){
    PhoneReceiver r;r.setMappingLifecycleDiagnostics(true);Bytes bad(14000,0);const auto p=packetize(bad,1,777777,Keyframe|Reference,0);uint64_t at=9000000;
    for(const auto& b:p)check(r.accept(b,at++).empty(),"logical-invalid body never becomes playback output");
    const auto d=r.mappingLifecycle();contract(d);
    check(d[3]==1&&d[5]==1&&d[8]==0&&r.stats()[17]==1&&r.stats()[18]==0,"core-delivered reason does not claim logical parser accepted");
    check(settleRow(d,0)[1]==1&&settleRow(d,0)[2]==1&&r.stats()[14]==0,"logical reject preserves original deliveredThrough retirement");
}
void exceptionSemantics(){
    initializeFec();CoreEvents e;Receiver r([](uint64_t,uint64_t,const Bytes&){throw std::runtime_error("fixture existing output exception");});r.setSettleObserver(&e,CoreEvents::observe);
    uint64_t at=10000000;const auto p=packetize(body(true),1,at,Keyframe|Reference,0);bool thrown=false;
    for(const auto& b:p){try{r.receive(b,at++);}catch(const std::runtime_error&){thrown=true;break;}}
    check(thrown&&r.stats.framesDelivered==1&&r.pendingFrames()==1,"existing output exception propagated at original state boundary");
    check(e.count==0&&r.settleCalls()==0,"pre-output framesDelivered is not a completed settle notification");
    r.expire(at++);check(e.count==1&&e.rows[0][0]==2&&r.settleCalls()==1&&r.pendingFrames()==0,"later outer cleanup records actual superseded settlement");
    PhoneReceiver schema;contract(schema.mappingLifecycle());
}
void unchanged(){
    PhoneReceiver control,observed;control.setMappingDiagnostics(true);observed.setMappingDiagnostics(true);
    control.setDiagnostics(true);observed.setDiagnostics(true);observed.setMappingLifecycleDiagnostics(true);uint64_t at=11000000;
    auto input=[&](const Bytes& p,uint64_t when){
        check(control.accept(p,when)==observed.accept(p,when),"ON/OFF delivered bodies identical");
        check(control.stats()==observed.stats(),"old21 stats exact after each actual datagram");
        check(control.mappingDetails()==observed.mappingDetails(),"old292 mapping detail exact after each datagram");
        check(control.drainEvents()==observed.drainEvents(),"legacy drained event records unchanged");
    };
    for(uint64_t id=2;id<=10;id++)for(const auto& p:packets(id,false))input(p,at++);
    control.expire(at+80000);observed.expire(at+80000);
    check(control.stats()==observed.stats()&&control.mappingDetails()==observed.mappingDetails()&&control.drainEvents()==observed.drainEvents(),"expiry and retire policy unchanged");
    at+=80001;for(const auto& p:packets(300,true))input(p,at++);
    input(packets(20,true)[0],at++);auto mismatch=packets(301,true);input(mismatch[0],at++);put(mismatch[1],24,123,8);input(mismatch[1],at++);
    observed.setMappingLifecycleDiagnostics(false);for(const auto& p:packets(302,true))input(p,at++);
    observed.setMappingLifecycleDiagnostics(true);for(const auto& p:packets(303,true))input(p,at++);
    const auto before=observed.mappingLifecycle();observed.expire(1);const auto after=observed.mappingLifecycle();
    check(before==after,"invalid caller timestamp does not update lifecycle clock or observations");contract(after);
}
}
int main(int argc,char** argv){try{
    if(argc!=2)throw std::invalid_argument("fixed native lifecycle fixture required");
    const std::string selected=argv[1];
    if(selected=="core")coreReasons();else if(selected=="states")coreStates();
    else if(selected=="capacity")capacityIdentity();else if(selected=="pending")livePending();
    else if(selected=="deadlines")originalDeadlines();
    else if(selected=="bounded")bounded();else if(selected=="off")offCoverage();
    else if(selected=="logical")logicalReject();else if(selected=="exception")exceptionSemantics();
    else if(selected=="unchanged")unchanged();else throw std::invalid_argument("unknown fixed fixture");
    std::cout<<"{\"case\":\""<<selected<<"\",\"passed\":true,\"checks\":"<<checks
        <<",\"lifecycle_values\":240,\"legacy_mapping_values\":292,\"legacy_stats_values\":21,\"schema_version\":1"
        <<",\"scope\":\"offline_actual_native_lifecycle_no_phone_network_or_codec\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
