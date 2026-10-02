#include "h264_access_unit.hpp"
#include "recovery_feedback.hpp"
#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <cstring>
#include <iostream>
#include <mutex>
#include <poll.h>
#include <random>
#include <sstream>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace huoguo::udp;
using Clock=std::chrono::steady_clock;
static uint64_t nowUs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now().time_since_epoch()).count());
}
static void require(bool ok,const char* text) { if(!ok) throw std::runtime_error(text); }
static std::string distribution(std::vector<uint64_t> values) {
    if(values.empty()) return "{\"count\":0}";
    std::sort(values.begin(),values.end());
    auto at=[&](size_t numerator) { return values[std::min(values.size()-1,(values.size()*numerator+99)/100-1)]; };
    std::ostringstream out;
    out<<"{\"count\":"<<values.size()<<",\"min\":"<<values.front()<<",\"p50\":"<<at(50)
       <<",\"p95\":"<<at(95)<<",\"p99\":"<<at(99)<<",\"max\":"<<values.back()<<"}";
    return out.str();
}
struct Socket {
    int fd=-1;
    Socket():fd(::socket(AF_INET,SOCK_DGRAM,0)) { require(fd>=0,"UDP socket"); }
    ~Socket() { if(fd>=0) close(fd); }
};

int main(int argc,char** argv) {
    try {
        if(argc!=5) {
            std::cerr<<"Usage: h264_udp_bridge WIRE_BITS_PER_SECOND LOSS_PERCENT REORDER_EVERY_N_FRAMES FEEDBACK_0_OR_1\n"
                        "stdin: existing framed h264 host channel; stdout: reconstructed Annex B; stderr: JSON events.\n"
                        "Only localhost UDP. Transport timing starts at bridge receipt, excluding source capture/encode.\n";
            return 2;
        }
        const uint64_t bitrate=std::stoull(argv[1]); const double loss=std::stod(argv[2]);
        const int reorder=std::stoi(argv[3]); const bool feedback=std::stoi(argv[4])!=0;
        require(loss>=0 && loss<=20 && reorder>=0,"impairment bounds");
        reed_solomon_init(); Pacer pacer(bitrate);
        std::array<uint8_t,4> codec{}; readBytes(std::cin,codec);
        require(std::string(codec.begin(),codec.end())=="h264","host codec marker");
        Socket incoming,outgoing;
        sockaddr_in endpoint{}; endpoint.sin_family=AF_INET; endpoint.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
        require(bind(incoming.fd,reinterpret_cast<sockaddr*>(&endpoint),sizeof(endpoint))==0,"loopback bind");
        socklen_t endpointBytes=sizeof(endpoint);
        require(getsockname(incoming.fd,reinterpret_cast<sockaddr*>(&endpoint),&endpointBytes)==0,"loopback address");
        require(connect(outgoing.fd,reinterpret_cast<sockaddr*>(&endpoint),sizeof(endpoint))==0,"fixed loopback peer");
        std::atomic<bool> done=false;
        std::atomic<bool> oversizedIdrBlocked=false;
        std::mutex budgetGateMutex;
        uint64_t oversizedIdrFrame=0,fittingIdrDeadline=0;
        RecoveryFeedback recoveryFeedback;
        uint64_t emittedBytes=0,emittedFrames=0,maxDelay=0;
        Receiver receiver([&](auto frameId,auto captured,const Bytes& bytes) {
            if(oversizedIdrBlocked && inspectAnnexB(bytes).idr) {
                std::lock_guard lock(budgetGateMutex);
                // A late older IDR cannot repair the rejected newer reference.
                if(frameId>oversizedIdrFrame) { oversizedIdrBlocked=false; fittingIdrDeadline=0; }
            }
            std::cout.write(reinterpret_cast<const char*>(bytes.data()),std::streamsize(bytes.size()));
            std::cout.flush(); require(bool(std::cout),"Annex B output closed");
            emittedBytes+=bytes.size(); emittedFrames++;
            maxDelay=std::max(maxDelay,nowUs()-captured);
        });
        std::exception_ptr receiveError;
        std::thread reader([&]() {
            try {
                std::array<uint8_t,2048> data{};
                sockaddr_in peer{}; socklen_t peerBytes=sizeof(peer); bool known=false;
                while(!done) {
                    pollfd pending{incoming.fd,POLLIN,0};
                    if(poll(&pending,1,2)>0) {
                        const auto size=recvfrom(incoming.fd,data.data(),data.size(),0,
                                                 reinterpret_cast<sockaddr*>(&peer),&peerBytes);
                        known=size>0 && peer.sin_addr.s_addr==htonl(INADDR_LOOPBACK);
                        if(known) receiver.receive(std::span(data.data(),size_t(size)),nowUs());
                    }
                    receiver.expire(nowUs());
                    if(oversizedIdrBlocked) {
                        std::lock_guard lock(budgetGateMutex);
                        // A smaller candidate IDR was admitted but did not
                        // recover the reference chain by its deadline. It may
                        // have suffered loss; resume the same finite request
                        // episode without resetting its three-round cap.
                        if(fittingIdrDeadline && nowUs()>=fittingIdrDeadline) {
                            oversizedIdrBlocked=false; fittingIdrDeadline=0;
                        }
                    }
                    if(feedback && known) {
                        if(const auto sequence=recoveryFeedback.tick(nowUs(),receiver.stats.keyframeRequests,
                                                                     receiver.needsKeyframe(),oversizedIdrBlocked)) {
                            Bytes message(12,0); message[0]='H'; message[1]='G'; message[2]='I'; message[3]=1;
                            put(message,4,*sequence,8);
                            require(sendto(incoming.fd,message.data(),message.size(),0,
                                           reinterpret_cast<sockaddr*>(&peer),peerBytes)==ssize_t(message.size()),"IDR feedback");
                        }
                    }
                }
            } catch(...) { receiveError=std::current_exception(); done=true; }
        });
        uint64_t frames=0,lastReference=0,configPackets=0,sent=0,dropped=0,expired=0,wireBytes=0;
        uint64_t lastRequest=0,idrFrames=0,insufficientBudgetFrames=0,insufficientIdrFrames=0;
        uint64_t schedulerExpiredFrames=0,plannedWireBytes=0,queueOnlyBudgetFrames=0;
        uint64_t fullFecRejectedButDataOnlyFits=0;
        uint64_t upstreamRequests=0,adaptationNotifications=0,adaptationSuppressed=0,lastAdaptationAt=0;
        std::string lastBudgetFeedback="null";
        std::vector<uint64_t> frameSizes,idrSizes,frameWireBytes,idrWireBytes,frameSerializationUs;
        std::vector<uint64_t> frameQueueUs,packetizationUs,schedulerOvershootUs,framePacketCounts;
        std::vector<uint64_t> dataOnlySerializationUs,parityWireBytes;
        std::vector<std::string> deadlineExamples;
        Bytes config;
        std::mt19937 random(20261001); std::uniform_real_distribution<double> sample(0,100);
        const auto start=nowUs();
        try {
            HostAccessUnit unit;
            while(!done && readHostAccessUnit(std::cin,unit)) {
                const auto info=inspectAnnexB(unit.payload);
                if(unit.flaggedPts&(1ULL<<62)) {
                    require(unit.payload.size()<=65536,"H264 parameter configuration bounds");
                    config=unit.payload; configPackets++; continue;
                }
                if(!info.hasSlice) continue;
                std::array<uint8_t,32> message{};
                for(;;) {
                    const auto count=recv(outgoing.fd,message.data(),message.size(),MSG_DONTWAIT);
                    if(count<=0) break;
                    if(count==12 && message[0]=='H' && message[1]=='G' && message[2]=='I' && message[3]==1) {
                        const auto sequence=get(std::span(message.data(),12),4,8);
                        if(sequence>lastRequest) {
                            lastRequest=sequence;
                            if(oversizedIdrBlocked) continue; // An already queued request cannot repair this known budget failure.
                            upstreamRequests++;
                            // Parent must translate this authenticated local pipe
                            // request to HostSession.control.sendall(b'\x11').
                            std::cerr<<"{\"event\":\"request_idr\",\"sequence\":"<<sequence<<"}\n"<<std::flush;
                        }
                    }
                }
                const auto captured=nowUs(); frames++;
                Bytes frame;
                if(info.idr && !config.empty()) frame=config;
                frame.insert(frame.end(),unit.payload.begin(),unit.payload.end());
                const uint8_t flags=uint8_t((info.idr ? Keyframe:0)|(info.reference ? Reference:0));
                if(!info.idr && !lastReference) continue; // Await genuine IDR; never relabel a P frame.
                frameSizes.push_back(frame.size());
                if(info.idr) idrSizes.push_back(frame.size());
                auto packets=packetize(frame,frames,captured,flags,info.idr ? 0:lastReference);
                if(info.reference) lastReference=frames;
                if(info.idr) idrFrames++;
                const uint64_t packedAt=nowUs(),wire=packetizedWireBytes(frame.size());
                const uint64_t lowerBound=pacer.serializationUs(wire);
                uint64_t dataWire=0;
                for(const auto& packet:packets) if(packet[6]<packet[7]) dataWire+=packet.size()+28;
                const uint64_t dataOnlyBound=pacer.serializationUs(dataWire);
                const uint64_t ready=pacer.readyUs(packedAt),queued=ready-packedAt;
                const uint64_t remaining=packedAt<captured+80000 ? captured+80000-packedAt:0;
                plannedWireBytes+=wire;
                frameWireBytes.push_back(wire); frameSerializationUs.push_back(lowerBound);
                frameQueueUs.push_back(queued); packetizationUs.push_back(packedAt-captured);
                framePacketCounts.push_back(packets.size());
                dataOnlySerializationUs.push_back(dataOnlyBound); parityWireBytes.push_back(wire-dataWire);
                if(info.idr) idrWireBytes.push_back(wire);
                if(lowerBound+queued>remaining) {
                    // A known impossible frame is rejected as a whole. Sending a
                    // prefix and reserving unsent tails used to create an ever-
                    // growing synthetic pacing queue and repeated IDR failures.
                    insufficientBudgetFrames++;
                    if(info.idr) insufficientIdrFrames++;
                    if(lowerBound<=remaining) queueOnlyBudgetFrames++;
                    if(dataOnlyBound+queued<=remaining) fullFecRejectedButDataOnlyFits++;
                    expired+=packets.size();
                    if(info.idr && lowerBound>80000) {
                        // This cannot be repaired by asking for another equally
                        // large IDR. Reserve a bounded encoder adaptation signal;
                        // this diagnostic bridge never changes the live encoder.
                        {
                            std::lock_guard lock(budgetGateMutex);
                            oversizedIdrFrame=frames; fittingIdrDeadline=0; oversizedIdrBlocked=true;
                        }
                        const auto safeRemaining=remaining>5000 ? remaining-5000:std::max(uint64_t(1),remaining);
                        const uint64_t minimumWireBps=(wire*8000000+safeRemaining-1)/safeRemaining;
                        const uint64_t estimatedPayloadBudget=frame.size()*bitrate*safeRemaining/(wire*8000000);
                        std::ostringstream budget;
                        budget<<"{\"reason\":\"idr_serialization_exceeds_media_lifetime\",\"frame\":"<<frames
                              <<",\"payload_bytes\":"<<frame.size()<<",\"full_wire_bytes\":"<<wire
                              <<",\"data_only_wire_bytes\":"<<dataWire<<",\"wire_budget_bps\":"<<bitrate
                              <<",\"media_lifetime_us\":80000,\"remaining_lifetime_us\":"<<remaining
                              <<",\"serialization_lower_bound_us\":"<<lowerBound
                              <<",\"data_only_serialization_lower_bound_us\":"<<dataOnlyBound
                              <<",\"estimated_minimum_wire_bps_with_5ms_margin\":"<<minimumWireBps
                              <<",\"estimated_next_idr_payload_budget_bytes\":"<<estimatedPayloadBudget
                              <<",\"requires_encoder_or_wire_budget_change\":true,\"auto_apply\":false"
                              <<",\"suggested_action\":\"smaller_verified_idr_or_higher_verified_wire_budget\"}";
                        lastBudgetFeedback=budget.str();
                        // No more than three informational notifications per
                        // run, separated by two seconds. Each rejected frame
                        // remains included in the exact budget-failure count.
                        if(adaptationNotifications<3 && (!adaptationNotifications || packedAt-lastAdaptationAt>=2000000)) {
                            adaptationNotifications++; lastAdaptationAt=packedAt;
                            std::cerr<<"{\"event\":\"encoder_budget_feedback\",\"detail\":"<<lastBudgetFeedback<<"}\n"<<std::flush;
                        } else adaptationSuppressed++;
                    }
                    if(deadlineExamples.size()<8) {
                        std::ostringstream example;
                        example<<"{\"frame\":"<<frames<<",\"idr\":"<<(info.idr ? "true":"false")
                               <<",\"cause\":\"insufficient_complete_frame_deadline_budget\""
                               <<",\"payload_bytes\":"<<frame.size()<<",\"wire_bytes\":"<<wire
                               <<",\"packets\":"<<packets.size()<<",\"serialization_lower_bound_us\":"<<lowerBound
                               <<",\"data_only_serialization_lower_bound_us\":"<<dataOnlyBound
                               <<",\"parity_wire_bytes\":"<<wire-dataWire
                               <<",\"pacer_queue_us\":"<<queued<<",\"remaining_lifetime_us\":"<<remaining<<"}";
                        deadlineExamples.push_back(example.str());
                    }
                    continue;
                }
                if(info.idr && oversizedIdrBlocked) {
                    std::lock_guard lock(budgetGateMutex);
                    fittingIdrDeadline=captured+80000;
                }
                if(reorder && frames%uint64_t(reorder)==0)
                    for(size_t i=0;i+1<packets.size();i+=2) std::swap(packets[i],packets[i+1]);
                bool schedulerExpired=false;
                for(const auto& packet:packets) {
                    const auto at=pacer.reserveBefore(nowUs(),packet.size()+28,captured+80000);
                    if(!at) { expired++; schedulerExpired=true; continue; }
                    std::this_thread::sleep_until(Clock::time_point(std::chrono::microseconds(*at)));
                    const auto awakened=nowUs();
                    schedulerOvershootUs.push_back(awakened>*at ? awakened-*at:0);
                    if(awakened>=captured+80000) { expired++; schedulerExpired=true; continue; }
                    wireBytes+=packet.size()+28;
                    if(sample(random)<loss) { dropped++; continue; }
                    if(send(outgoing.fd,packet.data(),packet.size(),0)!=ssize_t(packet.size()))
                        throw std::runtime_error(std::string("UDP send: ")+std::strerror(errno));
                    sent++;
                }
                if(schedulerExpired) {
                    schedulerExpiredFrames++;
                    if(deadlineExamples.size()<8) {
                        std::ostringstream example;
                        example<<"{\"frame\":"<<frames<<",\"idr\":"<<(info.idr ? "true":"false")
                               <<",\"cause\":\"scheduler_or_actual_queue_deadline\""
                               <<",\"payload_bytes\":"<<frame.size()<<",\"wire_bytes\":"<<wire
                               <<",\"serialization_lower_bound_us\":"<<lowerBound
                               <<",\"initial_pacer_queue_us\":"<<queued
                               <<",\"sender_elapsed_us\":"<<nowUs()-captured<<"}";
                        deadlineExamples.push_back(example.str());
                    }
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(90));
        } catch(...) { done=true; reader.join(); throw; }
        done=true; reader.join();
        if(receiveError) std::rethrow_exception(receiveError);
        const auto& stats=receiver.stats;
        std::cerr<<"{\"event\":\"summary\",\"validation\":\"actual_h264_localhost_udp_roundtrip\""
                 <<",\"source_frames\":"<<frames<<",\"source_idr_frames\":"<<idrFrames
                 <<",\"source_config_packets\":"<<configPackets<<",\"delivered_frames\":"<<emittedFrames
                 <<",\"output_bytes\":"<<emittedBytes<<",\"sent_packets\":"<<sent<<",\"injected_drops\":"<<dropped
                 <<",\"source_expired_packets\":"<<expired<<",\"receiver_expired_frames\":"<<stats.framesExpired
                 <<",\"dependency_dropped_frames\":"<<stats.dependencyDropped
                 <<",\"recovered_data_shards\":"<<stats.recoveredShards<<",\"idr_requests\":"<<stats.keyframeRequests
                 <<",\"feedback_request_rounds\":"<<recoveryFeedback.counters.rounds
                 <<",\"feedback_udp_packets_sent\":"<<recoveryFeedback.counters.packets
                 <<",\"forwarded_encoder_idr_requests\":"<<upstreamRequests
                 <<",\"feedback_recovered_episodes\":"<<recoveryFeedback.counters.recoveredEpisodes
                 <<",\"feedback_exhausted_episodes\":"<<recoveryFeedback.counters.exhaustedEpisodes
                 <<",\"feedback_budget_blocked_episodes\":"<<recoveryFeedback.counters.budgetBlockedEpisodes
                 <<",\"feedback_budget_blocked_time_us\":"<<recoveryFeedback.counters.budgetBlockedTimeUs
                 <<",\"recovery_feedback_state\":\""<<(feedback ? recoveryFeedback.state():"feedback_disabled")<<"\""
                 <<",\"requires_encoder_or_wire_budget_change\":"<<(oversizedIdrBlocked ? "true":"false")
                 <<",\"encoder_budget_feedback_notifications\":"<<adaptationNotifications
                 <<",\"encoder_budget_feedback_suppressed\":"<<adaptationSuppressed
                 <<",\"last_encoder_budget_feedback\":"<<lastBudgetFeedback
                 <<",\"max_transport_delay_ms\":"<<double(maxDelay)/1000
                 <<",\"offered_wire_mbps\":"<<wireBytes*8.0/(nowUs()-start)
                 <<",\"source_planned_packetized_mbps\":"<<plannedWireBytes*8.0/(nowUs()-start)
                 <<",\"insufficient_frame_budget_count\":"<<insufficientBudgetFrames
                 <<",\"insufficient_idr_budget_count\":"<<insufficientIdrFrames
                 <<",\"queue_only_insufficient_budget_count\":"<<queueOnlyBudgetFrames
                 <<",\"full_fec_rejected_but_data_only_fits_count\":"<<fullFecRejectedButDataOnlyFits
                 <<",\"scheduler_expired_frame_count\":"<<schedulerExpiredFrames
                 <<",\"frame_payload_bytes_distribution\":"<<distribution(frameSizes)
                 <<",\"idr_payload_bytes_distribution\":"<<distribution(idrSizes)
                 <<",\"frame_packetized_wire_bytes_distribution\":"<<distribution(frameWireBytes)
                 <<",\"idr_packetized_wire_bytes_distribution\":"<<distribution(idrWireBytes)
                 <<",\"frame_packet_count_distribution\":"<<distribution(framePacketCounts)
                 <<",\"frame_serialization_lower_bound_us_distribution\":"<<distribution(frameSerializationUs)
                 <<",\"data_only_serialization_lower_bound_us_distribution\":"<<distribution(dataOnlySerializationUs)
                 <<",\"fec_parity_wire_bytes_distribution\":"<<distribution(parityWireBytes)
                 <<",\"pacer_queue_before_frame_us_distribution\":"<<distribution(frameQueueUs)
                 <<",\"packetization_us_distribution\":"<<distribution(packetizationUs)
                 <<",\"pacer_sleep_overshoot_us_distribution\":"<<distribution(schedulerOvershootUs)
                 <<",\"deadline_drop_examples\":[";
        for(size_t i=0; i<deadlineExamples.size(); i++) { if(i) std::cerr<<','; std::cerr<<deadlineExamples[i]; }
        std::cerr<<']'
                 <<",\"clock_scope\":\"bridge_received_to_roundtrip_output_excludes_capture_and_encode\"}\n";
        return 0;
    } catch(const std::exception& error) { std::cerr<<"H264 UDP bridge: "<<error.what()<<'\n'; return 1; }
}
