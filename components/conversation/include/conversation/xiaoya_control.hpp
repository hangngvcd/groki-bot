// SPDX-License-Identifier: BSL-1.0
#pragma once
#include "conversation/xiaoya_command.hpp"
#include <cstring>
#include <optional>
#include <cJSON.h>
namespace stackchan::conversation {
inline std::optional<XiaoyaCommand> parse_xiaoya(const cJSON* root) {
    const auto* cmd=cJSON_GetObjectItemCaseSensitive(root,"cmd");
    if (!cJSON_IsString(cmd)) return {};
    XiaoyaCommand out;
    if (!std::strcmp(cmd->valuestring,"intro")) {
        if (!cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root,"force"))) return {};
        out.kind=XiaoyaCommand::Kind::Intro; out.force=true; return out;
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
