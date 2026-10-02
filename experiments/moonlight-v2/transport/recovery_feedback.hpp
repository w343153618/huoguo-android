#pragma once
#include <cstdint>
#include <optional>

namespace huoguo::udp {

// Control requests, never media retransmissions. The caller deduplicates each
// round's sequence before asking the encoder for one genuine IDR. All times use
// the same monotonic clock; this policy is also tested with a deterministic one.
class RecoveryFeedback {
public:
    static constexpr uint64_t InitialDelayUs=5000;
    static constexpr uint64_t PacketRetryUs=20000;
    static constexpr unsigned PacketsPerRound=3;
    static constexpr unsigned MaxRounds=3;
    struct Counters {
        uint64_t episodes=0, rounds=0, packets=0, duplicatePackets=0;
        uint64_t recoveredEpisodes=0, exhaustedEpisodes=0, budgetBlockedEpisodes=0;
        uint64_t budgetBlockedTimeUs=0;
    } counters;

    // episode is Receiver.stats.keyframeRequests: one unresolved reference
    // outage, not a count of upstream encoder requests. Returning a sequence
    // indicates a bounded feedback packet due now. blocked means the sender
    // proved the IDR's serialization alone cannot fit the media lifetime.
    std::optional<uint64_t> tick(uint64_t now, uint64_t episode,
                                 bool needsKeyframe, bool blocked) {
        if(active_ && blocked_ && now>=previousTick_) counters.budgetBlockedTimeUs+=now-previousTick_;
        previousTick_=now;
        if(!needsKeyframe) {
            if(active_) counters.recoveredEpisodes++;
            active_=false; blocked_=false;
            return std::nullopt;
        }
        if(!episode) return std::nullopt; // Wait for a detected reference loss.
        if(!active_ || episode!=episode_) {
            active_=true; episode_=episode; rounds_=0; packets_=0;
            blockedRecorded_=false; exhaustedRecorded_=false;
            nextPacket_=now+InitialDelayUs; roundStarted_=0;
            counters.episodes++;
        }
        blocked_=blocked;
        if(blocked) {
            if(!blockedRecorded_) { blockedRecorded_=true; counters.budgetBlockedEpisodes++; }
            return std::nullopt; // Require encoder/budget change, not a larger burst of identical IDRs.
        }
        if(rounds_==MaxRounds && packets_==PacketsPerRound) {
            if(!exhaustedRecorded_) { exhaustedRecorded_=true; counters.exhaustedEpisodes++; }
            return std::nullopt;
        }
        if(now<nextPacket_) return std::nullopt;
        if(!rounds_ || packets_==PacketsPerRound) {
            rounds_++; packets_=0; roundStarted_=now;
            sequence_++; counters.rounds++;
        }
        packets_++; counters.packets++;
        if(packets_>1) counters.duplicatePackets++;
        nextPacket_=packets_<PacketsPerRound ? now+PacketRetryUs:
                    roundStarted_+(rounds_==1 ? 250000:500000);
        return sequence_;
    }

    bool active() const { return active_; }
    bool requiresEncoderChange() const { return active_ && blocked_; }
    unsigned roundsInEpisode() const { return rounds_; }
    const char* state() const {
        if(!active_) return "no_unresolved_reference_loss";
        if(blocked_) return "requires_encoder_or_wire_budget_change";
        if(rounds_==MaxRounds && packets_==PacketsPerRound) return "bounded_requests_exhausted";
        return "waiting_for_decodable_idr";
    }
private:
    uint64_t episode_=0, sequence_=0, nextPacket_=0, roundStarted_=0, previousTick_=0;
    unsigned rounds_=0, packets_=0;
    bool active_=false, blocked_=false, blockedRecorded_=false, exhaustedRecorded_=false;
};

} // namespace huoguo::udp
