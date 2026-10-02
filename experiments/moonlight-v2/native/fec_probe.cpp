#include "rs.h"
#include <algorithm>
#include <array>
#include <iostream>
#include <stdexcept>
#include <vector>

int main() {
    constexpr int data=10, parity=2, total=data+parity, bytes=1200;
    reed_solomon_init();
    reed_solomon* rs=reed_solomon_new(data,parity);
    if(!rs)throw std::runtime_error("Reed-Solomon initialization failed");
    const int padded=reed_solomon_padded_size(bytes);
    std::array<uint8_t*,total> shards{};
    for(auto& p:shards) {
        p=static_cast<uint8_t*>(reed_solomon_aligned_alloc(padded));
        if(!p)throw std::runtime_error("FEC allocation failed");
    }
    std::array<std::vector<uint8_t>,total> originals;
    for(int i=0;i<data;i++)for(int j=0;j<padded;j++)shards[i][j]=(i*31+j*17+j/251)%256;
    if(reed_solomon_encode(rs,shards.data(),total,padded)!=0)
        throw std::runtime_error("FEC encode failed");
    for(int i=0;i<total;i++)originals[i].assign(shards[i],shards[i]+padded);
    auto reset=[&]() { for(int i=0;i<total;i++)std::copy(originals[i].begin(),originals[i].end(),shards[i]); };
    int recovered=0;
    for(int first=0;first<total;first++)for(int second=first+1;second<total;second++) {
        reset();std::array<uint8_t,total> missing{};missing[first]=missing[second]=1;
        std::fill(shards[first],shards[first]+padded,0);
        std::fill(shards[second],shards[second]+padded,0);
        if(reed_solomon_decode(rs,shards.data(),missing.data(),total,padded)!=0)
            throw std::runtime_error("Two-erasure reconstruction failed");
        for(int i=0;i<data;i++)if(!std::equal(originals[i].begin(),originals[i].end(),shards[i]))
            throw std::runtime_error("Reconstructed payload differs");
        recovered++;
    }
    reset();std::array<uint8_t,total> missing{};missing[0]=missing[1]=missing[2]=1;
    if(reed_solomon_decode(rs,shards.data(),missing.data(),total,padded)==0)
        throw std::runtime_error("Three erasures must exceed two-parity recovery budget");
    for(auto p:shards)reed_solomon_free(p);
    reed_solomon_release(rs);
    std::cout<<"PASS Moonlight nanors FEC: "<<recovered
             <<" two-erasure combinations recovered exactly; three rejected; 20% parity overhead. Synthetic only.\n";
}
