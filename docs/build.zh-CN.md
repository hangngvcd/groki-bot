[English](build.md) | [中文](build.zh-CN.md) | [日本語](build.ja.md)

[用法 1：只刷固件](voice-backends.zh-CN.md#用法-1只刷固件)

# 从源码构建

只有想修改固件时才需要这一节。只想用机器人，按上面第 1 条用刷机页刷已发布的版本即可。

推荐用 Docker 编译，本机不必安装 ESP-IDF。先启动 Docker Desktop（或本机 Docker 守护进程）。

### Docker（推荐）

镜像是 `espressif/idf:v5.5.4`。第一次拉取约 14 GB。官方镜像里没有 Node，Makefile 会在容器内自动安装 Node.js 18+。产物在 `build-cores3/`。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # 给 M5Unified 打一行补丁
make build-docker BOARD=cores3
```

`BOARD=` 可换成 `stopwatch`。本版已验证能编译的是 `cores3` 与 `stopwatch`。`atoms3r` / `atoms3` 本版还没有重新编译验证：它们以前编不过，是因为脸部动画资源超出 1 MB 存储分区，这批资源已经删除，见[已知问题第 4 条](known_issues.md)。Docker 路径必须显式传入同样的 `BOARD`，才会加载对应的 sdkconfig 默认配置，产物在 `build-<board>/`。

刷机不走 Docker。写入这次编出的固件，在本机执行（串口换成你的，见[下载模式](troubleshooting.zh-CN.md#下载模式)）：

```sh
make flash BOARD=cores3 PORT=/dev/cu.usbmodem1101     # macOS 示例
make monitor BOARD=cores3 PORT=/dev/cu.usbmodem1101
```

`make flash` 需要本机已安装 ESP-IDF（会加载 IDF 环境再调用 `idf.py flash`）。没有本机 IDF 时，用刷机页刷仓库已发布的版本：<https://josssphzhou.github.io/groki-bot/>。该页面读取 GitHub Release，不能直接选 `build-cores3/` 里的文件。

### 本机 ESP-IDF

环境：ESP-IDF 5.5（按 5.5.4 验证）。按 Espressif 文档安装，在 IDF 源码目录执行 `./install.sh esp32s3`，再 `source export.sh`。Makefile 默认 `IDF_PATH=$(HOME)/esp-idf/5.5.4`。编译机的 `PATH` 里需要有 Node.js 18+（CMake 配置阶段就会找 `node`，用来把 `.avdsl` 编成字节码；编译器脚本在 `tools/avatar_dsl/`）。系统 `python3` 若是 3.14，IDF 5.5 会去找对应的虚拟环境，未安装时 `make` 会直接失败。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # 给 M5Unified 打一行补丁
make set-target BOARD=cores3                 # 仅首次（各 BOARD 使用独立 build 目录）
make build     BOARD=cores3
make flash     BOARD=cores3 PORT=/dev/cu.usbmodem1101    # Linux：/dev/ttyACM0
make monitor   BOARD=cores3 PORT=/dev/cu.usbmodem1101
```

本机 `make build BOARD=<代号>` 的产物在 `build-<board>/`。

CMake 配置时若报 `Could not find NODE_EXECUTABLE`，说明当前环境（包括 Docker 镜像）的 `PATH` 里没有 `node`，不是源码缺文件。

`tools/apply-m5-patches.sh` 只修正上游 M5Unified 里 `RTC_PowerHub_Class::setAlarmIRQ` 的 `buf` 未初始化，避免 GCC 14 报 `-Werror=maybe-uninitialized`。

OpenAI / Gemini 的 API 密钥不编进固件，经 BLE / Wi-Fi 设置页在运行时写入 NVS。也可以在已被 gitignore 的 `sdkconfig.defaults.local` 里提供编译期默认值。

## 刷机页与设置页

用浏览器写入已发布固件（Chrome / Edge）：

- **刷机**：<https://josssphzhou.github.io/groki-bot/>
- **蓝牙设置**：<https://josssphzhou.github.io/groki-bot/settings.html>（Web Bluetooth，仅桌面版 Chrome / Edge）
- **Wi-Fi 设置**：设备连上 Wi-Fi 后访问 `http://stackchan-XXXXXX.local/`（mDNS）
- **iOS / SoftAP**：进入 AP 模式后，用 iPhone 相机扫 LCD 上的 Wi-Fi 二维码，captive portal 会打开设置页。CoreS3 / StopWatch 点屏幕右上角打开设备界面，在操作页选「AP 模式」；AtomS3R / AtomS3 短按 BtnA 打开状态层，长按循环切换 `operation_mode`。

推送标签 `vX.Y.Z` 后，CI（启用 GitHub Actions 时）为四块板构建并挂到 Release，Pages 站点随后更新。刷机页源文件在 `docs/index.html`，蓝牙设置页源文件在 `tools/settings.html`。

spotpear128cam 发布、网页烧录与 OTA：标签发布附带 `firmware-<tag>-spotpear128cam.zip`，网页选择 SpotPear ESP32-S3 1.28-CAM (小芽)；设备仅列出本板固件并下载 `firmware/<tag>/spotpear128cam/stackchan_idf.bin`；双 4 MiB OTA 槽，已启用启动回滚并调用 mark-valid，Wi-Fi 新旧版本升级和故障镜像回滚仍待真机验证；家长通过 Grok Bot 更新属 ticket 24 后续工作，不新增家长设置页。
