#include "media_datagram.hpp"
#include <iostream>
#include <random>
using huoguo::udp::Pacer;
namespace {
unsigned checks=0,windowChecks=0;
void check(bool okay,const char* reason){if(!okay)throw std::runtime_error(reason);checks++;}
template<class F>void rejects(F fn,const char* reason){bool bad=false;try{fn();}catch(const std::exception&){bad=true;}check(bad,reason);}
struct Send {uint64_t at;size_t bytes;};
struct Run {std::vector<Send> sends;uint64_t span;};
Run simulate(size_t burst,uint64_t ordinaryOversleep,int stallPhase=-1){
    constexpr uint64_t start=1000000,deadline=start+80000;
    Pacer pacer(16000000,burst);uint64_t now=start;Run result;
    for(unsigned packet=0;packet<120;packet++){
        const auto ready=pacer.readyUs(now);
        const auto at=pacer.reserveBefore(now,1148,deadline);
        if(!at)break;
        check(*at==ready,"admission ready time and actual next slot agree");
        const auto actual=*at+ordinaryOversleep+(stallPhase>=0&&int(packet%10)==stallPhase?2000:0);
        if(actual>=deadline){pacer.cancelReservation();break;}
        pacer.sentAt(actual);result.sends.push_back({actual,1148});now=actual+20;
    }
    result.span=result.sends.empty()?0:result.sends.back().at-start;return result;
}
void envelope(const std::vector<Send>& sends,uint64_t bitrate,size_t bucket){
    size_t maxPacket=0;for(const auto& s:sends)maxPacket=std::max(maxPacket,s.bytes);
    for(size_t first=0;first<sends.size();first++){
        uint64_t bytes=0;
        for(size_t last=first;last<sends.size();last++){
            bytes+=sends[last].bytes;
            const auto allowance=bitrate*(sends[last].at-sends[first].at)+(bucket+maxPacket)*8000000ULL;
            if(bytes*8000000ULL>allowance)throw std::runtime_error("actual-send sliding window exceeds rate*time+bucket+onepacket");
            windowChecks++;
        }
    }
}
}
int main(){try{
    Pacer strict(4000000);const uint64_t t=1000000;
    check(strict.burstBytes()==0,"strict default");
    check(strict.slot(t,1000)==t&&strict.slot(t,1000)==t+2000,"default legacy pace sequence");
    check(strict.slot(t+50000,1000)==t+50000&&strict.slot(t+50000,1000)==t+52000,"default legacy strict reanchor");
    strict.sentAt(t+999999);check(strict.readyUs(t+50000)==t+54000,"sentAt default no-op");
    Pacer denied(4000000);check(denied.reserveBefore(t,1000,t+2000).value()==t,"exact default deadline");
    for(unsigned i=0;i<1000;i++)check(!denied.reserveBefore(t,1000,t+2000),"reject cannot accumulate pacing debt");
    check(denied.readyUs(t)==t+2000,"legacy rejection state unchanged");
    for(size_t bucket:{size_t(0),size_t(1),size_t(2048),size_t(4096)})check(Pacer(16000000,bucket).burstBytes()==bucket,"bucket range accepts endpoints");
    rejects([]{Pacer invalid(16000000,4097);},"bucket4097 rejected");
    rejects([]{Pacer invalid(16000000,size_t(-1));},"negative converted bucket rejected");
    Pacer pending(16000000,2048);
    const auto a=pending.reserveBefore(t,1148,t+80000).value();
    rejects([&]{pending.reserveBefore(t,1148,t+80000);},"must settle actual send before another reservation");
    rejects([&]{pending.sentAt(a-1);},"actual send cannot precede reserved slot");
    pending.sentAt(a+5000);
    rejects([&]{pending.sentAt(a+5000);},"duplicate settlement rejected");
    auto b=pending.reserveBefore(a+5000,1148,t+80000).value();pending.sentAt(b);
    Pacer canceled(16000000,2048);
    canceled.reserveBefore(t,1148,t+80000);canceled.cancelReservation();
    check(canceled.readyUs(t)==t,"unsent reservation restores previous debt");
    rejects([&]{canceled.cancelReservation();},"cancel without reservation rejected");
    check(!canceled.reserveBefore(t,1148,t+573)&&canceled.readyUs(t)==t,"deadline rejection consumes no credit");

    const auto plain=simulate(0,100),catchup=simulate(2048,100);
    check(plain.sends.size()==120&&catchup.sends.size()==120&&catchup.span<80000,"100us sleep overshoot does not accumulate into 80ms failure");
    envelope(plain.sends,16000000,0);envelope(catchup.sends,16000000,2048);
    const auto stalledStrict=simulate(0,100,4),stalledCatchup=simulate(2048,100,4);
    if(!(stalledStrict.sends.size()<stalledCatchup.sends.size()&&stalledCatchup.span<80000))throw std::runtime_error(
        "periodic model strict="+std::to_string(stalledStrict.sends.size())+"/"+std::to_string(stalledStrict.span)+
        " catchup="+std::to_string(stalledCatchup.sends.size())+"/"+std::to_string(stalledCatchup.span));
    checks++;
    // Legacy strict pacing does not settle actual late writes; preserving its
    // default does not promise an actual-send burst envelope after a long stall.
    envelope(stalledCatchup.sends,16000000,2048);
    const auto stalledTail=simulate(2048,100,9);
    check(stalledTail.sends.size()<120&&stalledTail.span<80000,"late final stall still respects deadline rather than extending window");
    envelope(stalledTail.sends,16000000,2048);
    // Vary packet size/rate and inject long stalls. Check ACTUAL write times,
    // including the overdue packet; never only verify scheduled timestamps.
    std::mt19937 random(7);
    for(uint64_t rate:{500000ULL,1000000ULL,16000000ULL,40000000ULL}){
        for(size_t bucket:{size_t(0),size_t(1),size_t(2048),size_t(4096)}){
            Pacer pacer(rate,bucket);uint64_t now=t;std::vector<Send> samples;
            for(unsigned i=0;i<180;i++){
                const size_t bytes=100+random()%1100;
                const auto at=pacer.slot(now,bytes);
                const auto actual=at+(i%11==0?3000:random()%200);
                pacer.sentAt(actual);samples.push_back({actual,bytes});now=actual+random()%30;
            }
            if(bucket)envelope(samples,rate,bucket);
        }
    }
    // Exact small-packet boundary catches a 1us round-up giving 4 extra bytes
    // of credit at 40Mbps/4096B, which mixed sizes can hide under maxpacket.
    Pacer tiny(40000000,4096);std::vector<Send> exactBoundary;uint64_t now=t;
    for(unsigned i=0;i<100;i++){const auto at=tiny.slot(now,100);tiny.sentAt(at);exactBoundary.push_back({at,100});now=at;}
    envelope(exactBoundary,40000000,4096);
    std::cout<<"{\"passed\":true,\"scope\":\"pure_timing_actual_Pacer_no_codec_socket_or_phone\",\"checks\":"<<checks
        <<",\"sliding_window_checks\":"<<windowChecks
        <<",\"ordinary_100us_strict_sent\":"<<plain.sends.size()<<",\"ordinary_100us_strict_span_us\":"<<plain.span
        <<",\"ordinary_100us_catchup_sent\":"<<catchup.sends.size()<<",\"ordinary_100us_catchup_span_us\":"<<catchup.span
        <<",\"periodic_2ms_phase4_strict_sent\":"<<stalledStrict.sends.size()<<",\"periodic_2ms_phase4_strict_span_us\":"<<stalledStrict.span
        <<",\"periodic_2ms_phase4_catchup_sent\":"<<stalledCatchup.sends.size()<<",\"periodic_2ms_phase4_catchup_span_us\":"<<stalledCatchup.span
        <<",\"periodic_2ms_phase9_catchup_sent\":"<<stalledTail.sends.size()<<",\"periodic_2ms_phase9_catchup_span_us\":"<<stalledTail.span<<"}\n";
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
