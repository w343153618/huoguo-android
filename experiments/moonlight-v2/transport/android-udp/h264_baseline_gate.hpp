#pragma once
#include "logical_frame.hpp"

namespace huoguo::android_udp {
struct Nal { std::span<const uint8_t> bytes; uint8_t type() const { return bytes[0]&31; } };
inline std::vector<Nal> annexNals(std::span<const uint8_t> bytes) {
    std::vector<std::pair<size_t,size_t>> starts;
    for(size_t i=0;i+3<bytes.size();i++) {
        if(bytes[i] || bytes[i+1]) continue;
        size_t payload=0;
        if(bytes[i+2]==1) payload=i+3;
        else if(i+4<bytes.size() && bytes[i+2]==0 && bytes[i+3]==1) payload=i+4;
        if(payload) { starts.emplace_back(i,payload); i=payload-1; }
    }
    if(starts.empty() || starts.front().first>3 || starts.size()>128) throw std::invalid_argument("Annex B NAL bounds");
    for(size_t i=0;i<starts.front().first;i++) if(bytes[i]) throw std::invalid_argument("Annex B prefix");
    std::vector<Nal> out;
    for(size_t i=0;i<starts.size();i++) {
        const size_t end=i+1<starts.size() ? starts[i+1].first:bytes.size();
        if(end<=starts[i].second || (bytes[starts[i].second]&128)) throw std::invalid_argument("NAL header");
        out.push_back({bytes.subspan(starts[i].second,end-starts[i].second)});
    }
    return out;
}
class Bits {
    Bytes bytes_; size_t bit_=0;
public:
    explicit Bits(std::span<const uint8_t> nal) {
        unsigned zeros=0;
        for(size_t i=1;i<nal.size();i++) {
            const uint8_t v=nal[i];
            if(zeros>=2 && v==3) {
                if(i+1>=nal.size() || nal[i+1]>3) throw std::invalid_argument("RBSP escape");
                zeros=0; continue;
            }
            bytes_.push_back(v); zeros=v==0 ? zeros+1:0;
        }
    }
    uint32_t read(unsigned count=1) {
        if(count>32 || bit_+count>bytes_.size()*8) throw std::invalid_argument("truncated H264 syntax");
        uint32_t value=0;
        for(unsigned i=0;i<count;i++,bit_++) value=(value<<1)|((bytes_[bit_/8]>>(7-bit_%8))&1);
        return value;
    }
    uint32_t ue() {
        unsigned zeros=0;
        while(!read()) if(++zeros>30) throw std::invalid_argument("H264 Exp-Golomb bounds");
        return (1U<<zeros)-1+read(zeros);
    }
    int32_t se() { const auto value=ue(); return value&1 ? int32_t((value+1)/2):-int32_t(value/2); }
};
struct Sps { uint32_t id=0,frameBits=0,pocType=0,pocBits=0,width=0,height=0; };
struct Pps { uint32_t id=0,sps=0; };
class BaselineGate {
    std::map<uint32_t,Sps> sps_;
    std::map<uint32_t,Pps> pps_;
    void parameter(const Nal& nal) {
        if(nal.type()==7) {
            Bits b(nal.bytes); const auto profile=b.read(8); b.read(8); b.read(8);
            if(profile!=66) throw std::invalid_argument("only verified Baseline supported");
            Sps s; s.id=b.ue(); const auto frameMinus4=b.ue(); s.pocType=b.ue();
            if(s.id>31 || frameMinus4>12 || (s.pocType!=0 && s.pocType!=2)) throw std::invalid_argument("unsupported SPS syntax");
            s.frameBits=frameMinus4+4;
            if(s.pocType==0) { const auto minus4=b.ue(); if(minus4>12) throw std::invalid_argument("POC bounds"); s.pocBits=minus4+4; }
            if(b.ue()!=1) throw std::invalid_argument("only one-reference H264 supported");
            if(b.read()) throw std::invalid_argument("frame_num gaps unsupported");
            const auto mbWidth=b.ue(),mbHeight=b.ue();
            if(mbWidth>511 || mbHeight>511 || !b.read()) throw std::invalid_argument("interlaced/oversized SPS unsupported");
            b.read(); uint32_t left=0,right=0,top=0,bottom=0;
            if(b.read()) { left=b.ue(); right=b.ue(); top=b.ue(); bottom=b.ue(); }
            const uint64_t codedW=uint64_t(mbWidth+1)*16,codedH=uint64_t(mbHeight+1)*16;
            if(uint64_t(left)+right>=codedW/2 || uint64_t(top)+bottom>=codedH/2) throw std::invalid_argument("SPS crop bounds");
            s.width=uint32_t(codedW-2*(uint64_t(left)+right)); s.height=uint32_t(codedH-2*(uint64_t(top)+bottom));
            if(!sps_.contains(s.id) && sps_.size()>=4) throw std::invalid_argument("SPS cache bounds");
            sps_[s.id]=s;
        } else if(nal.type()==8) {
            Bits b(nal.bytes); Pps p; p.id=b.ue(); p.sps=b.ue();
            if(p.id>255 || p.sps>31 || b.read() || b.read() || b.ue()) throw std::invalid_argument("CABAC/fields/FMO unsupported");
            if(b.ue() || b.ue() || b.read() || b.read(2)) throw std::invalid_argument("multiple/weighted references unsupported");
            b.se(); b.se(); b.se(); b.read(); b.read();
            if(b.read()) throw std::invalid_argument("redundant slices unsupported");
            if(!pps_.contains(p.id) && pps_.size()>=16) throw std::invalid_argument("PPS cache bounds");
            pps_[p.id]=p;
        }
    }
public:
    void configuration(std::span<const uint8_t> bytes) {
        bool found=false;
        for(const auto& nal:annexNals(bytes)) {
            if(nal.type()!=7 && nal.type()!=8 && nal.type()!=6 && nal.type()!=9) throw std::invalid_argument("unsupported config NAL");
            if(nal.type()==7 || nal.type()==8) { parameter(nal); found=true; }
        }
        if(!found || sps_.empty() || pps_.empty()) throw std::invalid_argument("missing SPS/PPS");
    }
    H264Info accessUnit(std::span<const uint8_t> bytes,uint32_t width,uint32_t height) {
        H264Info info;
        for(const auto& nal:annexNals(bytes)) {
            const auto type=nal.type();
            if(type==7 || type==8) { parameter(nal); info.parameterSets=true; continue; }
            if(type==6 || type==9 || type==12) continue;
            if(type!=1 && type!=5) throw std::invalid_argument("unsupported AU NAL");
            Bits b(nal.bytes); b.ue(); const auto slice=b.ue(); const auto ppsId=b.ue();
            if(slice>9 || (slice%5!=0 && slice%5!=2)) throw std::invalid_argument("B/SP/SI slices unsupported");
            const bool intra=slice%5==2,idr=type==5,reference=nal.bytes[0]&0x60;
            if(idr && (!intra || !reference)) throw std::invalid_argument("invalid IDR slice");
            const auto p=pps_.find(ppsId);
            if(p==pps_.end()) throw std::invalid_argument("slice references unknown PPS");
            const auto s=sps_.find(p->second.sps);
            if(s==sps_.end() || s->second.width!=width || s->second.height!=height) throw std::invalid_argument("SPS/geometry mismatch");
            b.read(s->second.frameBits);
            if(idr) b.ue();
            if(s->second.pocType==0) b.read(s->second.pocBits);
            if(!intra) {
                if(b.read() && b.ue()!=0) throw std::invalid_argument("multiple slice references unsupported");
                if(b.read()) throw std::invalid_argument("reference-list modifications unsupported");
            }
            if(reference) {
                if(idr) { b.read(); if(b.read()) throw std::invalid_argument("long-term IDR reference unsupported"); }
                else if(b.read()) throw std::invalid_argument("adaptive reference marking unsupported");
            }
            info.hasSlice=true; info.idr|=idr; info.reference|=reference;
        }
        if(!info.hasSlice) throw std::invalid_argument("AU has no supported slice");
        return info;
    }
};
} // namespace huoguo::android_udp
