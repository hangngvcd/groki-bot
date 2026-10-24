// SPDX-License-Identifier: BSL-1.0
#include "lingling/assets.hpp"
#include <cstring>
namespace lingling {
namespace {
std::uint16_t u16(const std::uint8_t* p) { return p[0] | (std::uint16_t(p[1]) << 8); }
std::uint32_t u32(const std::uint8_t* p) { return u16(p) | (std::uint32_t(u16(p+2)) << 16); }
}
bool AssetPack::parse(const std::uint8_t* data, std::size_t len) {
    data_=nullptr; count_=0;
    if (!data || len < 16 || std::memcmp(data,"LLAP",4) || u16(data+4)!=1 || u32(data+12)!=0) return false;
    const std::size_t count=u16(data+6), size=u32(data+8);
    if (size>len || size<16 || count>(size-16)/kEntrySize) return false;
    const auto table_end=16+count*kEntrySize;
    for (std::size_t i=0;i<count;++i) {
        const auto* p=data+16+i*kEntrySize;
        const auto* nul=static_cast<const std::uint8_t*>(std::memchr(p,0,32));
        if (!nul || nul==p) return false;
        for (const auto* tail=nul;tail<p+32;++tail) if (*tail) return false;
        const std::size_t offset=u32(p+32), n=u32(p+36);
        const auto kind=u16(p+40); const auto rate=u32(p+44);
        if (offset<table_end || offset%4 || offset>size || n>size-offset || !n ||
            (kind!=1 && kind!=2) || u16(p+42)!=0 || (kind==1 && rate!=0) ||
            (kind==2 && (rate<8000 || rate>192000 || n%2))) return false;
    }
    data_=data; count_=count; return true;
}
std::optional<Entry> AssetPack::find(std::string_view name) const {
    for (std::size_t i=0;i<count_;++i) {
        const auto* p=data_+16+i*kEntrySize;
        const auto* nul=static_cast<const std::uint8_t*>(std::memchr(p,0,32));
        if (nul && std::string_view(reinterpret_cast<const char*>(p),nul-p)==name)
            return Entry{std::string_view(reinterpret_cast<const char*>(p),nul-p),{data_+u32(p+32),u32(p+36)},u16(p+40),u32(p+44),u32(p+48)};
    }
    return std::nullopt;
}
bool AssetPack::verify(const Entry& e) const {
    std::uint32_t crc=0xffffffff;
    for (auto byte:e.data) {
        crc^=byte;
        for (int bit=0;bit<8;++bit) crc=(crc>>1) ^ (0xedb88320u & (0u-(crc&1)));
    }
    return (crc^0xffffffff)==e.crc32;
}
std::string face_slot_name(std::uint8_t form,std::string_view expr) {
    return "face.f"+std::to_string(form)+"."+std::string(expr);
}
std::optional<Entry> find_face(const AssetPack& pack,std::uint8_t form,std::string_view expr) {
    if (auto e=pack.find(face_slot_name(form,expr))) return e;
    return pack.find("face."+std::string(expr));
}
std::vector<EvolutionStep> plan_evolution(std::uint8_t from,std::uint8_t to) {
    std::vector<EvolutionStep> result;
    if (from<1 || to>4 || to<=from) return result;
    for (unsigned f=from+1;f<=to;++f) result.push_back({std::uint8_t(f),(f-from-1)*500u});
    return result;
}
}
