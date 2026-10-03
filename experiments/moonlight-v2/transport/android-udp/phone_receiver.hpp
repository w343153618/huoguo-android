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
        uint64_t observedCoreSettleReason=0,observedCoreSettleAt=0;
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
    // Separate, default-off contract. The legacy 292 mapping values, 21 stats
    // and drained frame-event records remain unchanged.
    static constexpr size_t LifecycleHeaderColumns=32,LifecycleSettleCapacity=16,LifecycleSettleColumns=4;
    static constexpr size_t LifecycleCapacitySnapshots=2,LifecycleCapacityHeaderColumns=8;
    static constexpr size_t LifecycleOccupantCapacity=8,LifecycleOccupantColumns=8,LifecycleDedupCapacity=32;
    static constexpr size_t LifecycleSnapshotColumns=LifecycleCapacityHeaderColumns+
        LifecycleOccupantCapacity*LifecycleOccupantColumns;
    static constexpr size_t LifecycleValues=LifecycleHeaderColumns+
        LifecycleSettleCapacity*LifecycleSettleColumns+LifecycleCapacitySnapshots*LifecycleSnapshotColumns;
    using LifecycleSettleEvent=std::array<uint64_t,LifecycleSettleColumns>;
    using LifecycleCapacitySnapshot=std::array<uint64_t,LifecycleSnapshotColumns>;
    using LifecycleDetails=std::array<uint64_t,LifecycleValues>;
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
    bool lifecycleDiagnostics_=false;
    uint64_t lifecycleObserved_=0,lifecycleSettleEvicted_=0,lifecycleSettleCleared_=0;
    std::array<uint64_t,4> lifecycleReasons_{};
    std::array<LifecycleSettleEvent,LifecycleSettleCapacity> lifecycleSettles_{};
    size_t lifecycleSettleHead_=0,lifecycleSettleCount_=0;
    uint64_t lifecycleCapacityCalls_=0,lifecycleCapacityUnobserved_=0;
    uint64_t lifecycleSnapshotsTotal_=0,lifecycleSnapshotsEvicted_=0,lifecycleSnapshotsCleared_=0;
    uint64_t lifecycleSuppressed_=0,lifecycleDedupEvicted_=0;
    std::array<LifecycleCapacitySnapshot,LifecycleCapacitySnapshots> lifecycleSnapshots_{};
    size_t lifecycleSnapshotHead_=0,lifecycleSnapshotCount_=0;
    std::array<uint64_t,LifecycleDedupCapacity> lifecycleDedup_{};
    size_t lifecycleDedupHead_=0,lifecycleDedupCount_=0;
    uint64_t lifecycleEnableTransitions_=0,lifecycleDisableTransitions_=0,lifecycleLastEnable_=0;
    void observeCoreSettle(Receiver::SettleReason reason,uint64_t id,uint64_t at) noexcept {
        // Installed only while enabled. Fixed metadata writes and an existing
        // map lookup: no allocation, erase, core reentry, lock, I/O or waiting.
        lifecycleObserved_++;lifecycleReasons_[uint64_t(reason)-1]++;
        if(lifecycleSettleCount_==LifecycleSettleCapacity){
            lifecycleSettleHead_=(lifecycleSettleHead_+1)%LifecycleSettleCapacity;lifecycleSettleEvicted_++;
        }else lifecycleSettleCount_++;
        const auto index=(lifecycleSettleHead_+lifecycleSettleCount_-1)%LifecycleSettleCapacity;
        lifecycleSettles_[index]={lifecycleObserved_,id,uint64_t(reason),at};
        const auto found=mappings_.find(id);
        if(found!=mappings_.end()){
            found->second.observedCoreSettleReason=uint64_t(reason);found->second.observedCoreSettleAt=at;
        }
    }
    void observeLifecycleCapacity(const Header& h,uint64_t at) noexcept {
        lifecycleCapacityCalls_++;
        if(std::find(lifecycleDedup_.begin(),lifecycleDedup_.end(),h.frame)!=lifecycleDedup_.end()){
            lifecycleSuppressed_++;return;
        }
        if(lifecycleDedupCount_==LifecycleDedupCapacity){
            lifecycleDedup_[lifecycleDedupHead_]=h.frame;
            lifecycleDedupHead_=(lifecycleDedupHead_+1)%LifecycleDedupCapacity;lifecycleDedupEvicted_++;
        }else{
            lifecycleDedup_[(lifecycleDedupHead_+lifecycleDedupCount_)%LifecycleDedupCapacity]=h.frame;
            lifecycleDedupCount_++;
        }
        if(lifecycleSnapshotCount_==LifecycleCapacitySnapshots){
            lifecycleSnapshotHead_=(lifecycleSnapshotHead_+1)%LifecycleCapacitySnapshots;lifecycleSnapshotsEvicted_++;
        }else lifecycleSnapshotCount_++;
        const auto index=(lifecycleSnapshotHead_+lifecycleSnapshotCount_-1)%LifecycleCapacitySnapshots;
        auto& snapshot=lifecycleSnapshots_[index];snapshot={};
        const std::array<uint64_t,LifecycleCapacityHeaderColumns> header{
            ++lifecycleSnapshotsTotal_,h.frame,at,h.flags,h.reference,mappings_.size(),
            core_.pendingFrames(),mappings_.size()};
        std::copy(header.begin(),header.end(),snapshot.begin());
        size_t row=0;
        for(const auto& [id,m]:mappings_){
            const std::array<uint64_t,LifecycleOccupantColumns> occupant{id,m.arrival,
                m.arrival+m.original.lifetimeUs,m.original.flags,m.original.reference,core_.frameState(id),
                m.observedCoreSettleReason,m.observedCoreSettleAt};
            std::copy(occupant.begin(),occupant.end(),snapshot.begin()+LifecycleCapacityHeaderColumns+row*LifecycleOccupantColumns);
            row++;
        }
    }
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
                mappingRejected_++;observeMappingRejection(h,arrival,MappingCapacity);
                if(lifecycleDiagnostics_)observeLifecycleCapacity(h,arrival);
                else lifecycleCapacityUnobserved_++;
                return take();
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
    void setMappingLifecycleDiagnostics(bool enabled) noexcept {
        if(enabled==lifecycleDiagnostics_)return;
        lifecycleDiagnostics_=enabled;
        if(enabled){
            lifecycleEnableTransitions_++;lifecycleLastEnable_=now_;
            core_.setSettleObserver(this,[](void* context,Receiver::SettleReason reason,uint64_t id,uint64_t at) noexcept {
                static_cast<PhoneReceiver*>(context)->observeCoreSettle(reason,id,at);
            });
        }else{
            core_.setSettleObserver(nullptr,nullptr);lifecycleDisableTransitions_++;
            lifecycleSettleCleared_+=lifecycleSettleCount_;lifecycleSettleHead_=0;lifecycleSettleCount_=0;lifecycleSettles_={};
            lifecycleSnapshotsCleared_+=lifecycleSnapshotCount_;lifecycleSnapshotHead_=0;lifecycleSnapshotCount_=0;lifecycleSnapshots_={};
            lifecycleDedup_={};lifecycleDedupHead_=0;lifecycleDedupCount_=0;
            for(auto& [id,m]:mappings_){(void)id;m.observedCoreSettleReason=0;m.observedCoreSettleAt=0;}
        }
    }
    LifecycleDetails mappingLifecycle()const noexcept {
        // Actual settle calls, not pre-output framesDelivered: an existing
        // output callback may throw before the original settle point. The
        // default OFF path keeps only this scalar and installs no observer.
        const auto terminated=core_.settleCalls();
        LifecycleDetails result{};
        const std::array<uint64_t,LifecycleHeaderColumns> header{
            1,uint64_t(lifecycleDiagnostics_),15,lifecycleObserved_,terminated-lifecycleObserved_,
            lifecycleReasons_[0],lifecycleReasons_[1],lifecycleReasons_[2],lifecycleReasons_[3],
            lifecycleObserved_,lifecycleSettleCount_,lifecycleSettleEvicted_,lifecycleSettleCleared_,
            LifecycleSettleCapacity,LifecycleSettleColumns,lifecycleCapacityCalls_,lifecycleCapacityUnobserved_,
            lifecycleSnapshotsTotal_,lifecycleSnapshotCount_,lifecycleSnapshotsEvicted_,lifecycleSnapshotsCleared_,
            lifecycleSuppressed_,LifecycleCapacitySnapshots,LifecycleCapacityHeaderColumns,
            LifecycleOccupantCapacity,LifecycleOccupantColumns,lifecycleEnableTransitions_,lifecycleDisableTransitions_,
            now_,LifecycleDedupCapacity,lifecycleDedupEvicted_,lifecycleLastEnable_};
        std::copy(header.begin(),header.end(),result.begin());
        for(size_t row=0;row<lifecycleSettleCount_;row++){
            const auto& event=lifecycleSettles_[(lifecycleSettleHead_+row)%LifecycleSettleCapacity];
            std::copy(event.begin(),event.end(),result.begin()+LifecycleHeaderColumns+row*LifecycleSettleColumns);
        }
        constexpr auto offset=LifecycleHeaderColumns+LifecycleSettleCapacity*LifecycleSettleColumns;
        for(size_t row=0;row<lifecycleSnapshotCount_;row++){
            const auto& snapshot=lifecycleSnapshots_[(lifecycleSnapshotHead_+row)%LifecycleCapacitySnapshots];
            std::copy(snapshot.begin(),snapshot.end(),result.begin()+offset+row*LifecycleSnapshotColumns);
        }
        return result;
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
