#pragma once
#include "rs.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <span>
#include <stdexcept>
#include <vector>

namespace huoguo::udp {
using Bytes = std::vector<uint8_t>;
constexpr size_t HeaderBytes = 56, ShardBytes = 1024, MaxFrameBytes = 1024*1024;
constexpr uint8_t Keyframe = 1, Reference = 2;
constexpr uint32_t SessionTag = 0x53494d31; // Synthetic namespace; NOT an authentication token.

inline void put(Bytes& data, size_t offset, uint64_t value, size_t width) {
    for (size_t index=0; index<width; index++) data[offset+width-index-1]=uint8_t(value>>(index*8));
}
inline uint64_t get(std::span<const uint8_t> data, size_t offset, size_t width) {
    uint64_t value=0;
    for(size_t index=0; index<width; index++) value=(value<<8)|data[offset+index];
    return value;
}

struct Header {
    uint8_t flags=0, shard=0, dataCount=0, parityCount=2;
    uint16_t block=0, blocks=0, shardBytes=ShardBytes;
    uint64_t frame=0, captureUs=0, reference=0;
    uint32_t frameBytes=0, blockBytes=0, lifetimeUs=80000, session=SessionTag;

    Bytes serialize(std::span<const uint8_t> payload) const {
        Bytes data(HeaderBytes+payload.size());
        data[0]='H'; data[1]='G'; data[2]='U'; data[3]='D'; data[4]=1;
        data[5]=flags; data[6]=shard; data[7]=dataCount; data[8]=parityCount;
        put(data,10,block,2); put(data,12,blocks,2); put(data,14,shardBytes,2);
        put(data,16,frame,8); put(data,24,captureUs,8); put(data,32,frameBytes,4);
        put(data,36,blockBytes,4); put(data,40,lifetimeUs,4); put(data,44,session,4);
        put(data,48,reference,8);
        std::copy(payload.begin(),payload.end(),data.begin()+HeaderBytes);
        return data;
    }

    static Header parse(std::span<const uint8_t> data) {
        if(data.size()<HeaderBytes || data[0]!='H' || data[1]!='G' || data[2]!='U' ||
           data[3]!='D' || data[4]!=1 || data[9]!=0) throw std::invalid_argument("packet prefix");
        Header h;
        h.flags=data[5]; h.shard=data[6]; h.dataCount=data[7]; h.parityCount=data[8];
        h.block=get(data,10,2); h.blocks=get(data,12,2); h.shardBytes=get(data,14,2);
        h.frame=get(data,16,8); h.captureUs=get(data,24,8); h.frameBytes=get(data,32,4);
        h.blockBytes=get(data,36,4); h.lifetimeUs=get(data,40,4); h.session=get(data,44,4);
        h.reference=get(data,48,8);
        const size_t blockCapacity=10*ShardBytes;
        const size_t expectedBlocks=(size_t(h.frameBytes)+blockCapacity-1)/blockCapacity;
        const size_t expectedBlockBytes=h.block+1==h.blocks ?
            h.frameBytes-size_t(h.block)*blockCapacity : blockCapacity;
        const size_t expectedData=(expectedBlockBytes+ShardBytes-1)/ShardBytes;
        const size_t expectedShardBytes=expectedData ? (expectedBlockBytes+expectedData-1)/expectedData : 0;
        if(h.session!=SessionTag || h.flags>3 || !h.frame || !h.frameBytes ||
           h.frameBytes>MaxFrameBytes || h.blocks!=expectedBlocks || h.block>=h.blocks ||
           h.blockBytes!=expectedBlockBytes || h.dataCount!=expectedData ||
           !h.dataCount || h.dataCount>10 || h.parityCount!=2 ||
           h.shard>=h.dataCount+h.parityCount || h.shardBytes!=expectedShardBytes ||
           data.size()!=HeaderBytes+h.shardBytes || h.lifetimeUs<1000 || h.lifetimeUs>80000 ||
           ((h.flags&Keyframe) && (!(h.flags&Reference) || h.reference)) ||
           (!(h.flags&Keyframe) && (!h.reference || h.reference>=h.frame)))
            throw std::invalid_argument("packet bounds/dependency");
        return h;
    }
};

class FecBlock {
    reed_solomon* codec_=nullptr;
    std::vector<uint8_t*> shards_;
    std::vector<uint8_t> present_;
    const size_t bytes_;
    const int padded_;
public:
    const int dataCount;
    size_t received=0;
    explicit FecBlock(int count,size_t bytes): bytes_(bytes), padded_(reed_solomon_padded_size(int(bytes))), dataCount(count) {
        codec_=reed_solomon_new(count,2);
        if(!codec_) throw std::runtime_error("nanors init");
        shards_.resize(count+2,nullptr); present_.resize(count+2,0);
        try {
            for(auto& pointer:shards_) {
                pointer=static_cast<uint8_t*>(reed_solomon_aligned_alloc(padded_));
                if(!pointer) throw std::bad_alloc();
                std::fill(pointer,pointer+padded_,0);
            }
        } catch(...) {
            for(auto pointer:shards_) reed_solomon_free(pointer);
            reed_solomon_release(codec_); throw;
        }
    }
    FecBlock(const FecBlock&)=delete;
    FecBlock& operator=(const FecBlock&)=delete;
    ~FecBlock() { for(auto pointer:shards_) reed_solomon_free(pointer); reed_solomon_release(codec_); }
    std::span<const uint8_t> shard(size_t index) const { return {shards_.at(index),bytes_}; }
    void encode(std::span<const uint8_t> data) {
        for(size_t i=0; i<data.size(); i++) shards_[i/bytes_][i%bytes_]=data[i];
        if(reed_solomon_encode(codec_,shards_.data(),int(shards_.size()),padded_))
            throw std::runtime_error("nanors encode");
    }
    bool receive(size_t index, std::span<const uint8_t> data) {
        if(present_.at(index)) return false;
        std::copy(data.begin(),data.end(),shards_[index]); present_[index]=1; received++; return true;
    }
    Bytes recover(size_t bytes, size_t& recoveredShards) {
        if(received<size_t(dataCount)) return {};
        std::vector<uint8_t> missing(present_.size());
        recoveredShards=0;
        for(size_t i=0; i<missing.size(); i++) {
            missing[i]=!present_[i];
            if(i<size_t(dataCount) && missing[i]) recoveredShards++;
        }
        if(reed_solomon_decode(codec_,shards_.data(),missing.data(),int(shards_.size()),padded_))
            throw std::runtime_error("nanors reconstruct");
        Bytes output(bytes);
        for(size_t i=0; i<bytes; i++) output[i]=shards_[i/bytes_][i%bytes_];
        return output;
    }
};

inline std::vector<Bytes> packetize(std::span<const uint8_t> frame, uint64_t id,
                                  uint64_t captureUs, uint8_t flags, uint64_t reference,
                                  uint32_t lifetimeUs=80000) {
    if(frame.empty() || frame.size()>MaxFrameBytes || !id || lifetimeUs>80000 || lifetimeUs<1000)
        throw std::invalid_argument("source frame");
    std::vector<Bytes> output;
    const size_t capacity=10*ShardBytes, blocks=(frame.size()+capacity-1)/capacity;
    for(size_t i=0; i<blocks; i++) {
        const size_t begin=i*capacity, bytes=std::min(capacity,frame.size()-begin);
        const int count=int((bytes+ShardBytes-1)/ShardBytes);
        const size_t shardBytes=(bytes+size_t(count)-1)/size_t(count);
        FecBlock block(count,shardBytes); block.encode(frame.subspan(begin,bytes));
        Header h;
        h.flags=flags; h.dataCount=uint8_t(count); h.block=uint16_t(i); h.blocks=uint16_t(blocks);
        h.shardBytes=uint16_t(shardBytes);
        h.frame=id; h.captureUs=captureUs; h.frameBytes=uint32_t(frame.size());
        h.blockBytes=uint32_t(bytes); h.reference=reference; h.lifetimeUs=lifetimeUs;
        for(int shard=0; shard<count+2; shard++) {
            h.shard=uint8_t(shard); output.push_back(h.serialize(block.shard(shard)));
        }
    }
    return output;
}

inline size_t packetizedWireBytes(size_t frameBytes) {
    size_t output=0;
    while(frameBytes) {
        const size_t blockBytes=std::min(frameBytes,10*ShardBytes);
        const size_t data=(blockBytes+ShardBytes-1)/ShardBytes;
        const size_t shardBytes=(blockBytes+data-1)/data;
        output+=(data+2)*(shardBytes+HeaderBytes+28);
        frameBytes-=blockBytes;
    }
    return output;
}

struct Stats {
    uint64_t packets=0, wireBytes=0, invalid=0, duplicate=0, settledPackets=0;
    uint64_t expiredPackets=0, framesExpired=0, framesDelivered=0, recoveredShards=0;
    uint64_t referenceLost=0, keyframeRequests=0, dependencyDropped=0, memoryRejected=0;
};

class Receiver {
    struct Part {
        std::unique_ptr<FecBlock> fec;
        Bytes payload;
        explicit Part(int count,size_t bytes):fec(std::make_unique<FecBlock>(count,bytes)) {}
    };
    struct Frame {
        Header h;
        std::map<uint16_t,Part> parts;
        size_t recovered=0;
        explicit Frame(Header header):h(header) {}
        bool complete() const {
            if(parts.size()!=h.blocks) return false;
            for(const auto& [id, part]:parts) { (void)id; if(part.payload.empty()) return false; }
            return true;
        }
    };
    std::map<uint64_t,Frame> pending_;
    std::deque<uint64_t> settled_;
    uint64_t lastReference_=0, lastDelivered_=0;
    bool needsKeyframe_=true;
    std::function<void(uint64_t,uint64_t,const Bytes&)> output_;
    void settle(uint64_t id) { settled_.push_back(id); if(settled_.size()>256) settled_.pop_front(); }
    void requestKeyframe(uint64_t captureUs) {
        if(!needsKeyframe_ || !recoveryCaptureUs) { stats.keyframeRequests++; recoveryCaptureUs=captureUs; }
        needsKeyframe_=true;
    }
    void lostReference(uint64_t captureUs) { stats.referenceLost++; requestKeyframe(captureUs); }
    static bool sameFrame(const Header& a,const Header& b) {
        return a.flags==b.flags && a.captureUs==b.captureUs && a.reference==b.reference &&
               a.frameBytes==b.frameBytes && a.blocks==b.blocks && a.lifetimeUs==b.lifetimeUs;
    }
public:
    Stats stats;
    uint64_t recoveryCaptureUs=0;
    explicit Receiver(std::function<void(uint64_t,uint64_t,const Bytes&)> output):output_(std::move(output)) {}
    size_t pendingFrames() const { return pending_.size(); }
    bool needsKeyframe() const { return needsKeyframe_; }
    void expire(uint64_t nowUs) {
        for(auto it=pending_.begin(); it!=pending_.end();) {
            if(it->first<=lastDelivered_) {
                stats.dependencyDropped++; settle(it->first); it=pending_.erase(it);
            } else if(nowUs>=it->second.h.captureUs+it->second.h.lifetimeUs) {
                stats.framesExpired++;
                if(it->second.h.flags&Reference) lostReference(it->second.h.captureUs);
                settle(it->first); it=pending_.erase(it);
            } else ++it;
        }
        drain(nowUs);
    }
    void drain(uint64_t nowUs) {
        for(auto it=pending_.begin(); it!=pending_.end();) {
            auto& frame=it->second;
            if(!frame.complete() || nowUs>=frame.h.captureUs+frame.h.lifetimeUs) { ++it; continue; }
            const bool key=frame.h.flags&Keyframe;
            if(frame.h.frame<=lastDelivered_) {
                stats.dependencyDropped++; settle(it->first); it=pending_.erase(it); continue;
            }
            const bool earlierKeyPending=std::any_of(pending_.begin(),it,[](const auto& item) {
                return item.second.h.flags&Keyframe;
            });
            if(!key && ((needsKeyframe_ && !earlierKeyPending) || frame.h.reference<lastReference_)) {
                if(needsKeyframe_ && !earlierKeyPending) requestKeyframe(frame.h.captureUs);
                stats.dependencyDropped++; settle(it->first); it=pending_.erase(it); continue;
            }
            if(!key && (needsKeyframe_ || frame.h.reference!=lastReference_)) { ++it; continue; }
            Bytes data; data.reserve(frame.h.frameBytes);
            for(auto& [partId, part]:frame.parts) {
                (void)partId; data.insert(data.end(),part.payload.begin(),part.payload.end());
            }
            stats.framesDelivered++; stats.recoveredShards+=frame.recovered;
            lastDelivered_=frame.h.frame;
            if(key) needsKeyframe_=false;
            if(frame.h.flags&Reference) lastReference_=frame.h.frame;
            output_(frame.h.frame,frame.h.captureUs,data);
            if(key) recoveryCaptureUs=0;
            settle(it->first); it=pending_.erase(it);
            // A dependency that arrived later may unblock a lower map entry.
            it=pending_.begin();
        }
    }
    void receive(std::span<const uint8_t> packet, uint64_t nowUs) {
        stats.packets++; stats.wireBytes+=packet.size();
        Header h;
        try { h=Header::parse(packet); } catch(const std::invalid_argument&) { stats.invalid++; return; }
        // Single-host prototype clock domain. Remote capture times must first be
        // mapped to this local monotonic clock through an authenticated session.
        if(h.captureUs>nowUs+10000) { stats.invalid++; return; }
        if(nowUs>=h.captureUs+h.lifetimeUs) { stats.expiredPackets++; expire(nowUs); return; }
        if(h.frame<=lastDelivered_ || std::find(settled_.begin(),settled_.end(),h.frame)!=settled_.end()) {
            stats.settledPackets++; return;
        }
        expire(nowUs);
        auto found=pending_.find(h.frame);
        if(found==pending_.end()) {
            if(pending_.size()>=8) { stats.memoryRejected++; return; }
            found=pending_.emplace(h.frame,Frame(h)).first;
        }
        auto& frame=found->second;
        if(!sameFrame(frame.h,h)) { stats.invalid++; return; }
        auto part=frame.parts.find(h.block);
        if(part==frame.parts.end()) part=frame.parts.emplace(h.block,Part(h.dataCount,h.shardBytes)).first;
        if(!part->second.payload.empty()) { stats.duplicate++; return; }
        if(!part->second.fec->receive(h.shard,packet.subspan(HeaderBytes))) { stats.duplicate++; return; }
        if(part->second.fec->received>=h.dataCount) {
            size_t recovered=0;
            part->second.payload=part->second.fec->recover(h.blockBytes,recovered);
            frame.recovered+=recovered;
            part->second.fec.reset();
        }
        drain(nowUs);
    }
};

// Deadline-aware byte pacing. The default has strict reanchoring after stalls.
// Experimental catch-up credit is bounded in bytes; it must be settled against
// each actual write time, so a late write cannot gain an extra full bucket.
class Pacer {
    uint64_t bitrate_, nextUs_=0;
    const size_t burstBytes_;
    uint64_t reservedAtUs_=0,reservedCostUs_=0,previousNextUs_=0;
    bool pending_=false;
    uint64_t creditUs()const {
        // Round DOWN: at high rates rounding up could grant a few extra bytes
        // beyond the bucket in an all-small-packet sliding window.
        return burstBytes_*8*1000000ULL/bitrate_;
    }
    uint64_t virtualReadyUs(uint64_t nowUs)const {
        const auto credit=creditUs();
        return std::max(nextUs_,nowUs>credit?nowUs-credit:uint64_t(0));
    }
    void reserve(uint64_t nowUs,uint64_t at,uint64_t cost) {
        // at is the actual next slot; only debt accounting may start in the
        // bounded past. Never raise the complete-frame admission budget.
        previousNextUs_=nextUs_;nextUs_=virtualReadyUs(nowUs)+cost;
        if(burstBytes_){reservedAtUs_=at;reservedCostUs_=cost;pending_=true;}
    }
    void requireSettled()const {
        if(burstBytes_&&pending_)throw std::logic_error("pacer actual write time not settled");
    }
public:
    explicit Pacer(uint64_t bitrate,size_t burstBytes=0):bitrate_(bitrate),burstBytes_(burstBytes) {
        if(bitrate<500000 || bitrate>40000000) throw std::invalid_argument("wire bitrate");
        if(burstBytes>4096)throw std::invalid_argument("pacer burst byte bound");
    }
    uint64_t slot(uint64_t nowUs,size_t bytes) {
        requireSettled();const uint64_t at=readyUs(nowUs);
        const uint64_t interval=serializationUs(bytes);
        reserve(nowUs,at,interval);return at;
    }
    uint64_t readyUs(uint64_t nowUs) const { return std::max(nowUs,nextUs_); }
    size_t burstBytes()const{return burstBytes_;}
    uint64_t serializationUs(size_t bytes) const {
        return (bytes*8*1000000ULL+bitrate_-1)/bitrate_;
    }
    std::optional<uint64_t> reserveBefore(uint64_t nowUs,size_t bytes,uint64_t deadlineUs) {
        requireSettled();
        const uint64_t at=readyUs(nowUs),cost=serializationUs(bytes);
        // Never advance the future schedule for a packet that will not be sent.
        if(at>=deadlineUs || cost>deadlineUs-at) return {};
        reserve(nowUs,at,cost);return at;
    }
    // Call immediately after a successful complete write. The strict default
    // ignores this method for compatibility. A catch-up caller must settle
    // every reservation before reserving another packet, even after oversleep.
    void sentAt(uint64_t actualWriteUs) {
        if(!burstBytes_)return;
        if(!pending_||actualWriteUs<reservedAtUs_)throw std::invalid_argument("pacer actual write time");
        const auto credit=creditUs();
        const auto floor=actualWriteUs>credit?actualWriteUs-credit:uint64_t(0);
        nextUs_=std::max(nextUs_,floor+reservedCostUs_);pending_=false;
    }
    void cancelReservation() {
        if(!burstBytes_)return;
        if(!pending_)throw std::logic_error("pacer reservation is not pending");
        nextUs_=previousNextUs_;pending_=false;
    }
};
}
