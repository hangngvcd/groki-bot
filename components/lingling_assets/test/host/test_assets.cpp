// SPDX-License-Identifier: BSL-1.0
#include "lingling/assets.hpp"
#include <cassert>
#include <cstring>
#include <vector>

int main() {
    lingling::AssetPack pack;
    auto put=[](auto& bytes,unsigned at,std::uint32_t value) {
        for (unsigned i=0;i<4;++i) bytes[at+i]=(value>>(i*8))&255;
    };
    std::vector<std::uint8_t> valid(72);
    std::memcpy(valid.data(),"LLAP",4);
    valid[4]=1; valid[6]=1; put(valid,8,72);
    std::memcpy(valid.data()+16,"face.f1.neutral",15);
    put(valid,48,68); put(valid,52,4); valid[56]=1;
    put(valid,64,0x2144df1c); // IEEE CRC32 of four zero bytes.
    assert(pack.parse(valid.data(),valid.size()));
    assert(pack.count()==1 && !pack.find("missing"));
    assert(pack.verify(*pack.find("face.f1.neutral")));
    assert(lingling::find_face(pack,1,"neutral"));
    assert(!lingling::find_face(pack,2,"neutral"));
    auto invalid=[&](unsigned at,std::uint32_t value) {
        auto bytes=valid; put(bytes,at,value);
        assert(!pack.parse(bytes.data(),bytes.size())); assert(pack.count()==0);
        assert(!pack.find("face.f1.neutral"));
    };
    { // Ticket 15c: an erased (0xFF) or zeroed pack region is "no pack", never a crash.
        std::vector<std::uint8_t> erased(4096,0xFF), zero(4096,0);
        assert(!pack.parse(erased.data(),erased.size()) && pack.count()==0);
        assert(!pack.parse(zero.data(),zero.size()) && !pack.find("vo.intro.1"));
        assert(!lingling::find_face(pack,1,"neutral"));
        assert(!pack.parse(nullptr,0) && !pack.parse(valid.data(),15));
    }
    invalid(0,0); // magic
    invalid(4,2); // version
    invalid(8,73); invalid(8,15); // pack length
    invalid(12,1); // reserved
    invalid(48,0xfffffffcu); invalid(48,64); invalid(48,69); // offsets
    invalid(52,0xffffffffu); invalid(52,0); // length / overflow
    invalid(56,3); invalid(60,16000); // kind / PNG rate
    auto bytes=valid; bytes[6]=255; assert(!pack.parse(bytes.data(),bytes.size()));
    bytes=valid; std::memset(bytes.data()+16,'x',32); assert(!pack.parse(bytes.data(),bytes.size()));
    bytes=valid; bytes[47]='x'; assert(!pack.parse(bytes.data(),bytes.size())); // padding
    assert(!pack.parse(nullptr,100));
    assert(!pack.parse(valid.data(),15));
    bytes=valid; bytes[68]=1;
    assert(pack.parse(bytes.data(),bytes.size())); assert(!pack.verify(*pack.find("face.f1.neutral")));
    // A form-less face is used if a form-specific name is absent.
    bytes=valid; std::memset(bytes.data()+16,0,32);
    std::memcpy(bytes.data()+16,"face.neutral",12);
    assert(pack.parse(bytes.data(),bytes.size()));
    assert(lingling::find_face(pack,4,"neutral")->name=="face.neutral");
    // PCM must have a usable rate and an even number of bytes.
    bytes=valid; bytes[56]=2;
    assert(!pack.parse(bytes.data(),bytes.size()));
    put(bytes,60,16000); assert(pack.parse(bytes.data(),bytes.size()));
    assert(pack.find("face.f1.neutral")->sample_rate==16000);
    put(bytes,52,3); assert(!pack.parse(bytes.data(),bytes.size()));
    const auto steps=lingling::plan_evolution(1,4);
    assert(steps.size()==3 && steps[0].form==2 && steps[0].at_ms==0 && steps[2].at_ms==1000);
    assert(lingling::plan_evolution(4,1).empty());
    assert(lingling::plan_evolution(0,4).empty());
    assert(lingling::plan_evolution(1,5).empty());
    assert(lingling::face_slot_name(3,"happy")=="face.f3.happy");
}
