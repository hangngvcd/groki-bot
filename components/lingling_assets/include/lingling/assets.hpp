// SPDX-License-Identifier: BSL-1.0
#pragma once
#include <cstdint>
#include <cstddef>
#include <span>
#include <string>
#include <string_view>
#include <optional>
#include <vector>
namespace lingling {
// The specified fields occupy 52 bytes (including the trailing CRC32).
inline constexpr std::size_t kEntrySize = 52;
struct Entry {
    std::string_view name;
    std::span<const std::uint8_t> data;
    std::uint16_t kind;
    std::uint32_t sample_rate;
    std::uint32_t crc32;
};
class AssetPack {
public:
    // Borrows immutable storage, which must outlive this pack and returned entries.
    // Validation performs no allocations; malformed tables cannot exhaust RAM.
    bool parse(const std::uint8_t* data, std::size_t len);
    std::optional<Entry> find(std::string_view name) const;
    bool verify(const Entry& entry) const;
    std::size_t count() const { return count_; }
private:
    const std::uint8_t* data_{nullptr};
    std::size_t count_{0};
};
std::string face_slot_name(std::uint8_t form, std::string_view expression);
std::optional<Entry> find_face(const AssetPack&, std::uint8_t form, std::string_view expression);
struct EvolutionStep { std::uint8_t form; std::uint32_t at_ms; };
std::vector<EvolutionStep> plan_evolution(std::uint8_t from, std::uint8_t to);
// Mapping lives for the process lifetime. Call after flash/NVS initialization.
const AssetPack& assets();
}
