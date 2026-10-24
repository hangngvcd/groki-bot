// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0
#pragma once

#include <memory>
#include <string>
#include <string_view>
#include "cJSON.h"

namespace stackchan::wifi_config::release_ota {

// Keep only releases with an image for this board. Legacy ZIPs are CoreS3.
inline std::string filter_versions_for_board(std::string_view versions_json, const char* slug)
{
    if (versions_json.find('\0') != std::string_view::npos) return "[]";
    if (!slug || !*slug) return "[]";
    const std::string input(versions_json);
    using Json = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;
    Json root(cJSON_ParseWithLengthOpts(input.c_str(), input.size() + 1, nullptr, true), cJSON_Delete);
    if (!cJSON_IsArray(root.get())) return "[]";
    Json result(cJSON_CreateArray(), cJSON_Delete);
    if (!result) return "[]";
    const cJSON* entry = nullptr;
    cJSON_ArrayForEach(entry, root.get()) {
        if (!cJSON_IsObject(entry)) continue;
        const auto* boards = cJSON_GetObjectItemCaseSensitive(entry, "boards");
        const auto* image = cJSON_IsObject(boards)
            ? cJSON_GetObjectItemCaseSensitive(boards, slug)
            : (!boards && std::string_view(slug) == "cores3"
                ? cJSON_GetObjectItemCaseSensitive(entry, "zip") : nullptr);
        if (!cJSON_IsString(image) || !image->valuestring[0]) continue;
        auto* copy = cJSON_Duplicate(entry, true);
        if (!copy || !cJSON_AddItemToArray(result.get(), copy)) {
            cJSON_Delete(copy);
            return "[]";
        }
    }
    char* printed = cJSON_PrintUnformatted(result.get());
    if (!printed) return "[]";
    std::string out(printed);
    cJSON_free(printed);
    return out;
}

} // namespace stackchan::wifi_config::release_ota
