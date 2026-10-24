# SPDX-FileCopyrightText: 2026 Joseph Zhou
# SPDX-License-Identifier: BSL-1.0
file(READ "${INPUT}" DATA HEX)
string(REGEX REPLACE "(..)" "0x\\1," DATA "${DATA}")
file(WRITE "${OUTPUT}" "#pragma once\n#include <cstdint>\ninline constexpr std::uint8_t kFace[] = {${DATA}};\n")
