#include "phone_receiver.hpp"
#include <iostream>
#include <random>

using namespace huoguo::android_udp;
namespace {
void check(bool okay,const char* name) { if(!okay) throw std::runtime_error(name); }
Bytes logical(bool key,size_t bytes=14000) {
    Bytes nal(bytes,0x7f); nal[0]=0; nal[1]=0; nal[2]=0; nal[3]=1; nal[4]=key?0x65:0x41;
    return serializeLogical({540,1200,key?IdrFlag:0,{},nal});
}
std::vector<Bytes> fixture(uint64_t id,bool key,uint64_t capture=987654321234ULL) {
    return packetize(logical(key),id,capture,uint8_t(Reference|(key?Keyframe:0)),key?0:id-1);
}
void selfTest() {
    initializeFec();
    PhoneReceiver skew; const auto body=logical(true); auto first=fixture(1,true);
    std::vector<Bytes> delivered; uint64_t at=1000000;
    // Two erased DATA shards in each block; parity must reconstruct exact bytes.
    std::reverse(first.begin(),first.end());
    for(const auto& p:first) if(Header::parse(p).shard>=2) {
        auto out=skew.accept(p,at++); delivered.insert(delivered.end(),out.begin(),out.end());
    }
    check(delivered.size()==1 && delivered[0]==body,"host/phone clock skew + FEC exact body");
    auto stats=skew.stats(); check(stats[8]>=4 && stats[19]==0 && stats[14]==0,"FEC recovery and bounded mapping cleanup");
    skew.accept(first.back(),at++); check(skew.stats()[4]>0,"settled parity replay");

    PhoneReceiver mismatch; const auto p=fixture(1,true);
    mismatch.accept(p[0],2000000); auto bad=p[1]; put(bad,24,123,8);
    mismatch.accept(bad,2000001); check(mismatch.stats()[13]==1,"original capture mismatch rejected");
    mismatch.expire(2080000); const auto before=mismatch.stats();
    for(const auto& shard:p) mismatch.accept(shard,2080001);
    check(mismatch.stats()[18]==0 && mismatch.stats()[14]==0 && mismatch.stats()[16]==1,"expired frame cannot restart assembly deadline");
    auto next=fixture(2,true); for(const auto& shard:next) mismatch.accept(shard,2081000);
    check(mismatch.stats()[18]==1 && mismatch.stats()[19]==0 && before[6]==1,"new IDR restores dependency");

    PhoneReceiver newerIdr; newerIdr.accept(p[0],2500000);
    for(const auto& shard:next) newerIdr.accept(shard,2500010);
    check(newerIdr.stats()[18]==1 && newerIdr.stats()[14]==0,"newer IDR immediately retires older unresolved mappings");
    newerIdr.accept(p[1],2500020); check(newerIdr.stats()[4]>0 && newerIdr.stats()[14]==0,"older abandoned frame cannot reacquire mapping after IDR");

    PhoneReceiver bounded; uint64_t when=3000000;
    for(uint64_t id=1;id<=9;id++) { const auto part=fixture(id,true); bounded.accept(part[0],when++); }
    check(bounded.stats()[14]==8 && bounded.stats()[13]==1,"at most eight assembly mappings");
    bounded.expire(3080009); check(bounded.stats()[14]==0 && bounded.stats()[16]==8,"mapping expiration remains bounded");
    bounded.accept(fixture(300,true)[0],3090000);
    bounded.accept(fixture(20,true)[0],3090001); check(bounded.stats()[13]>=2,"old replay outside 256 frame window rejected");
    bounded.expire(1); check(bounded.stats()[2]>=1,"phone monotonic regression rejected");

    auto malformed=body; put(malformed,16,70000,4);
    bool rejected=false; try { parseLogical(malformed); } catch(const std::invalid_argument&) { rejected=true; }
    check(rejected,"logical config bound");
    std::cout<<"{\"scope\":\"synthetic_control_not_real_h264\",\"passed\":true,\"cases\":13}\n";
}
}
int main(int argc,char** argv) {
    try {
        if(argc==2 && std::string(argv[1])=="--self-test") { selfTest(); return 0; }
        bool two=false; double loss=0; size_t reorder=1; uint32_t seed=7;
        for(int i=1;i<argc;i++) {
            const std::string arg=argv[i];
            if(arg=="--drop-two-data") two=true;
            else if(arg=="--loss" && i+1<argc) loss=std::stod(argv[++i]);
            else if(arg=="--reorder" && i+1<argc) reorder=std::stoul(argv[++i]);
            else if(arg=="--seed" && i+1<argc) seed=uint32_t(std::stoul(argv[++i]));
            else throw std::invalid_argument("probe argument");
        }
        if(loss<0 || loss>1 || reorder<1 || reorder>32) throw std::invalid_argument("probe bounds");
        PhoneReceiver receiver; std::mt19937 random(seed); std::uniform_real_distribution<double> chance(0,1);
        std::vector<Bytes> group; uint64_t at=100000000,dropped=0,input=0;
        auto deliver=[&](const Bytes& packet){
            const auto h=Header::parse(packet);
            if((two && h.shard<2) || chance(random)<loss) { dropped++; return; }
            for(const auto& body:receiver.accept(packet,at++)) {
                Bytes prefix(4); put(prefix,0,body.size(),4);
                std::cout.write(reinterpret_cast<const char*>(prefix.data()),4);
                std::cout.write(reinterpret_cast<const char*>(body.data()),std::streamsize(body.size()));
            }
        };
        std::array<uint8_t,2> length{};
        while(readBytes(std::cin,length,true)) {
            const auto size=get(length,0,2); if(size<HeaderBytes || size>HeaderBytes+ShardBytes) throw std::invalid_argument("IPC packet size");
            Bytes packet(size); readBytes(std::cin,packet); input++; group.push_back(std::move(packet));
            if(group.size()==reorder) { for(auto it=group.rbegin();it!=group.rend();it++) deliver(*it); group.clear(); }
        }
        for(auto it=group.rbegin();it!=group.rend();it++) deliver(*it);
        receiver.expire(at+80000); const auto s=receiver.stats();
        std::cerr<<"{\"scope\":\"offline_native_receiver_not_phone_or_WAN\",\"input_packets\":"<<input<<",\"dropped_packets\":"<<dropped<<",\"stats\":[";
        for(size_t i=0;i<s.size();i++) std::cerr<<(i?",":"")<<s[i]; std::cerr<<"]}\n"; return 0;
    } catch(const std::exception& error) { std::cerr<<"probe failed: "<<error.what()<<"\n"; return 1; }
}
