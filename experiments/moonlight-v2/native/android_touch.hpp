#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <map>
#include <stdexcept>
#include <vector>

namespace huoguo {
// Moonlight LiSendTouchEvent types. Never fall back to mouse events.
enum class TouchType : uint8_t { Down=1, Up=2, Move=3, Cancel=4, CancelAll=7 };
using ControlPacket = std::array<uint8_t,32>;
struct Point { float x,y,pressure; };
class AndroidTouch {
    uint16_t width_,height_;
    std::map<uint32_t,Point> active_;
    static void put(ControlPacket& p,size_t at,uint64_t value,size_t bytes) {
        for(size_t i=0;i<bytes;i++)p[at+bytes-1-i]=uint8_t(value>>(8*i));
    }
    ControlPacket packet(uint32_t id,uint8_t action,Point point) const {
        ControlPacket p{};p[0]=2;p[1]=action;
        put(p,2,id,8);
        put(p,10,std::min<uint32_t>(width_-1,uint32_t(point.x*width_)),4);
        put(p,14,std::min<uint32_t>(height_-1,uint32_t(point.y*height_)),4);
        put(p,18,width_,2);put(p,20,height_,2);
        put(p,22,action==1?0:uint16_t(std::round(point.pressure*65535.f)),2);
        // Mouse action buttons and buttons remain zero: touchscreen, not mouse.
        return p;
    }
public:
    AndroidTouch(uint16_t width,uint16_t height):width_(width),height_(height) {
        if(!width||!height)throw std::invalid_argument("empty Android display");
    }
    size_t activeCount() const { return active_.size(); }
    std::vector<ControlPacket> event(TouchType type,uint32_t id,Point point={}) {
        if(type==TouchType::CancelAll) {
            std::vector<ControlPacket> out;
            for(const auto& [pointer,last]:active_)out.push_back(packet(pointer,1,last));
            active_.clear();return out;
        }
        auto existing=active_.find(id);
        if(type==TouchType::Cancel) {
            if(existing==active_.end())throw std::invalid_argument("unknown touch cancel");
            auto p=packet(id,1,existing->second);active_.erase(existing);return {p};
        }
        if(!std::isfinite(point.x)||!std::isfinite(point.y)||!std::isfinite(point.pressure)
                ||point.x<0||point.x>1||point.y<0||point.y>1||point.pressure<0||point.pressure>1)
            throw std::invalid_argument("touch must be normalized inside streamed Android content");
        if(type==TouchType::Down) {
            if(existing!=active_.end()||active_.size()>=10)throw std::invalid_argument("duplicate or excessive pointer");
            active_[id]=point;return {packet(id,0,point)};
        }
        if(existing==active_.end())throw std::invalid_argument("touch without matching down");
        if(type==TouchType::Move) { existing->second=point;return {packet(id,2,point)}; }
        if(type==TouchType::Up) { auto p=packet(id,1,point);active_.erase(existing);return {p}; }
        throw std::invalid_argument("unsupported touch type: no mouse fallback");
    }
    std::vector<ControlPacket> resize(uint16_t width,uint16_t height) {
        if(!width||!height)throw std::invalid_argument("empty Android display");
        auto releases=event(TouchType::CancelAll,0);width_=width;height_=height;return releases;
    }
};
}
