#include "media_datagram.hpp"
#include <arpa/inet.h>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <poll.h>
#include <random>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

using namespace huoguo::udp;
using Clock=std::chrono::steady_clock;
static uint64_t nowUs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(Clock::now().time_since_epoch()).count());
}
static void require(bool ok,const char* text) { if(!ok) throw std::runtime_error(text); }
static Bytes payload(size_t bytes,uint64_t id) {
    Bytes output(bytes);
    for(size_t i=0; i<bytes; i++) output[i]=uint8_t((i*31+i/257+id*17)&255);
    return output;
}
static void selfTest() {
    constexpr uint64_t time=1000000;
    auto data=payload(10240,1);
    int cases=0;
    for(int a=0; a<12; a++) for(int b=a+1; b<12; b++) {
        auto packets=packetize(data,1,time,Keyframe|Reference,0);
        size_t delivered=0;
        Receiver receiver([&](auto id,auto pts,const Bytes& bytes) {
            require(id==1 && pts==time && bytes==data,"two erasure reconstruction corruption"); delivered++;
        });
        for(int shard=11; shard>=0; shard--) if(shard!=a && shard!=b)
            receiver.receive(packets[shard],time+1000+size_t(11-shard)*100);
        require(delivered==1 && receiver.pendingFrames()==0,"two erasure reorder delivery"); cases++;
    }
    // Across-frame reordering cannot hand a dependent frame to a decoder before
    // its reference, even when both payloads are already reconstructed.
    std::vector<uint64_t> ids;
    Receiver reordered([&](auto id,auto,const Bytes& bytes) {
        require(bytes==payload(23100,id),"multiblock reorder payload"); ids.push_back(id);
    });
    auto first=packetize(payload(23100,1),1,time,Keyframe|Reference,0);
    auto second=packetize(payload(23100,2),2,time+16000,Reference,1);
    reordered.receive(first[0],time+17000);
    reordered.receive(first[0],time+17001);
    for(auto it=second.rbegin(); it!=second.rend(); ++it) reordered.receive(*it,time+20000);
    require(ids.empty(),"dependent frame bypassed unavailable reference");
    for(auto it=first.rbegin(); it!=first.rend(); ++it) reordered.receive(*it,time+22000);
    require(ids==std::vector<uint64_t>({1,2}) && reordered.stats.duplicate>=1,"across-frame reorder/duplicate");

    // Three lost data shards exceed a block's FEC budget. At deadline the
    // reference is abandoned; no late retransmit revives it. A future IDR heals.
    ids.clear();
    Receiver deadline([&](auto id,auto,const Bytes&) { ids.push_back(id); });
    for(auto& packet:packetize(data,1,time,Keyframe|Reference,0)) deadline.receive(packet,time+1000);
    auto lost=packetize(data,2,time+16000,Reference,1);
    for(size_t i=3; i<lost.size(); i++) deadline.receive(lost[i],time+20000);
    for(auto& packet:packetize(data,3,time+32000,Reference,2)) deadline.receive(packet,time+40000);
    require(ids==std::vector<uint64_t>({1}),"decoded missing reference");
    deadline.expire(time+96000);
    require(deadline.stats.framesExpired==1 && deadline.stats.referenceLost==1 &&
            deadline.stats.keyframeRequests==1 && deadline.stats.dependencyDropped==1,"deadline recovery state");
    for(auto& packet:lost) deadline.receive(packet,time+100000);
    for(auto& packet:packetize(data,4,time+100000,Keyframe|Reference,0)) deadline.receive(packet,time+101000);
    require(ids==std::vector<uint64_t>({1,4}) && deadline.stats.expiredPackets==12,"late replay or IDR recovery");

    // Whole-frame loss, which FEC cannot observe/recover, eventually requests an
    // IDR instead of leaving complete P frames resident indefinitely.
    ids.clear();
    Receiver missing([&](auto id,auto,const Bytes&) { ids.push_back(id); });
    for(auto& packet:packetize(data,1,time,Keyframe|Reference,0)) missing.receive(packet,time+1000);
    for(auto& packet:packetize(data,3,time+32000,Reference,2)) missing.receive(packet,time+40000);
    missing.expire(time+112000);
    require(ids.size()==1 && missing.pendingFrames()==0 && missing.stats.keyframeRequests==1,"whole reference loss");
    Receiver startup([](auto,auto,const Bytes&) {});
    for(auto& packet:packetize(data,2,time+16000,Reference,1)) startup.receive(packet,time+20000);
    require(startup.stats.keyframeRequests==1 && startup.stats.dependencyDropped==1,
            "lost startup IDR must request recovery, not silently wait for periodic keyframe");

    // A non-reference frame lost after an IDR must not invalidate the reference.
    ids.clear();
    Receiver nonref([&](auto id,auto,const Bytes&) { ids.push_back(id); });
    for(auto& packet:packetize(data,1,time,Keyframe|Reference,0)) nonref.receive(packet,time+1000);
    auto nr=packetize(data,2,time+16000,0,1);
    nonref.receive(nr[0],time+20000); nonref.expire(time+96000);
    for(auto& packet:packetize(data,3,time+100000,Reference,1)) nonref.receive(packet,time+101000);
    require(ids==std::vector<uint64_t>({1,3}) && nonref.stats.keyframeRequests==0,"nonreference loss invalidates reference");

    Receiver bounds([](auto,auto,const Bytes&) {});
    auto invalid=first[0]; put(invalid,32,MaxFrameBytes+1,4); bounds.receive(invalid,time+1000);
    invalid=first[0]; invalid[6]=100; bounds.receive(invalid,time+1000);
    invalid=first[0]; put(invalid,44,123,4); bounds.receive(invalid,time+1000);
    invalid=first[0]; invalid.pop_back(); bounds.receive(invalid,time+1000);
    invalid=first[0]; put(invalid,40,120000,4); bounds.receive(invalid,time+1000);
    require(bounds.stats.invalid==5 && bounds.pendingFrames()==0,"untrusted length/session/deadline bounds");
    for(uint64_t id=1; id<=20; id++) {
        auto pending=packetize(data,id,time,Reference|Keyframe,0);
        bounds.receive(pending[0],time+1000);
    }
    require(bounds.pendingFrames()==8 && bounds.stats.memoryRejected==12,"bounded incomplete-frame memory");
    Pacer pacer(4000000);
    require(pacer.slot(time,1000)==time,"first pace slot");
    require(pacer.slot(time,1000)==time+2000,"wire byte pacing");
    require(pacer.slot(time+50000,1000)==time+50000,"pacer catchup burst");
    require(pacer.slot(time+50000,1000)==time+52000,"pacer stall reanchor");
    Pacer canceled(4000000);
    require(canceled.reserveBefore(time,1000,time+2000)==std::optional<uint64_t>(time),"deadline packet reservation");
    for(int i=0;i<1000;i++) require(!canceled.reserveBefore(time,1000,time+2000),"unsendable packet accepted");
    require(canceled.readyUs(time)==time+2000,"discarded packets accumulated virtual pacing debt");
    require(canceled.reserveBefore(time+2000,1000,time+4000)==std::optional<uint64_t>(time+2000),
            "discarded-frame pacing state poisoned the next frame");
    std::cout<<"{\"validation\":\"synthetic_component\",\"two_erasure_cases\":"<<cases
             <<",\"across_frame_reorder\":true,\"duplicate\":true,\"three_loss_deadline_idr\":true"
             <<",\"whole_frame_loss\":true,\"nonreference_loss\":true,\"bounds\":true,\"byte_pacer\":true}\n";
}

struct Socket {
    int fd=-1;
    Socket() { fd=::socket(AF_INET,SOCK_DGRAM,0); if(fd<0) throw std::runtime_error("UDP socket"); }
    ~Socket() { if(fd>=0) close(fd); }
    Socket(const Socket&)=delete;
    Socket& operator=(const Socket&)=delete;
};
static double percentile(std::vector<double> values,double fraction) {
    if(values.empty()) return 0;
    std::sort(values.begin(),values.end());
    return values[size_t(std::ceil((values.size()-1)*fraction))];
}

// The independent UDP receiver is an actual kernel socket. The seeded link
// impairment happens before sendto, which is NOT a real WAN loss measurement.
static void loopback(double duration,uint64_t bitrate,int fps,double lossPercent,int reorderEvery,
                     bool feedbackEnabled=false,double feedbackLoss=0,uint64_t seed=20261001) {
    require(duration>=1 && duration<=30,"duration must be 1-30 seconds");
    require(fps==30 || fps==60 || fps==120,"fps must be 30/60/120");
    require(lossPercent>=0 && lossPercent<=20,"synthetic loss must be 0-20 percent");
    require(reorderEvery>=0,"reorder interval");
    require(feedbackLoss>=0 && feedbackLoss<=100,"feedback loss percent");
    Socket receiverSocket,senderSocket;
    sockaddr_in address{}; address.sin_family=AF_INET; address.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    address.sin_port=0; // Only loopback, no interface or remote endpoint option.
    require(bind(receiverSocket.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"loopback bind");
    socklen_t addressBytes=sizeof(address);
    require(getsockname(receiverSocket.fd,reinterpret_cast<sockaddr*>(&address),&addressBytes)==0,"bound address");
    require(connect(senderSocket.fd,reinterpret_cast<sockaddr*>(&address),sizeof(address))==0,"pin loopback feedback peer");
    std::atomic<bool> done=false;
    std::vector<double> frameDelay,frameGaps,recoveryDelay;
    uint64_t lastArrival=0,lastDelivered=0,maxUnavailable=0;
    Receiver* receiverPointer=nullptr;
    Receiver receiver([&](auto id,auto captured,const Bytes& frame) {
        require(frame==payload(frame.size(),id),"loopback payload corruption");
        const auto at=nowUs(); frameDelay.push_back(double(at-captured)/1000);
        if(lastArrival) frameGaps.push_back(double(at-lastArrival)/1000);
        lastArrival=at;
        maxUnavailable=std::max(maxUnavailable,id-lastDelivered-1);
        lastDelivered=id;
        if(receiverPointer->recoveryCaptureUs)
            recoveryDelay.push_back(double(at-receiverPointer->recoveryCaptureUs)/1000);
    });
    receiverPointer=&receiver;
    std::exception_ptr receiveError;
    uint64_t feedbackSent=0,feedbackLost=0;
    std::thread reader([&]() {
        try {
            std::array<uint8_t,2048> packet{};
            sockaddr_in peer{}; socklen_t peerBytes=sizeof(peer);
            bool peerKnown=false;
            uint64_t feedbackSequence=0,nextFeedback=0;
            int feedbackAttempts=0;
            std::mt19937 feedbackGenerator(uint32_t(seed+2));
            std::uniform_real_distribution<double> feedbackSample(0,100);
            while(!done) {
                pollfd polling{receiverSocket.fd,POLLIN,0};
                if(poll(&polling,1,2)>0) {
                    const auto count=recvfrom(receiverSocket.fd,packet.data(),packet.size(),0,
                                              reinterpret_cast<sockaddr*>(&peer),&peerBytes);
                    peerKnown=count>0 && peer.sin_addr.s_addr==htonl(INADDR_LOOPBACK);
                    if(peerKnown) receiver.receive(std::span(packet.data(),size_t(count)),nowUs());
                }
                receiver.expire(nowUs());
                if(feedbackEnabled && peerKnown && receiver.stats.keyframeRequests && receiver.needsKeyframe()) {
                    if(feedbackSequence!=receiver.stats.keyframeRequests) {
                        feedbackSequence=receiver.stats.keyframeRequests;
                        feedbackAttempts=0; nextFeedback=nowUs()+5000; // Synthetic 5 ms control latency.
                    }
                    if(feedbackAttempts<3 && nowUs()>=nextFeedback) {
                        Bytes message(12,0); message[0]='H'; message[1]='G'; message[2]='I'; message[3]=1;
                        put(message,4,feedbackSequence,8); feedbackAttempts++; nextFeedback=nowUs()+20000;
                        if(feedbackSample(feedbackGenerator)<feedbackLoss) { feedbackLost++; }
                        else {
                            require(sendto(receiverSocket.fd,message.data(),message.size(),0,
                                           reinterpret_cast<sockaddr*>(&peer),peerBytes)==ssize_t(message.size()),"IDR feedback send");
                            feedbackSent++;
                        }
                    }
                }
            }
        } catch(...) { receiveError=std::current_exception(); done=true; }
    });
    std::mt19937 generator{uint32_t(seed)};
    std::uniform_real_distribution<double> sample(0,100);
    Pacer pacer(bitrate);
    uint64_t generated=0,sentPackets=0,sentBytes=0,offeredBytes=0,simulatedLoss=0,sourceExpired=0;
    uint64_t feedbackReceived=0,lastFeedbackSequence=0,forcedIdrs=0;
    const uint64_t start=nowUs(),end=start+uint64_t(duration*1000000);
    // FEC is 20% only for full ten-data-shard blocks. Small frames/tail blocks
    // can cost much more, so reserve by actual packetized bytes, not a 1.2 guess.
    const size_t frameWireBudget=size_t(bitrate*.85/(8*fps));
    size_t frameBytes=std::min(MaxFrameBytes,frameWireBudget);
    while(frameBytes && packetizedWireBytes(frameBytes)>frameWireBudget) frameBytes--;
    require(frameBytes>0,"wire budget cannot carry even a tiny FEC frame");
    try {
        for(uint64_t id=1; nowUs()<end && !done; id++) {
            const uint64_t scheduled=start+(id-1)*1000000/fps;
            if(scheduled>=end) break;
            std::this_thread::sleep_until(Clock::time_point(std::chrono::microseconds(scheduled)));
            const uint64_t capture=nowUs();
            bool requestedIdr=false;
            std::array<uint8_t,32> message{};
            for(;;) {
                const auto bytes=recv(senderSocket.fd,message.data(),message.size(),MSG_DONTWAIT);
                if(bytes<=0) break;
                if(bytes==12 && message[0]=='H' && message[1]=='G' && message[2]=='I' && message[3]==1) {
                    const auto sequence=get(std::span(message.data(),12),4,8);
                    if(sequence>lastFeedbackSequence) { requestedIdr=true; feedbackReceived++; lastFeedbackSequence=sequence; }
                }
            }
            const bool key=requestedIdr || ((id-1)%uint64_t(fps/2))==0;
            if(requestedIdr) forcedIdrs++;
            auto packets=packetize(payload(frameBytes,id),id,capture,key ? Keyframe|Reference : Reference,
                                   key ? 0 : id-1);
            generated++;
            // Swap neighbours, preserving emission pacing; no fake delay metric.
            if(reorderEvery && id%uint64_t(reorderEvery)==0)
                for(size_t i=0; i+1<packets.size(); i+=2) std::swap(packets[i],packets[i+1]);
            for(const auto& packet:packets) {
                const auto slot=pacer.reserveBefore(nowUs(),packet.size()+28,capture+80000);
                if(!slot) { sourceExpired++; continue; }
                std::this_thread::sleep_until(Clock::time_point(std::chrono::microseconds(*slot)));
                if(nowUs()>=capture+80000) { sourceExpired++; continue; }
                offeredBytes+=packet.size()+28;
                if(sample(generator)<lossPercent) { simulatedLoss++; continue; }
                const auto result=send(senderSocket.fd,packet.data(),packet.size(),0);
                if(result!=ssize_t(packet.size())) throw std::runtime_error(std::string("UDP send: ")+std::strerror(errno));
                sentPackets++; sentBytes+=packet.size()+28;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(90));
    } catch(...) { done=true; reader.join(); throw; }
    done=true; reader.join();
    if(receiveError) std::rethrow_exception(receiveError);
    maxUnavailable=std::max(maxUnavailable,generated-lastDelivered);
    const double elapsed=double(nowUs()-start)/1000000;
    const auto& s=receiver.stats;
    std::cout<<"{\"validation\":\"actual_localhost_udp_with_synthetic_payload_and_impairment\""
             <<",\"target_fps\":"<<fps<<",\"wire_budget_mbps\":"<<double(bitrate)/1000000
             <<",\"seed\":"<<seed<<",\"loss_percent\":"<<lossPercent<<",\"reorder_every_frames\":"<<reorderEvery
             <<",\"feedback_enabled\":"<<(feedbackEnabled ? "true":"false")<<",\"feedback_loss_percent\":"<<feedbackLoss
             <<",\"duration_seconds\":"<<elapsed<<",\"payload_bytes_per_frame\":"<<frameBytes
             <<",\"synthetic_payload_mbps\":"<<frameBytes*8.0*fps/1000000
             <<",\"packetized_overhead_percent\":"<<(double(packetizedWireBytes(frameBytes))/frameBytes-1)*100
             <<",\"generated_frames\":"<<generated<<",\"delivered_frames\":"<<s.framesDelivered
             <<",\"delivered_fps\":"<<s.framesDelivered/duration<<",\"sent_packets\":"<<sentPackets
             <<",\"simulated_dropped_packets\":"<<simulatedLoss<<",\"source_expired_packets\":"<<sourceExpired
             <<",\"wire_mbps\":"<<sentBytes*8/elapsed/1000000<<",\"offered_wire_mbps\":"<<offeredBytes*8/elapsed/1000000
             <<",\"received_packets\":"<<s.packets
             <<",\"recovered_data_shards\":"<<s.recoveredShards<<",\"expired_frames\":"<<s.framesExpired
             <<",\"dependency_dropped_frames\":"<<s.dependencyDropped<<",\"idr_requests\":"<<s.keyframeRequests
             <<",\"feedback_packets_sent\":"<<feedbackSent<<",\"feedback_packets_lost\":"<<feedbackLost
             <<",\"feedback_requests_received\":"<<feedbackReceived<<",\"forced_idrs\":"<<forcedIdrs
             <<",\"max_consecutive_unavailable_frames\":"<<maxUnavailable
             <<",\"recovery_p95_ms\":"<<percentile(recoveryDelay,.95)<<",\"recovery_max_ms\":"<<percentile(recoveryDelay,1)
             <<",\"recovery_unresolved_at_end\":"<<(receiver.recoveryCaptureUs ? "true":"false")
             <<",\"unresolved_recovery_elapsed_ms\":"<<(receiver.recoveryCaptureUs ? double(nowUs()-receiver.recoveryCaptureUs)/1000:0)
             <<",\"delay_p50_ms\":"<<percentile(frameDelay,.50)<<",\"delay_p95_ms\":"<<percentile(frameDelay,.95)
             <<",\"delay_p99_ms\":"<<percentile(frameDelay,.99)<<",\"gap_p95_ms\":"<<percentile(frameGaps,.95)
             <<",\"gap_p99_ms\":"<<percentile(frameGaps,.99)<<",\"pending_frames_end\":"<<receiver.pendingFrames()<<"}\n";
}

int main(int argc,char** argv) {
    try {
        reed_solomon_init();
        if(argc==2 && std::string(argv[1])=="--self-test") { selfTest(); return 0; }
        if((argc==7 || argc==10) && std::string(argv[1])=="--loopback") {
            loopback(std::stod(argv[2]),std::stoull(argv[3]),std::stoi(argv[4]),
                     std::stod(argv[5]),std::stoi(argv[6]),argc==10 && std::stoi(argv[7])!=0,
                     argc==10 ? std::stod(argv[8]):0,argc==10 ? std::stoull(argv[9]):20261001); return 0;
        }
        std::cerr<<"Usage: udp_media_probe --self-test\n"
                    "       udp_media_probe --loopback SECONDS WIRE_BITS_PER_SECOND FPS LOSS_PERCENT REORDER_EVERY_N_FRAMES\n"
                    "                       [FEEDBACK_0_OR_1 FEEDBACK_LOSS_PERCENT SEED]\n"
                    "All sockets bind 127.0.0.1; no production/public mode exists.\n";
        return 2;
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
}
