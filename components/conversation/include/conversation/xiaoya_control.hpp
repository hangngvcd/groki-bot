// SPDX-License-Identifier: BSL-1.0
#pragma once
#include "conversation/xiaoya_command.hpp"
#include <cstring>
#include <optional>
#include <cJSON.h>
namespace stackchan::conversation {
// Validate the native-size header before handing untrusted bytes to a PNG decoder.
inline bool glyph_png_header(const std::vector<std::uint8_t>& png) {
    static constexpr std::uint8_t signature[] = {137,80,78,71,13,10,26,10};
    return png.size() >= 33 && png.size() <= 24576 &&
        std::memcmp(png.data(), signature, 8) == 0 &&
        png[8] == 0 && png[9] == 0 && png[10] == 0 && png[11] == 13 &&
        std::memcmp(png.data()+12, "IHDR", 4) == 0 &&
        png[16] == 0 && png[17] == 0 && png[18] == 0 && png[19] == 240 &&
        png[20] == 0 && png[21] == 0 && png[22] == 0 && png[23] == 240;
}
inline std::shared_ptr<const std::vector<std::uint8_t>> decode_glyph_png(const char* text) {
    const auto n = std::strlen(text);
    if (!n || n > 32768 || n % 4) return {};
    auto value=[](char c)->int {
        if (c >= 'A' && c <= 'Z') return c-'A';
        if (c >= 'a' && c <= 'z') return c-'a'+26;
        if (c >= '0' && c <= '9') return c-'0'+52;
        return c == '+' ? 62 : c == '/' ? 63 : -1;
    };
    auto bytes = std::make_shared<std::vector<std::uint8_t>>();
    bytes->reserve(n / 4 * 3);
    for (std::size_t i=0; i<n; i+=4) {
        const int a=value(text[i]), b=value(text[i+1]);
        const bool pad2=text[i+2]=='=', pad3=text[i+3]=='=';
        const int c=pad2 ? 0 : value(text[i+2]), d=pad3 ? 0 : value(text[i+3]);
        if (a<0 || b<0 || c<0 || d<0 || (pad2 && !pad3) ||
            ((pad2 || pad3) && i+4!=n) || (pad2 && (b & 15)) ||
            (pad3 && !pad2 && (c & 3))) return {};
        bytes->push_back(static_cast<std::uint8_t>((a<<2) | (b>>4)));
        if (!pad2) bytes->push_back(static_cast<std::uint8_t>((b<<4) | (c>>2)));
        if (!pad3) bytes->push_back(static_cast<std::uint8_t>((c<<6) | d));
    }
    return glyph_png_header(*bytes) ? bytes : nullptr;
}
inline std::optional<XiaoyaCommand> parse_xiaoya(const cJSON* root) {
    const auto* cmd=cJSON_GetObjectItemCaseSensitive(root,"cmd");
    if (!cJSON_IsString(cmd)) return {};
    XiaoyaCommand out;
    if (!std::strcmp(cmd->valuestring,"intro")) {
        if (!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root,"force"))) return {};
        out.kind=XiaoyaCommand::Kind::Intro; out.force=true; return out;
    }
    if (!std::strcmp(cmd->valuestring,"volume")) {
        // {"type":"xiaoya","cmd":"volume","volume":40} — parent-set speaker volume.
        const auto* v=cJSON_GetObjectItemCaseSensitive(root,"volume");
        if (!cJSON_IsNumber(v) || v->valuedouble<0 || v->valuedouble>100 || v->valuedouble!=v->valueint) return {};
        out.kind=XiaoyaCommand::Kind::Volume; out.volume=static_cast<std::uint8_t>(v->valueint); return out;
    }
    if (!std::strcmp(cmd->valuestring,"glyph")) {
        out.kind=XiaoyaCommand::Kind::Glyph;
        if (cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root,"clear"))) {
            out.clear=true; return out;
        }
        const auto* png=cJSON_GetObjectItemCaseSensitive(root,"png");
        if (!cJSON_IsString(png)) return {};
        out.png=decode_glyph_png(png->valuestring);
        if (!out.png) return {};
        out.hold_ms=10000;
        if (const auto* hold=cJSON_GetObjectItemCaseSensitive(root,"hold_ms")) {
            if (!cJSON_IsNumber(hold) || !(hold->valuedouble>=1000 && hold->valuedouble<=60000) ||
                hold->valuedouble!=hold->valueint) return {};
            out.hold_ms=static_cast<std::uint32_t>(hold->valueint);
        }
        return out;
    }
    if (std::strcmp(cmd->valuestring,"form")) return {};
    auto form=[](const cJSON* v)->int {
        if (cJSON_IsNumber(v) && v->valuedouble>=1 && v->valuedouble<=4 && v->valuedouble==v->valueint) return v->valueint;
        if (cJSON_IsString(v) && std::strlen(v->valuestring)==2 && v->valuestring[0]=='f' && v->valuestring[1]>='1' && v->valuestring[1]<='4') return v->valuestring[1]-'0';
        return 0;
    };
    out.form=form(cJSON_GetObjectItemCaseSensitive(root,"form"));
    if (!out.form) return {};
    if (const auto* v=cJSON_GetObjectItemCaseSensitive(root,"from")) {
        out.from=form(v); if (!out.from) return {};
    }
    if (const auto* v=cJSON_GetObjectItemCaseSensitive(root,"animate")) {
        if (!cJSON_IsBool(v)) return {};
        out.animate=cJSON_IsTrue(v);
    }
    return out;
}
}
