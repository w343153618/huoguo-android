#pragma once
#include "logical_frame.hpp"
#include <mutex>
#include <bit>

namespace huoguo::android_udp {
inline void initializeFec() { static std::once_flag once; std::call_once(once,[]{reed_solomon_init();}); }

// The host and phone monotonic clocks are unrelated. This adapter deliberately
// grants an 80ms ASSEMBLY window from the first successfully admitted mapping
// shard, not an alleged capture-to-WAN latency limit. A capacity-rejected shard
// has no grant; later shards of an existing mapping never extend its grant.
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
    // Independent, non-destructive numeric diagnostics. The legacy 21 stats
    // and frame-event drain retain their original contracts. All event times
    // below use caller-supplied valid phone RX arrival/tick microseconds.
    static constexpr size_t MappingDetailHeaderColumns=36,MappingDetailEventColumns=8;
    static constexpr size_t MappingDetailEventCapacity=32,MappingCapacityTrackingCapacity=32;
    static constexpr size_t MappingDetailValues=MappingDetailHeaderColumns+
        MappingDetailEventCapacity*MappingDetailEventColumns;
    using MappingDetailEvent=std::array<uint64_t,MappingDetailEventColumns>;
    using MappingDetails=std::array<uint64_t,MappingDetailValues>;
    enum MappingDetailReason:uint64_t {
        MappingOldFrame=1,MappingCapacity=2,MappingHeaderMismatch=3,MappingCapacityAdmitted=4
    };
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
    bool mappingDiagnostics_=false;
    // Fixed storage: rejection/admission diagnostics never allocate per packet.
    std::array<uint64_t,3> mappingReasons_{};
    uint64_t mappingObserved_=0,mappingUnobserved_=0;
    uint64_t capacityAdapterLast_=0,capacityCoreLast_=0,capacityOldestLast_=0;
    uint64_t capacityAdapterMax_=0,capacityCoreMax_=0,capacityOldestMax_=0,adapterMax_=0;
    std::array<MappingDetailEvent,MappingDetailEventCapacity> mappingEvents_{};
    size_t mappingEventHead_=0,mappingEventCount_=0;
    uint64_t mappingEventTotal_=0,mappingEventEvicted_=0,mappingEventCleared_=0;
    uint64_t mappingEnableTransitions_=0,mappingDisableTransitions_=0;
    uint64_t newMappingsObserved_=0,admissionsAfterCapacity_=0,capacityTrackingEvicted_=0;
    struct CapacityObservation { uint64_t id=0,firstRejectedAt=0; };
    std::array<CapacityObservation,MappingCapacityTrackingCapacity> capacityObservations_{};
    uint64_t oldestMappingAge(uint64_t at)const {
        uint64_t oldest=0;
        for(const auto& [id,m]:mappings_){(void)id;oldest=std::max(oldest,at>=m.arrival?at-m.arrival:uint64_t(0));}
        return oldest;
    }
    size_t capacityTrackingActive()const {
        return size_t(std::count_if(capacityObservations_.begin(),capacityObservations_.end(),
            [](const auto& item){return item.id!=0;}));
    }
    uint64_t rememberCapacityRejection(uint64_t id,uint64_t at){
        for(const auto& item:capacityObservations_)if(item.id==id)return item.firstRejectedAt;
        auto slot=std::find_if(capacityObservations_.begin(),capacityObservations_.end(),
            [](const auto& item){return item.id==0;});
        if(slot==capacityObservations_.end()){
            slot=std::min_element(capacityObservations_.begin(),capacityObservations_.end(),
                [](const auto& a,const auto& b){return a.firstRejectedAt<b.firstRejectedAt;});
            capacityTrackingEvicted_++;
        }
        *slot={id,at};return at;
    }
    void mappingEvent(uint64_t id,uint64_t at,MappingDetailReason reason,uint64_t firstCapacity=0){
        if(mappingEventCount_==MappingDetailEventCapacity){
            mappingEventHead_=(mappingEventHead_+1)%MappingDetailEventCapacity;mappingEventEvicted_++;
        }else mappingEventCount_++;
        const auto index=(mappingEventHead_+mappingEventCount_-1)%MappingDetailEventCapacity;
        mappingEvents_[index]={++mappingEventTotal_,id,at,uint64_t(reason),mappings_.size(),
            core_.pendingFrames(),oldestMappingAge(at),firstCapacity};
    }
    void observeMappingRejection(const Header& h,uint64_t at,MappingDetailReason reason){
        if(!mappingDiagnostics_){mappingUnobserved_++;return;}
        mappingObserved_++;mappingReasons_[size_t(reason)-1]++;
        uint64_t firstCapacity=0;
        if(reason==MappingCapacity){
            capacityAdapterLast_=mappings_.size();capacityCoreLast_=core_.pendingFrames();
            capacityOldestLast_=oldestMappingAge(at);
            capacityAdapterMax_=std::max(capacityAdapterMax_,capacityAdapterLast_);
            capacityCoreMax_=std::max(capacityCoreMax_,capacityCoreLast_);
            capacityOldestMax_=std::max(capacityOldestMax_,capacityOldestLast_);
            firstCapacity=rememberCapacityRejection(h.frame,at);
        }
        mappingEvent(h.frame,at,reason,firstCapacity);
    }
    void observeNewMapping(uint64_t id,uint64_t at){
        if(!mappingDiagnostics_)return;
        newMappingsObserved_++;adapterMax_=std::max(adapterMax_,uint64_t(mappings_.size()));
        for(auto& item:capacityObservations_)if(item.id==id){
            const auto first=item.firstRejectedAt;item={};admissionsAfterCapacity_++;
            // This observes admission after emplace, before core receive. The
            // older rejection timestamp is an association only, never a grant.
            mappingEvent(id,at,MappingCapacityAdmitted,first);return;
        }
    }
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
        if(highest_>=h.frame && highest_-h.frame>=256) {
            mappingRejected_++;observeMappingRejection(h,arrival,MappingOldFrame);return take();
        }
        auto found=mappings_.find(h.frame);
        if(found==mappings_.end()) {
            if(mappings_.size()>=8) {
                mappingRejected_++;observeMappingRejection(h,arrival,MappingCapacity);return take();
            }
            found=mappings_.emplace(h.frame,Mapping(h,arrival)).first;
            highest_=std::max(highest_,h.frame);
            observeNewMapping(h.frame,arrival);
        } else if(!same(found->second.original,h)) {
            mappingRejected_++;observeMappingRejection(h,arrival,MappingHeaderMismatch);return take();
        }
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
    void setMappingDiagnostics(bool enabled){
        if(enabled==mappingDiagnostics_)return;
        mappingDiagnostics_=enabled;
        if(enabled){mappingEnableTransitions_++;adapterMax_=std::max(adapterMax_,uint64_t(mappings_.size()));}
        else{
            mappingDisableTransitions_++;mappingEventCleared_+=mappingEventCount_;
            mappingEventHead_=0;mappingEventCount_=0;capacityObservations_={};
        }
    }
    MappingDetails mappingDetails()const {
        // Header names/indexes are mirrored by NativeUdpFec.MAPPING_DETAIL_NAMES.
        // coverage_mask=15 declares the four installed hook families (reason,
        // capacity context, event ring, later admission), NOT complete coverage.
        // disabled rejections, evictions, clears and tracking loss remain visible.
        MappingDetails result{};
        const MappingDetailEvent first=mappingEventCount_?mappingEvents_[mappingEventHead_]:MappingDetailEvent{};
        const MappingDetailEvent last=mappingEventCount_?
            mappingEvents_[(mappingEventHead_+mappingEventCount_-1)%MappingDetailEventCapacity]:MappingDetailEvent{};
        const std::array<uint64_t,MappingDetailHeaderColumns> header{
            1,uint64_t(mappingDiagnostics_),15,mappingObserved_,mappingReasons_[0],mappingReasons_[1],mappingReasons_[2],
            mappingUnobserved_,capacityAdapterLast_,capacityCoreLast_,capacityOldestLast_,
            capacityAdapterMax_,capacityCoreMax_,capacityOldestMax_,adapterMax_,mappings_.size(),core_.pendingFrames(),
            mappingEventTotal_,mappingEventCount_,mappingEventEvicted_,MappingDetailEventCapacity,MappingDetailEventColumns,
            first[0],last[0],first[2],last[2],mappingEnableTransitions_,mappingDisableTransitions_,newMappingsObserved_,
            admissionsAfterCapacity_,capacityTrackingActive(),capacityTrackingEvicted_,MappingCapacityTrackingCapacity,
            now_,mappingRejected_,mappingEventCleared_};
        std::copy(header.begin(),header.end(),result.begin());
        for(size_t index=0;index<mappingEventCount_;index++){
            const auto& row=mappingEvents_[(mappingEventHead_+index)%MappingDetailEventCapacity];
            std::copy(row.begin(),row.end(),result.begin()+MappingDetailHeaderColumns+index*MappingDetailEventColumns);
        }
        return result;
    }
};
} // namespace huoguo::android_udp
