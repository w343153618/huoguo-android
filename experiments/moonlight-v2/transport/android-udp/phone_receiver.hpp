#pragma once
#include "logical_frame.hpp"
#include <mutex>
#include <bit>

namespace huoguo::android_udp {
inline void initializeFec() { static std::once_flag once; std::call_once(once,[]{reed_solomon_init();}); }

// The host and phone monotonic clocks are unrelated. This adapter deliberately
// grants an 80ms ASSEMBLY window from the first authenticated shard, not an
// alleged capture-to-WAN latency limit. Each frame can receive that grant once.
class PhoneReceiver {
    struct SeenBlock {
        uint8_t count;uint16_t shardBytes,mask=0;uint32_t bytes;
        explicit SeenBlock(const Header& h):count(h.dataCount),shardBytes(h.shardBytes),bytes(h.blockBytes){}
        bool ready()const{return std::popcount(mask)>=count;}
    };
    struct Mapping {
        Header original;uint64_t arrival,lastArrival,quorumReady=0;bool metadataValid=true;
        std::map<uint16_t,SeenBlock> seen;
        Mapping(const Header& h,uint64_t at):original(h),arrival(at),lastArrival(at){}
    };
public:
    // Numeric metadata only; no media bytes, keys, addresses or packet contents.
    // Columns are documented in NativeUdpFec.EVENT_NAMES. Times use the RX
    // arrival/tick domain supplied by the caller, not a cross-host latency.
    using FrameEvent=std::array<uint64_t,14>;
private:
    std::map<uint64_t,Mapping> mappings_;
    std::deque<uint64_t> retired_;
    std::vector<Bytes> output_;
    Receiver core_;
    uint64_t now_=0,highest_=0,deliveredThrough_=0;
    uint64_t packets_=0,bytes_=0,invalid_=0,settled_=0,mappingRejected_=0;
    uint64_t mappingExpired_=0,logicalRejected_=0,bodies_=0,maxAssembly_=0;
    bool logicalNeedsKeyframe_=false;
    bool diagnostics_=false;
    std::deque<FrameEvent> events_;
    uint64_t eventsEvicted_=0,eventSequence_=0;
    static constexpr size_t EventCapacity=256,EventBatch=64;
    void event(uint64_t type,uint64_t id,const Mapping& m,uint64_t reason,uint64_t pts=0){
        if(!diagnostics_)return;
        if(events_.size()>=EventCapacity){events_.pop_front();eventsEvicted_++;}
        events_.push_back({type,id,m.original.captureUs,m.original.reference,m.original.flags,
            m.arrival,m.lastArrival,m.quorumReady,now_,m.arrival+m.original.lifetimeUs,
            m.original.frameBytes,reason,++eventSequence_,pts});
    }
    void observeShard(Mapping& m,const Header& h,uint64_t arrival){
        if(!diagnostics_)return;
        m.lastArrival=arrival;
        auto block=m.seen.find(h.block);
        if(block==m.seen.end())block=m.seen.emplace(h.block,SeenBlock(h)).first;
        auto& seen=block->second;
        if(seen.count!=h.dataCount||seen.shardBytes!=h.shardBytes||seen.bytes!=h.blockBytes){m.metadataValid=false;return;}
        seen.mask|=uint16_t(1U<<h.shard);
        // This marks mathematical FEC quorum; logical validation and ordered
        // delivery may happen later. It is not a decoder-ready timestamp.
        if(!m.quorumReady&&m.metadataValid&&m.seen.size()==h.blocks&&
            std::all_of(m.seen.begin(),m.seen.end(),[](const auto& x){return x.second.ready();})){
            m.quorumReady=arrival;event(1,h.frame,m,0);
        }
    }
    static bool same(const Header& a,const Header& b) {
        return a.captureUs==b.captureUs && a.flags==b.flags && a.reference==b.reference &&
            a.frameBytes==b.frameBytes && a.blocks==b.blocks && a.lifetimeUs==b.lifetimeUs && a.session==b.session;
    }
    void retire(uint64_t id) {
        mappings_.erase(id);
        if(std::find(retired_.begin(),retired_.end(),id)==retired_.end()) retired_.push_back(id);
        if(retired_.size()>256) retired_.pop_front();
    }
    void finish() {
        // A newer delivered IDR settles its older unresolved dependencies in
        // the core too. Retaining their adapter mappings would waste the eight
        // frame budget until expiry and could obstruct the recovered stream.
        std::vector<uint64_t> settled;
        for(const auto& [id,mapping]:mappings_) { (void)mapping; if(id<=deliveredThrough_) settled.push_back(id); }
        for(auto id:settled){auto found=mappings_.find(id);if(id<deliveredThrough_)event(4,id,found->second,4);retire(id);}
    }
    void expireInternal(uint64_t now) {
        now_=now; core_.expire(now); finish();
        std::vector<uint64_t> expired;
        for(const auto& [id,m]:mappings_) if(now>=m.arrival+m.original.lifetimeUs) expired.push_back(id);
        for(auto id:expired) { auto found=mappings_.find(id);event(3,id,found->second,3);mappingExpired_++;retire(id); }
    }
    std::vector<Bytes> take() { std::vector<Bytes> result; result.swap(output_); return result; }
public:
    PhoneReceiver():core_([this](uint64_t id,uint64_t capture,const Bytes& body){
        deliveredThrough_=std::max(deliveredThrough_,id);
        try {
            const auto frame=parseLogical(body);
            const bool key=frame.flaggedPts&IdrFlag;
            const auto map=mappings_.find(id);
            if(map==mappings_.end() || key!=bool(map->second.original.flags&Keyframe)) throw std::invalid_argument("body/header IDR mismatch");
            if(logicalNeedsKeyframe_ && !key) { event(5,id,map->second,6,frame.flaggedPts&PtsMask);logicalRejected_++;return; }
            if(key) logicalNeedsKeyframe_=false;
            event(2,id,map->second,key?1:2,frame.flaggedPts&PtsMask);
            maxAssembly_=std::max(maxAssembly_,now_-capture); bodies_++; output_.push_back(body);
        } catch(const std::invalid_argument&) {
            auto map=mappings_.find(id);if(map!=mappings_.end())event(5,id,map->second,5);
            logicalRejected_++;logicalNeedsKeyframe_=true;
        }
    }) { initializeFec(); }
    std::vector<Bytes> accept(std::span<const uint8_t> packet,uint64_t arrival) {
        packets_++; bytes_+=packet.size();
        if(!arrival || arrival<now_ || arrival>uint64_t(INT64_MAX)-80000) { invalid_++; return take(); }
        expireInternal(arrival);
        Header h;
        try { h=Header::parse(packet); } catch(const std::invalid_argument&) { invalid_++; return take(); }
        if(std::find(retired_.begin(),retired_.end(),h.frame)!=retired_.end()) { settled_++; return take(); }
        if(highest_>=h.frame && highest_-h.frame>=256) { mappingRejected_++; return take(); }
        auto found=mappings_.find(h.frame);
        if(found==mappings_.end()) {
            if(mappings_.size()>=8) { mappingRejected_++; return take(); }
            found=mappings_.emplace(h.frame,Mapping(h,arrival)).first;
            highest_=std::max(highest_,h.frame);
        } else if(!same(found->second.original,h)) { mappingRejected_++; return take(); }
        observeShard(found->second,h,arrival);
        Bytes local(packet.begin(),packet.end()); put(local,24,found->second.arrival,8);
        core_.receive(local,arrival); finish(); return take();
    }
    std::vector<Bytes> expire(uint64_t now) {
        if(!now || now<now_ || now>uint64_t(INT64_MAX)-80000) { invalid_++; return take(); }
        expireInternal(now); return take();
    }
    std::array<uint64_t,21> stats() const {
        const auto& s=core_.stats;
        return {packets_,bytes_,invalid_+s.invalid,s.duplicate,settled_+s.settledPackets,
            s.expiredPackets,s.framesExpired,s.framesDelivered,s.recoveredShards,s.referenceLost,
            s.keyframeRequests,s.dependencyDropped,s.memoryRejected,mappingRejected_,mappings_.size(),
            0,mappingExpired_,logicalRejected_,bodies_,uint64_t(core_.needsKeyframe()||logicalNeedsKeyframe_),maxAssembly_};
    }
    void setDiagnostics(bool enabled){diagnostics_=enabled;if(!enabled)events_.clear();}
    std::vector<FrameEvent> drainEvents(){
        std::vector<FrameEvent> result;result.reserve(std::min(EventBatch,events_.size()));
        while(!events_.empty()&&result.size()<EventBatch){result.push_back(events_.front());events_.pop_front();}
        return result;
    }
    std::array<uint64_t,4> eventStats()const{return {uint64_t(diagnostics_),events_.size(),eventsEvicted_,eventSequence_};}
};
} // namespace huoguo::android_udp
