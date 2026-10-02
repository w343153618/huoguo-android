#pragma once
#include "media_datagram.hpp"
#include <istream>

namespace huoguo::udp {
struct H264Info { bool hasSlice=false, idr=false, reference=false, parameterSets=false; };
inline H264Info inspectAnnexB(std::span<const uint8_t> data) {
    H264Info info;
    bool hasStart=false;
    for(size_t i=0; i+3<data.size(); i++) {
        if(data[i]!=0 || data[i+1]!=0) continue;
        size_t nal=0;
        if(data[i+2]==1) nal=i+3;
        else if(data[i+2]==0 && i+4<data.size() && data[i+3]==1) nal=i+4;
        else continue;
        hasStart=true;
        const uint8_t header=data[nal],type=header&31;
        if(header&128) throw std::invalid_argument("forbidden H264 NAL bit");
        if(type==1 || type==5) {
            info.hasSlice=true; info.idr|=type==5; info.reference|=(header&0x60)!=0;
        }
        info.parameterSets|=type==7 || type==8;
        i=nal;
    }
    if(!hasStart) throw std::invalid_argument("expected complete Annex B access unit");
    return info;
}

// Existing host framing: h264 marker; optional 0x80000000,W,H geometry;
// otherwise 64-bit flagged PTS + 32-bit length + complete Annex B payload.
struct HostAccessUnit { uint64_t flaggedPts=0; Bytes payload; };
inline bool readBytes(std::istream& input,std::span<uint8_t> output,bool allowEof=false) {
    input.read(reinterpret_cast<char*>(output.data()),std::streamsize(output.size()));
    if(input.gcount()==0 && input.eof() && allowEof) return false;
    if(input.gcount()!=std::streamsize(output.size())) throw std::invalid_argument("truncated host video framing");
    return true;
}
inline bool readHostAccessUnit(std::istream& input,HostAccessUnit& unit) {
    for(;;) {
        std::array<uint8_t,12> header{};
        if(!readBytes(input,std::span(header).first(4),true)) return false;
        if(get(header,0,4)&0x80000000) {
            readBytes(input,std::span(header).subspan(4));
            const auto width=get(header,4,4),height=get(header,8,4);
            if(!width || !height || width>8192 || height>8192)
                throw std::invalid_argument("host geometry bounds");
            continue;
        }
        readBytes(input,std::span(header).subspan(4));
        const auto size=get(header,8,4);
        if(!size || size>MaxFrameBytes) throw std::invalid_argument("host access unit bounds");
        unit.flaggedPts=get(header,0,8); unit.payload.resize(size); readBytes(input,unit.payload);
        return true;
    }
}
}
