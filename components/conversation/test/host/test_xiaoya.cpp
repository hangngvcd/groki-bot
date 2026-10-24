// SPDX-License-Identifier: BSL-1.0
#undef NDEBUG
#include "conversation/xiaoya_control.hpp"
#include "conversation/control_dispatch.hpp"
#include <cassert>
#include <cstring>
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
    bool received=false;
    const char* json=R"({"type":"xiaoya","cmd":"form","form":"f4"})";
    parse_control(json,std::strlen(json),[](const HeadCommand&){ assert(false); },
        [&](const char* type,const cJSON* root) {
            assert(std::strcmp(type,"xiaoya")==0);
            const auto event=parse_xiaoya(root); assert(event && event->form==4); received=true;
        }, [](const char*) { assert(false); });
    assert(received);
}
