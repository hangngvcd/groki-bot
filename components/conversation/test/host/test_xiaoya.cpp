// SPDX-License-Identifier: BSL-1.0
#undef NDEBUG
#include "conversation/xiaoya_control.hpp"
#include "conversation/control_dispatch.hpp"
#include <cassert>
#include <cstring>
#include <string>
using namespace stackchan::conversation;
int main() {
    auto parse=[](const char* json) {
        auto* root=cJSON_Parse(json); assert(root);
        auto cmd=parse_xiaoya(root); cJSON_Delete(root); return cmd;
    };
    auto cmd=parse(R"({"cmd":"form","form":3,"from":"f1","animate":true})");
    assert(cmd && cmd->form==3 && cmd->from==1 && cmd->animate);
    for (auto json : {R"({"cmd":"form","form":0})", R"({"cmd":"form","form":5})",
        R"({"cmd":"form","form":2.5})",R"({"cmd":"form","form":"f0"})",
        R"({"cmd":"form","form":3,"from":null})",R"({"cmd":"form","form":3,"animate":"true"})",
        R"({"cmd":"intro","force":false})", R"({"cmd":"other"})"}) assert(!parse(json));
    cmd=parse(R"({"cmd":"intro","force":true})");
    assert(cmd && cmd->kind==XiaoyaCommand::Kind::Intro && cmd->force);
    cmd=parse(R"({"cmd":"volume","volume":40})");
    assert(cmd && cmd->kind==XiaoyaCommand::Kind::Volume && cmd->volume==40);
    cmd=parse(R"({"cmd":"volume","volume":0})");
    assert(cmd && cmd->kind==XiaoyaCommand::Kind::Volume && cmd->volume==0);
    cmd=parse(R"({"cmd":"volume","volume":100})");
    assert(cmd && cmd->volume==100);
    for (auto json : {R"({"cmd":"volume"})", R"({"cmd":"volume","volume":101})",
        R"({"cmd":"volume","volume":-1})", R"({"cmd":"volume","volume":40.5})",
        R"({"cmd":"volume","volume":"40"})"}) assert(!parse(json));
    auto encode=[](const std::vector<std::uint8_t>& bytes) {
        constexpr char alphabet[]="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        std::string result;
        for (std::size_t i=0; i<bytes.size(); i+=3) {
            const unsigned a=bytes[i], b=i+1<bytes.size() ? bytes[i+1] : 0,
                           c=i+2<bytes.size() ? bytes[i+2] : 0;
            result+=alphabet[a>>2]; result+=alphabet[((a&3)<<4)|(b>>4)];
            result+=i+1<bytes.size() ? alphabet[((b&15)<<2)|(c>>6)] : '=';
            result+=i+2<bytes.size() ? alphabet[c&63] : '=';
        }
        return result;
    };
    std::vector<std::uint8_t> png{137,80,78,71,13,10,26,10,0,0,0,13,'I','H','D','R',
                                 0,0,0,240,0,0,0,240,4,0,0,0,0,0,0,0,0};
    auto glyph=[&](const std::string& base64, const std::string& extra="") {
        return parse(("{\"cmd\":\"glyph\",\"png\":\""+base64+"\""+extra+"}").c_str());
    };
    auto encoded=encode(png);
    cmd=glyph(encoded,",\"hold_ms\":15000");
    assert(cmd && cmd->kind==XiaoyaCommand::Kind::Glyph && *cmd->png==png && cmd->hold_ms==15000);
    cmd=glyph(encoded); assert(cmd && cmd->hold_ms==10000 && !cmd->clear);
    cmd=parse(R"({"cmd":"glyph","clear":true})");
    assert(cmd && cmd->kind==XiaoyaCommand::Kind::Glyph && cmd->clear && !cmd->png);
    for (const auto& bad : {std::string(""), std::string("AAAA!AAA"), std::string("AAA-"),
            std::string("AAA_"), std::string("AA A"), std::string("A=AA"),
            std::string("AA=A"), std::string("AA==AAAA"), std::string("AAAA="),
            std::string(32772,'A')}) assert(!glyph(bad));
    // Exercise malformed padding on an otherwise valid PNG prefix, including unused bits.
    for (const auto* tail : {"A===", "AA=A", "AA==AAAA", "AB==", "AAB="})
        assert(!glyph(encoded+tail));
    for (const auto* hold : {"999", "60001", "1000.5", "\"10000\"", "null", "true"})
        assert(!glyph(encoded,std::string(",\"hold_ms\":")+hold));
    assert(glyph(encoded,",\"hold_ms\":1000") && glyph(encoded,",\"hold_ms\":60000"));
    assert(!parse(R"({"cmd":"glyph"})") && !parse(R"({"cmd":"glyph","png":12})"));
    png[19]=239; assert(!glyph(encode(png))); png[19]=240;
    png[23]=239; assert(!glyph(encode(png))); png[23]=240;
    png[0]=0; assert(!glyph(encode(png))); png[0]=137;
    png.resize(24576); assert(glyph(encode(png)));
    png.push_back(0); assert(!glyph(encode(png)));
    // Both legal padding lengths decode to the original bytes.
    png.resize(34); cmd=glyph(encode(png)); assert(cmd && *cmd->png==png);
    png.resize(35); cmd=glyph(encode(png)); assert(cmd && *cmd->png==png);
    bool received=false;
    const char* json=R"({"type":"xiaoya","cmd":"form","form":"f4"})";
    parse_control(json,std::strlen(json),[](const HeadCommand&){ assert(false); },
        [&](const char* type,const cJSON* root) {
            assert(std::strcmp(type,"xiaoya")==0);
            const auto event=parse_xiaoya(root); assert(event && event->form==4); received=true;
        }, [](const char*) { assert(false); });
    assert(received);
}
