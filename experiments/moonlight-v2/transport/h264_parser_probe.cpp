#include "h264_access_unit.hpp"
#include <iostream>
#include <sstream>
using namespace huoguo::udp;
static void require(bool condition,const char* name) { if(!condition) throw std::runtime_error(name); }
int main() {
    auto key=inspectAnnexB(Bytes{0,0,0,1,0x67,0x11,0,0,1,0x68,0x22,0,0,0,1,0x65,0x33});
    require(key.hasSlice && key.idr && key.reference && key.parameterSets,"IDR/SPS/PPS");
    auto nonref=inspectAnnexB(Bytes{0,0,1,0x01,0x99});
    require(nonref.hasSlice && !nonref.idr && !nonref.reference,"non-reference slice");
    auto config=inspectAnnexB(Bytes{0,0,0,1,0x67,0x11,0,0,1,0x68,0x22});
    require(!config.hasSlice && config.parameterSets,"configuration unit");
    for(auto invalid:{Bytes{1,2,3,4},Bytes{0,0,1,0xe5,1},Bytes{0,0,1}}) {
        bool rejected=false;
        try { inspectAnnexB(invalid); } catch(const std::invalid_argument&) { rejected=true; }
        require(rejected,"malformed NAL accepted");
    }
    Bytes framed(12,0); put(framed,0,0x80000000,4); put(framed,4,540,4); put(framed,8,1200,4);
    Bytes prefix(12,0); put(prefix,0,1000000,8); put(prefix,8,5,4);
    framed.insert(framed.end(),prefix.begin(),prefix.end());
    auto slice=Bytes{0,0,1,0x65,0x33}; framed.insert(framed.end(),slice.begin(),slice.end());
    std::istringstream stream(std::string(framed.begin(),framed.end())); HostAccessUnit unit;
    require(readHostAccessUnit(stream,unit) && unit.flaggedPts==1000000 && unit.payload==slice,"host framing/geometry");
    require(!readHostAccessUnit(stream,unit),"clean EOF");
    framed.pop_back(); std::istringstream truncated(std::string(framed.begin(),framed.end()));
    bool rejected=false;
    try { readHostAccessUnit(truncated,unit); } catch(const std::invalid_argument&) { rejected=true; }
    require(rejected,"truncated host access unit accepted");
    std::cout<<"PASS existing H264 framing, real NAL IDR/reference/config classification, bounds/truncation\n";
}
