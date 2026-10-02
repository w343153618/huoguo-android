#pragma once
#include "../h264_access_unit.hpp"

namespace huoguo::android_udp {
using namespace huoguo::udp;
constexpr size_t LogicalHeaderBytes=20,MaxConfigBytes=65536;
constexpr uint64_t ConfigFlag=1ULL<<62,IdrFlag=1ULL<<61,PtsMask=IdrFlag-1;
struct LogicalFrame {
    uint32_t width=0,height=0;
    uint64_t flaggedPts=0;
    Bytes config,accessUnit;
};
inline Bytes serializeLogical(const LogicalFrame& frame) {
    const size_t size=LogicalHeaderBytes+frame.config.size()+frame.accessUnit.size();
    if(!frame.width || !frame.height || frame.width>8192 || frame.height>8192 ||
       frame.config.size()>MaxConfigBytes || frame.accessUnit.empty() || size>MaxFrameBytes ||
       (frame.flaggedPts&ConfigFlag) || (frame.flaggedPts>>63) ||
       (!(frame.flaggedPts&IdrFlag) && !frame.config.empty())) throw std::invalid_argument("logical frame bounds");
    Bytes out(size); put(out,0,frame.width,4); put(out,4,frame.height,4);
    put(out,8,frame.flaggedPts,8); put(out,16,frame.config.size(),4);
    std::copy(frame.config.begin(),frame.config.end(),out.begin()+LogicalHeaderBytes);
    std::copy(frame.accessUnit.begin(),frame.accessUnit.end(),out.begin()+LogicalHeaderBytes+frame.config.size());
    return out;
}
inline LogicalFrame parseLogical(std::span<const uint8_t> bytes) {
    if(bytes.size()<=LogicalHeaderBytes || bytes.size()>MaxFrameBytes) throw std::invalid_argument("logical length");
    LogicalFrame frame; frame.width=uint32_t(get(bytes,0,4)); frame.height=uint32_t(get(bytes,4,4));
    frame.flaggedPts=get(bytes,8,8); const size_t configBytes=get(bytes,16,4);
    if(!frame.width || !frame.height || frame.width>8192 || frame.height>8192 ||
       configBytes>MaxConfigBytes || configBytes>=bytes.size()-LogicalHeaderBytes ||
       (frame.flaggedPts&ConfigFlag) || (frame.flaggedPts>>63) ||
       (!(frame.flaggedPts&IdrFlag) && configBytes)) throw std::invalid_argument("logical metadata");
    frame.config.assign(bytes.begin()+LogicalHeaderBytes,bytes.begin()+LogicalHeaderBytes+configBytes);
    frame.accessUnit.assign(bytes.begin()+LogicalHeaderBytes+configBytes,bytes.end());
    const auto info=inspectAnnexB(frame.accessUnit);
    if(!info.hasSlice || info.idr!=bool(frame.flaggedPts&IdrFlag)) throw std::invalid_argument("logical IDR flag");
    return frame;
}

// Existing HostSession channel geometry must be retained rather than skipped.
class HostReader {
    std::istream& input_;
public:
    uint32_t width=0,height=0;
    explicit HostReader(std::istream& input):input_(input) {
        std::array<uint8_t,4> marker{}; readBytes(input_,marker);
        if(std::string(marker.begin(),marker.end())!="h264") throw std::invalid_argument("host codec marker");
    }
    bool next(HostAccessUnit& unit) {
        for(;;) {
            std::array<uint8_t,12> header{};
            if(!readBytes(input_,std::span(header).first(4),true)) return false;
            if(get(header,0,4)==0x80000000) {
                readBytes(input_,std::span(header).subspan(4));
                width=uint32_t(get(header,4,4)); height=uint32_t(get(header,8,4));
                if(!width || !height || width>8192 || height>8192) throw std::invalid_argument("host geometry");
                continue;
            }
            if(header[0]&0x80) throw std::invalid_argument("unsupported host PTS flag");
            readBytes(input_,std::span(header).subspan(4));
            const auto bytes=get(header,8,4);
            if(!bytes || bytes>MaxFrameBytes) throw std::invalid_argument("host AU size");
            unit.flaggedPts=get(header,0,8); unit.payload.resize(bytes); readBytes(input_,unit.payload); return true;
        }
    }
};
} // namespace huoguo::android_udp
