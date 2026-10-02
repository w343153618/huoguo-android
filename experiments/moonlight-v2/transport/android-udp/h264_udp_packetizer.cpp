#include "h264_baseline_gate.hpp"
#include "phone_receiver.hpp"
#include <chrono>
#include <iostream>
#include <thread>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

using namespace huoguo::android_udp;
namespace {
uint64_t nowUs() { return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count()); }
constexpr size_t EncryptedIpOverhead=24+16+28;
struct Counters {
    uint64_t source=0,config=0,idr=0,frames=0,packets=0,plainBytes=0,wireBytes=0;
    uint64_t budgetDrops=0,outputDrops=0,unsupported=0,dependencyDrops=0,blockedUs=0,maxBlockedUs=0;
    uint64_t optionalParityFrames=0,optionalParityPackets=0;
    uint64_t recoveryRequests=0,recoveryExhausted=0,recoveryCompletions=0;
    uint64_t maxAuBytes=0,maxWireFrameBytes=0,started=nowUs(),lastReport=started;
    void summary(uint64_t bitrate,bool final=false) {
        const auto at=nowUs();
        if(!final && at-lastReport<1000000) return;
        lastReport=at;
        std::cerr<<"{\"event\":\"summary\",\"scope\":\"packetizer_stdout_not_WAN\",\"elapsed_us\":"<<(at-started)
            <<",\"wire_bitrate\":"<<bitrate<<",\"source_frames\":"<<source<<",\"config_messages\":"<<config
            <<",\"source_idr\":"<<idr<<",\"output_frames\":"<<frames<<",\"output_packets\":"<<packets
            <<",\"plaintext_bytes\":"<<plainBytes<<",\"estimated_ipv4_encrypted_wire_bytes\":"<<wireBytes
            <<",\"frame_budget_drops\":"<<budgetDrops<<",\"output_deadline_drops\":"<<outputDrops
            <<",\"optional_tail_parity_frames\":"<<optionalParityFrames<<",\"optional_tail_parity_packets\":"<<optionalParityPackets
            <<",\"unsupported_frames\":"<<unsupported<<",\"dependent_source_drops\":"<<dependencyDrops
            <<",\"recovery_requests\":"<<recoveryRequests<<",\"recovery_exhausted\":"<<recoveryExhausted
            <<",\"recovery_completions\":"<<recoveryCompletions
            <<",\"stdout_blocked_us\":"<<blockedUs<<",\"stdout_max_blocked_us\":"<<maxBlockedUs
            <<",\"max_au_bytes\":"<<maxAuBytes<<",\"max_wire_frame_bytes\":"<<maxWireFrameBytes
            <<",\"final\":"<<(final?"true":"false")<<"}\n";
    }
};
// Own stdout only. A blocked consumer may not indefinitely hold up the source.
// If a deadline lands inside an IPC record, abort rather than emit a corrupt
// length-framed stream. The receiver eventually expires the incomplete frame.
bool writePacket(const Bytes& packet,uint64_t deadline,Counters& counters) {
    Bytes record(packet.size()+2); put(record,0,packet.size(),2); std::copy(packet.begin(),packet.end(),record.begin()+2);
    size_t done=0; uint64_t blockedStart=0;
    while(done<record.size()) {
        const auto at=nowUs();
        if(at>=deadline) {
            if(blockedStart) { const auto delay=at-blockedStart; counters.blockedUs+=delay; counters.maxBlockedUs=std::max(counters.maxBlockedUs,delay); }
            if(done) throw std::runtime_error("partial IPC record deadline");
            return false;
        }
        const auto count=write(STDOUT_FILENO,record.data()+done,record.size()-done);
        if(count>0) { done+=size_t(count); continue; }
        if(count<0 && errno==EINTR) continue;
        if(count<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) {
            if(!blockedStart) blockedStart=at;
            pollfd descriptor{STDOUT_FILENO,POLLOUT,0};
            const int timeout=int(std::max(uint64_t(1),std::min(uint64_t(5),(deadline-at+999)/1000)));
            const int result=poll(&descriptor,1,timeout);
            if(result<0 && errno!=EINTR) throw std::runtime_error("stdout poll failed");
            if(result>0 && (descriptor.revents&(POLLERR|POLLHUP|POLLNVAL))) throw std::runtime_error("stdout consumer closed");
            continue;
        }
        throw std::runtime_error("stdout write failed");
    }
    if(blockedStart) { const auto delay=nowUs()-blockedStart; counters.blockedUs+=delay; counters.maxBlockedUs=std::max(counters.maxBlockedUs,delay); }
    return true;
}
}
int main(int argc,char** argv) {
    Counters counters; uint64_t bitrate=40000000,recoveryCooldownUs=500000; bool frameEvents=false; size_t burstBytes=0;
    try {
        if(argc>5) throw std::invalid_argument("usage: h264_udp_packetizer [wire_bitrate_bps [recovery_cooldown_us:100000|200000|500000 [frame_events:0|1 [catchup_bytes:0|2048|4096]]]]");
        if(argc>=2) { size_t used=0; bitrate=std::stoull(argv[1],&used); if(used!=std::string(argv[1]).size()) throw std::invalid_argument("wire bitrate syntax"); }
        // An explicit experiment option; default recovery and all frame/epoch
        // bounds remain unchanged. Reject values outside the three A/B cases.
        if(argc>=3) {
            const std::string value(argv[2]);
            if(value!="100000" && value!="200000" && value!="500000") throw std::invalid_argument("recovery cooldown syntax or bound");
            recoveryCooldownUs=std::stoull(value);
        }
        if(argc>=4) {
            const std::string value(argv[3]);
            if(value!="0" && value!="1") throw std::invalid_argument("frame events switch");
            frameEvents=value=="1";
        }
        if(argc>=5) {
            const std::string value(argv[4]);
            if(value!="0" && value!="2048" && value!="4096") throw std::invalid_argument("catch-up byte bound");
            burstBytes=std::stoull(value);
        }
        Pacer pacer(bitrate,burstBytes); initializeFec();
        if(isatty(STDOUT_FILENO)) throw std::invalid_argument("binary stdout must be redirected to the UDP wrapper");
        const auto flags=fcntl(STDOUT_FILENO,F_GETFL); if(flags<0 || fcntl(STDOUT_FILENO,F_SETFL,flags|O_NONBLOCK)<0) throw std::runtime_error("nonblocking stdout unavailable");
        std::signal(SIGPIPE,SIG_IGN); std::ios::sync_with_stdio(false);
        HostReader input(std::cin); HostAccessUnit au; BaselineGate gate; Bytes config;
        uint64_t id=0,lastReference=0,lastRequest=0; bool needsIdr=true,exhaustedReported=false;
        unsigned recoveryAttempts=0;
        // A failed IDR must not reset this epoch: otherwise repeated oversized
        // IDRs could bypass the bound, or one failed request could stall forever.
        // The encoder owner lowers its target using the budget feedback below.
        auto request=[&]{
            const auto at=nowUs();
            if(recoveryAttempts && at-lastRequest<recoveryCooldownUs) return;
            if(recoveryAttempts>=6) {
                if(!exhaustedReported) {
                    exhaustedReported=true; counters.recoveryExhausted++;
                    std::cerr<<"{\"event\":\"recovery_exhausted\",\"reason\":\"bounded_idr_requests\",\"attempts\":6}\n";
                }
                return;
            }
            recoveryAttempts++; lastRequest=at; counters.recoveryRequests++;
            std::cerr<<"{\"event\":\"request_idr\",\"reason\":\"local_dependency_or_admission\",\"attempt\":"<<recoveryAttempts<<",\"cooldown_us\":"<<recoveryCooldownUs<<"}\n";
        };
        while(input.next(au)) {
            const auto capture=nowUs();
            if(au.flaggedPts&ConfigFlag) {
                if(au.payload.size()>MaxConfigBytes) throw std::invalid_argument("configuration bound");
                gate.configuration(au.payload); config=au.payload; counters.config++; continue;
            }
            counters.source++; id++; counters.maxAuBytes=std::max(counters.maxAuBytes,uint64_t(au.payload.size()));
            H264Info info;
            try { info=gate.accessUnit(au.payload,input.width,input.height); }
            catch(const std::invalid_argument&) { counters.unsupported++; needsIdr=true; request(); std::cerr<<"{\"event\":\"frame_rejected\",\"frame\":"<<id<<",\"reason\":\"unsupported_h264_syntax\"}\n"; counters.summary(bitrate); continue; }
            const uint64_t reference=info.idr ? 0:lastReference;
            if(info.reference) lastReference=id; // Retain source chain even if this frame is subsequently rejected.
            if(info.idr) counters.idr++;
            if(frameEvents) {
                std::cerr<<"{\"event\":\"frame_source\",\"frame\":"<<id
                    <<",\"host_capture_us\":"<<capture<<",\"source_pts_us\":"<<(au.flaggedPts&PtsMask)
                    <<",\"au_bytes\":"<<au.payload.size()<<",\"keyframe\":"<<(info.idr?"true":"false")
                    <<",\"reference\":"<<reference<<"}\n";
            }
            if(!info.idr && (needsIdr || !reference)) { counters.dependencyDrops++; request(); counters.summary(bitrate); continue; }
            if(info.idr && config.empty()) { counters.unsupported++; needsIdr=true; request(); counters.summary(bitrate); continue; }
            LogicalFrame logical{input.width,input.height,(au.flaggedPts&PtsMask)|(info.idr?IdrFlag:0),info.idr?config:Bytes{},au.payload};
            const auto body=serializeLogical(logical);
            const auto packets=packetize(body,id,capture,uint8_t((info.idr?Keyframe:0)|(info.reference?Reference:0)),reference);
            const auto packetizationUs=nowUs()-capture;
            size_t remainingData=0;
            for(const auto& p:packets) if(p[6]<p[7]) remainingData++;
            uint64_t fullWire=0; for(const auto& p:packets) fullWire+=p.size()+EncryptedIpOverhead;
            counters.maxWireFrameBytes=std::max(counters.maxWireFrameBytes,fullWire);
            const uint64_t deadline=capture+80000,ready=pacer.readyUs(nowUs()),duration=pacer.serializationUs(fullWire);
            if(ready>=deadline || duration>deadline-ready) {
                counters.budgetDrops++; needsIdr=true;
                std::cerr<<"{\"event\":\"frame_rejected\",\"frame\":"<<id<<",\"host_capture_us\":"<<capture<<",\"keyframe\":"<<(info.idr?"true":"false")<<",\"reason\":\"whole_frame_80ms_budget\",\"au_bytes\":"<<au.payload.size()<<",\"full_wire_bytes\":"<<fullWire<<",\"serialization_us\":"<<duration<<"}\n";
                if(info.idr) {
                    // The owner must adapt the encoder, not enlarge the 80 ms
                    // deadline or raise the wire budget to conceal this rejection.
                    std::cerr<<"{\"event\":\"encoder_budget_feedback\",\"reason\":\"idr_exceeds_assembly_budget\",\"action\":\"smaller_idr_or_lower_encoder_bitrate_required\",\"wire_bitrate\":"<<bitrate<<",\"full_wire_bytes\":"<<fullWire<<",\"budget_us\":80000}\n";
                }
                request();
                counters.summary(bitrate); continue;
            }
            bool complete=true; uint64_t firstWriteUs=0,lastWriteUs=0,plannedSleepUs=0,actualSleepUs=0,maxOvershootUs=0;
            size_t emitted=0; const char* failureStage="none";
            for(const auto& packet:packets) {
                const auto slot=pacer.reserveBefore(nowUs(),packet.size()+EncryptedIpOverhead,deadline);
                if(!slot) { complete=false; failureStage="reserve_deadline"; break; }
                const auto at=nowUs();
                if(*slot>at) {
                    const auto planned=*slot-at; plannedSleepUs+=planned;
                    std::this_thread::sleep_for(std::chrono::microseconds(planned));
                    const auto actual=nowUs()-at; actualSleepUs+=actual;
                    if(actual>planned) maxOvershootUs=std::max(maxOvershootUs,actual-planned);
                }
                if(!writePacket(packet,deadline,counters)) {
                    pacer.cancelReservation(); complete=false; failureStage="write_deadline"; break;
                }
                const auto written=nowUs(); if(!firstWriteUs) firstWriteUs=written; lastWriteUs=written;
                pacer.sentAt(written); emitted++;
                if(packet[6]<packet[7]) remainingData--;
                counters.packets++; counters.plainBytes+=packet.size(); counters.wireBytes+=packet.size()+EncryptedIpOverhead;
            }
            // packetize() orders every block's data before its parity. At an
            // output failure only an intact original-data prefix may preserve
            // the reference; missing data or intermediate blocks still break it.
            const bool mediaDataComplete=remainingData==0;
            if(frameEvents) {
                std::cerr<<"{\"event\":\"frame_output\",\"frame\":"<<id
                    <<",\"host_capture_us\":"<<capture<<",\"source_pts_us\":"<<(au.flaggedPts&PtsMask)
                    <<",\"first_write_host_us\":"<<firstWriteUs<<",\"last_write_host_us\":"<<lastWriteUs
                    <<",\"full_wire_bytes\":"<<fullWire<<",\"complete\":"<<(complete?"true":"false")
                    <<",\"media_data_complete\":"<<(mediaDataComplete?"true":"false")
                    <<",\"expected_shards\":"<<packets.size()<<",\"emitted_shards\":"<<emitted
                    <<",\"packetization_us\":"<<packetizationUs<<",\"serialization_us\":"<<duration
                    <<",\"admission_margin_us\":"<<(deadline-ready-duration)
                    <<",\"planned_sleep_us\":"<<plannedSleepUs<<",\"actual_sleep_us\":"<<actualSleepUs
                    <<",\"max_sleep_overshoot_us\":"<<maxOvershootUs
                    <<",\"catchup_bytes\":"<<burstBytes<<",\"failure_stage\":\""<<failureStage<<"\"}\n";
            }
            if(mediaDataComplete) {
                counters.frames++;
                if(!complete) { counters.optionalParityFrames++; counters.optionalParityPackets+=packets.size()-emitted; }
                if(info.idr) {
                    needsIdr=false;
                    if(recoveryAttempts) {
                        counters.recoveryCompletions++;
                        std::cerr<<"{\"event\":\"recovery_complete\",\"frame\":"<<id<<",\"attempts\":"<<recoveryAttempts<<"}\n";
                    }
                    recoveryAttempts=0; lastRequest=0; exhaustedReported=false;
                }
            }
            else { counters.outputDrops++; needsIdr=true; request(); }
            counters.summary(bitrate);
        }
        counters.summary(bitrate,true); return 0;
    } catch(const std::exception&) {
        counters.summary(bitrate,true); std::cerr<<"{\"event\":\"frame_rejected\",\"reason\":\"fatal_input_or_output_contract\"}\n"; return 1;
    }
}
