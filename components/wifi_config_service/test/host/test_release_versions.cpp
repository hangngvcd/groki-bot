// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-License-Identifier: BSL-1.0
#include "release_versions.hpp"
#include <cassert>
#include <iostream>

using stackchan::wifi_config::release_ota::filter_versions_for_board;

int main()
{
    const char* input = R"([{"tag":"v3","boards":{"spotpear128cam":"spot.zip","cores3":"core.zip"},"extra":42},{"tag":"v2","boards":{"cores3":"core.zip"}},{"tag":"v1","zip":"legacy.zip"}])";
    assert(filter_versions_for_board(input, "spotpear128cam") == R"([{"tag":"v3","boards":{"spotpear128cam":"spot.zip","cores3":"core.zip"},"extra":42}])");
    assert(filter_versions_for_board(input, "atoms3") == "[]");
    assert(filter_versions_for_board(input, nullptr) == "[]");
    assert(filter_versions_for_board(input, "") == "[]");
    auto core = filter_versions_for_board(input, "cores3");
    cJSON* parsed = cJSON_Parse(core.c_str());
    assert(cJSON_GetArraySize(parsed) == 3);
    cJSON_Delete(parsed);
    for (const char* bad : {"", "null", "{}", "[", "[] trailing"}) {
        assert(filter_versions_for_board(bad, "spotpear128cam") == "[]");
    }
    std::string embedded_null = input;
    embedded_null.push_back('\0');
    embedded_null += "garbage";
    assert(filter_versions_for_board(embedded_null, "spotpear128cam") == "[]");
    const char* malformed = R"([null,1,{}, {"boards":null,"zip":"legacy.zip"},{"boards":{},"zip":"legacy.zip"},{"boards":{"spotpear128cam":null}},{"boards":{"spotpear128cam":42}},{"boards":{"spotpear128cam":""}}])";
    assert(filter_versions_for_board(malformed, "spotpear128cam") == "[]");
    assert(filter_versions_for_board(malformed, "cores3") == "[]");
    assert(filter_versions_for_board(R"([{"zip":"legacy.zip"}])", "cores3") == R"([{"zip":"legacy.zip"}])");
    assert(filter_versions_for_board(R"([{"zip":"legacy.zip"}])", "spotpear128cam") == "[]");
    std::cout << "release versions tests passed\n";
}
