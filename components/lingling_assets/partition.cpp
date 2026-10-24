// SPDX-License-Identifier: BSL-1.0
#include "lingling/assets.hpp"
#include <esp_partition.h>
#include <esp_log.h>
namespace lingling {
const AssetPack& assets() {
    static const AssetPack pack=[] {
        AssetPack result;
        const auto* part=esp_partition_find_first(ESP_PARTITION_TYPE_DATA,ESP_PARTITION_SUBTYPE_ANY,"model");
        const void* data=nullptr;
        static esp_partition_mmap_handle_t handle;
        if (part && part->size>0x1000 && esp_partition_mmap(part,0x1000,part->size-0x1000,
                ESP_PARTITION_MMAP_DATA,&data,&handle)==ESP_OK &&
                result.parse(static_cast<const std::uint8_t*>(data),part->size-0x1000)) {
            ESP_LOGI("lingling","asset pack: %u entries",unsigned(result.count()));
        } else ESP_LOGI("lingling","asset pack: none (fallback)");
        return result;
    }();
    return pack;
}
}
