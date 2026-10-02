#include "recovery_feedback.hpp"
#include <array>
#include <iostream>
#include <stdexcept>
#include <vector>

using huoguo::udp::RecoveryFeedback;
static void require(bool condition, const char* label) {
    if(!condition) throw std::runtime_error(label);
}
int main() {
    try {
        RecoveryFeedback policy;
        std::vector<uint64_t> times, sequences;
        for(uint64_t now=0;now<=2000000;now+=1000) {
            if(auto sequence=policy.tick(now,1,true,false)) { times.push_back(now); sequences.push_back(*sequence); }
        }
        require(times==std::vector<uint64_t>({5000,25000,45000,255000,275000,295000,755000,775000,795000}),
                "finite 5 ms initial + 250/500 ms round backoff");
        require(sequences==std::vector<uint64_t>({1,1,1,2,2,2,3,3,3}),"duplicate packet vs unique upstream IDR request");
        require(policy.counters.rounds==3 && policy.counters.packets==9 && policy.counters.exhaustedEpisodes==1,
                "all feedback lost: exactly three finite request rounds");
        require(policy.active() && !policy.requiresEncoderChange(),"request exhaustion is not recovery");
        policy.tick(2100000,1,false,false);
        require(policy.counters.recoveredEpisodes==1,"only a delivered genuine IDR completes recovery");
        require(!policy.active(),"recovered episode closes");
        require(!policy.tick(2200000,2,true,false),"new reference loss starts independent bounded episode");
        require(policy.tick(2205000,2,true,false)==4,"new episode retains monotonically increasing feedback sequence");

        RecoveryFeedback oversized;
        oversized.tick(0,1,true,false);
        require(oversized.tick(5000,1,true,false)==1,"initial ordinary loss request");
        for(uint64_t now=6000;now<=2000000;now+=1000) require(!oversized.tick(now,1,true,true),"oversized IDR suppresses repeats");
        require(oversized.counters.rounds==1 && oversized.counters.packets==1 && oversized.requiresEncoderChange(),
                "proven insufficient IDR budget requests adaptation rather than repeated identical IDRs");
        require(oversized.counters.budgetBlockedEpisodes==1 && oversized.counters.budgetBlockedTimeUs>=1990000,
                "blocked interval is explicit metadata, counted once per outage");
        // A newly generated IDR that actually fits removes the sender's block.
        // Lost reception can resume requests, but never resets the episode cap.
        for(uint64_t now=2001000;now<=4000000;now+=1000) oversized.tick(now,1,true,false);
        require(oversized.counters.rounds==3 && oversized.counters.packets==9 && oversized.counters.exhaustedEpisodes==1,
                "unblocking does not bypass finite per-episode cap");
        oversized.tick(4001000,1,false,false);
        require(!oversized.requiresEncoderChange() && oversized.counters.recoveredEpisodes==1,"small received IDR recovers");

        RecoveryFeedback initiallyOversized;
        for(uint64_t now=0;now<=1000000;now+=1000) require(!initiallyOversized.tick(now,1,true,true),"blocked initial IDR never asks for same size");
        require(initiallyOversized.counters.rounds==0 && initiallyOversized.requiresEncoderChange(),"initial budget failure remains visible");
        RecoveryFeedback initialUndetected;
        require(!initialUndetected.tick(1000000,0,true,false) && initialUndetected.counters.episodes==0,"no request before reference loss detected");
        std::cout<<"{\"validation\":\"deterministic_control_feedback_policy\",\"passed\":true,\"ordinary_loss_max_unique_requests_per_outage\":3,"
                    "\"max_feedback_packets_per_outage\":9,\"oversized_idr_retries_suppressed\":true,\"no_media_retransmissions\":true}\n";
        return 0;
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
}
