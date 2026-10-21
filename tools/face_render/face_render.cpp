// SPDX-FileCopyrightText: 2026 Joseph Zhou
// SPDX-FileCopyrightText: 2026 Kenta IDA <fuga@fugafuga.org> (canvas adapter)
// SPDX-License-Identifier: BSL-1.0
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "avatar_vm/vm.hpp"
#include "avatar_vm/design_scale.hpp"
#include "aora_face.hpp"
#include "face.hpp"
using namespace stackchan;
using namespace stackchan::avatar;
class FrameCanvas final : public Canvas {
public:
    explicit FrameCanvas(M5Canvas& c, bool round) noexcept : c_{c}, round_{round} {}
    std::int32_t width() const override { return c_.width(); }
    std::int32_t height() const override { return c_.height(); }
    void fillScreen(std::uint16_t color) override { c_.fillScreen(color); }
    void fillRect(std::int32_t x, std::int32_t y, std::int32_t w, std::int32_t h,
                  std::uint16_t color) override
    {
        c_.fillRect(x, y, w, h, color);
    }
    void fillCircle(std::int32_t x, std::int32_t y, std::int32_t r, std::uint16_t color) override
    {
        c_.fillCircle(x, y, r, color);
    }
    void fillTriangle(std::int32_t x0, std::int32_t y0, std::int32_t x1, std::int32_t y1,
                      std::int32_t x2, std::int32_t y2, std::uint16_t color) override
    {
        c_.fillTriangle(x0, y0, x1, y1, x2, y2, color);
    }
    void begin_group(std::int32_t, std::int32_t, std::int32_t, std::int32_t) override {}
    void end_group() override {}
    void begin_frame(std::uint16_t bg) override { c_.fillScreen(bg); }
    void end_frame() override {}
    void request_full_repaint() override {}

    bool is_circular() const noexcept override { return round_; }
private:
    M5Canvas& c_;
    bool round_;
};

namespace {
constexpr std::array<const char*, 15> names = {
    "neutral", "happy", "sad", "angry", "doubt", "sleepy", "listening",
    "thinking", "excited", "curious", "confused", "surprised", "dizzy", "affection", "bored"};
struct Frame {
    int w, h;
    std::vector<std::uint16_t> pixels;
    std::array<float, app::aora::kOutFloats> ring;
};
void require(bool ok, const std::string& message)
{
    if (!ok) throw std::runtime_error(message);
}
Frame render(bool round, int expression, float mouth, std::uint32_t time, bool sprout = true)
{
    M5Canvas buffer;
    const int w = round ? 240 : 320;
    buffer.createSprite(w, 240);
    FrameCanvas canvas(buffer, round);
    DrawContext ctx;
    ctx.expression = ctx.expression_from = ctx.expression_hold_to = ctx.expression_hold2_to =
        static_cast<Expression>(expression);
    ctx.expression_blend = ctx.expression_hold_blend = ctx.expression_hold2_blend = 1;
    ctx.eye_open_ratio = 1;
    ctx.mouth_open_ratio = mouth;
    ctx.now_ms = time;
    ctx.breath = std::sin(time * 0.001f);
    ctx.sprout_visible = round && sprout;
    ctx.layout_scale = round ? avatar_vm::round_face_layout_scale(canvas) : 0.0f;
    Frame frame{w, 240, {}, {}};
    app::aora::compose(ctx, time, frame.ring.data());
    if (round) app::aora::fit_to_canvas(frame.ring.data(), avatar_vm::round_face_layout_scale(canvas),
                                       w * 0.5f, 120.0f);
    ctx.aora_ring = frame.ring.data();
    auto bc = avatar_vm::decode(kFace);
    require(bool(bc), "bytecode decode");
    avatar_vm::Vm vm;
    auto result = vm.run(*bc, canvas, ctx, FaceTuning{});
    require(bool(result), result ? "" : avatar_vm::to_string(result.error()));
    frame.pixels.assign(buffer.getBuffer(), buffer.getBuffer() + w * 240);
    return frame;
}
// FNV-1a over RGB565 in explicit little-endian byte order.
std::uint64_t hash(const Frame& frame)
{
    std::uint64_t h = 14695981039346656037ull;
    for (auto p : frame.pixels) {
        h = (h ^ (p & 255)) * 1099511628211ull;
        h = (h ^ (p >> 8)) * 1099511628211ull;
    }
    return h;
}
using Bytes = std::vector<std::uint8_t>;
void be32(Bytes& out, std::uint32_t n)
{
    for (int s = 24; s >= 0; s -= 8) out.push_back(n >> s);
}
std::uint32_t crc(const Bytes& b)
{
    std::uint32_t c = ~0u;
    for (auto v : b) {
        c ^= v;
        for (int i = 0; i < 8; ++i) c = (c >> 1) ^ ((c & 1) ? 0xedb88320u : 0);
    }
    return ~c;
}
void chunk(Bytes& png, const char* type, const Bytes& data)
{
    be32(png, data.size());
    Bytes body(type, type + 4);
    body.insert(body.end(), data.begin(), data.end());
    png.insert(png.end(), body.begin(), body.end());
    be32(png, crc(body));
}
void png(const std::filesystem::path& path, const Frame& frame, bool mask)
{
    Bytes raw;
    for (int y = 0; y < frame.h; ++y) {
        raw.push_back(0); // PNG filter: none
        for (int x = 0; x < frame.w; ++x) {
            if (mask && std::hypot(x % 240 - 119.5, y % 240 - 119.5) > 120) {
                raw.insert(raw.end(), {32, 32, 32});
            } else {
                auto p = frame.pixels[y * frame.w + x];
                raw.push_back(((p >> 11) & 31) * 255 / 31);
                raw.push_back(((p >> 5) & 63) * 255 / 63);
                raw.push_back((p & 31) * 255 / 31);
            }
        }
    }
    Bytes z{0x78, 0x01};
    for (std::size_t pos = 0; pos < raw.size();) {
        auto n = std::min<std::size_t>(65535, raw.size() - pos);
        z.push_back(pos + n == raw.size() ? 1 : 0);
        for (auto value : {n, n ^ 65535}) {
            z.push_back(value & 255);
            z.push_back(value >> 8);
        }
        z.insert(z.end(), raw.begin() + pos, raw.begin() + pos + n);
        pos += n;
    }
    std::uint32_t a = 1, b = 0;
    for (auto v : raw) { a = (a + v) % 65521; b = (b + a) % 65521; }
    be32(z, (b << 16) | a);
    Bytes out{137,80,78,71,13,10,26,10}, ihdr;
    be32(ihdr, frame.w); be32(ihdr, frame.h);
    ihdr.insert(ihdr.end(), {8,2,0,0,0});
    chunk(out, "IHDR", ihdr); chunk(out, "IDAT", z); chunk(out, "IEND", {});
    std::ofstream file(path, std::ios::binary);
    file.write(reinterpret_cast<const char*>(out.data()), out.size());
    require(bool(file), "writing " + path.string());
}
constexpr std::array<int, 5> states{0, 6, 7, 0, 5};
constexpr std::array<const char*, 5> state_names{"companion", "listening", "thinking", "speaking", "resting"};
double green_x(const Frame& frame)
{
    double sum = 0;
    int count = 0;
    for (int y = 0; y < frame.h; ++y) for (int x = 0; x < frame.w; ++x) {
        if (frame.pixels[y * frame.w + x] != 0x7e4a) continue;
        require(y < 120, "sprout below body centre");
        sum += x; ++count;
    }
    require(count > 0, "missing sprout");
    return sum / count;
}
double sprout_tip_x(const Frame& frame)
{
    for (int y = 0; y < frame.h; ++y) {
        double sum = 0;
        int count = 0;
        for (int x = 0; x < frame.w; ++x) {
            if (frame.pixels[y * frame.w + x] == 0x7e4a) { sum += x; ++count; }
        }
        if (count) return sum / count;
    }
    throw std::runtime_error("missing sprout tip");
}
void test(const std::string& name)
{
    if (name == "safe_area") {
        // Includes the 12000..13200 eye orbit window as well as hop/zzz/heart phases.
        constexpr std::array<std::uint32_t, 30> times{
            6000, 6850, 7700, 8550, 9400, 10250, 11100,
            12000, 12150, 12400, 12700, 13000, 13199, 14500,
            // zzz wrap boundaries (2800 ms), heart peak/wrap (2400 ms),
            // happy hop peaks (700 + 1400n), excited peaks (520 + 1040n).
            0, 699, 700, 701, 519, 520, 521, 2399, 2400, 2799, 2800,
            7299, 7300, 7799, 7800, 8399};
        for (int e = 0; e < 15; ++e) for (float mouth : {0.0f, 0.7f}) for (auto t : times) {
            auto f = render(true, e, mouth, t);
            for (int y = 0; y < 240; ++y) for (int x = 0; x < 240; ++x) {
                if (f.pixels[y * 240 + x] != 0 && std::hypot(x - 119.5, y - 119.5) > 120 - 4)
                    throw std::runtime_error("safe area: expression " + std::to_string(e) +
                                             " time " + std::to_string(t));
            }
            for (std::size_t i = 0; i < f.ring.size(); i += 2)
                require(std::hypot(f.ring[i] - 120, f.ring[i+1] - 120) < 110, "eye outside body region");
        }
    } else if (name == "sprout") {
        for (std::size_t i = 0; i < states.size(); ++i)
            green_x(render(true, states[i], i == 3 ? 0.7f : 0, 6000));
        require(std::abs(sprout_tip_x(render(true, 0, 0, 6800)) -
                         sprout_tip_x(render(true, 0, 0, 8500))) > 0.5, "sprout does not sway");
    } else if (name == "unchanged") {
        for (int e = 0; e < 15; ++e) for (float mouth : {0.0f, 0.7f}) {
            auto f = render(false, e, mouth, 6000);
            require(std::count(f.pixels.begin(), f.pixels.end(), 0x7e4a) == 0, "CoreS3 sprout");
            require(f.pixels == render(false, e, mouth, 6000, false).pixels, "CoreS3 default path changed");
        }
        // Original grok_face.avdsl at 281f14f711f9301bdf6d05d0abaa25f97af02ba0,
        // captured before the sprout edit with -DFACE_SOURCE pointing to git show output.
        constexpr std::uint64_t golden = 14102123984359381969ull;
        require(hash(render(false, 0, 0, 6000)) == golden, "CoreS3 golden changed");
    } else throw std::runtime_error("unknown test");
}
} // namespace
int main(int argc, char** argv)
{
    try {
        if (argc == 2 && std::string(argv[1]) == "--hash") {
            std::cout << hash(render(false, 0, 0, 6000)) << '\n'; return 0;
        }
        if (argc == 3 && std::string(argv[1]) == "--test") { test(argv[2]); return 0; }
        require(argc == 2, "usage: face_render <out_dir>");
        std::filesystem::path dir(argv[1]);
        std::filesystem::create_directories(dir);
        std::vector<Frame> frames;
        for (int e = 0; e < 15; ++e) {
            auto f = render(true, e, 0, 6000);
            auto file = "spotpear128cam_" + std::string(e < 10 ? "0" : "") +
                std::to_string(e) + "_" + names[e] + ".png";
            png(dir / file, f, true);
            frames.push_back(std::move(f));
        }
        for (std::size_t i = 0; i < states.size(); ++i) {
            auto f = render(true, states[i], i == 3 ? 0.7f : 0, 6000);
            png(dir / ("spotpear128cam_state_" + std::string(state_names[i]) + ".png"), f, true);
            frames.push_back(std::move(f));
        }
        Frame sheet{5 * 240, 4 * 240, std::vector<std::uint16_t>(1200 * 960), {}};
        for (std::size_t i = 0; i < frames.size(); ++i)
            for (int y = 0; y < 240; ++y) for (int x = 0; x < 240; ++x) {
                auto p = frames[i].pixels[y * 240 + x];
                sheet.pixels[((i / 5) * 240 + y) * sheet.w + (i % 5) * 240 + x] = p;
            }
        png(dir / "spotpear128cam_sheet.png", sheet, true);
        png(dir / "cores3_00_neutral.png", render(false, 0, 0, 6000), false);
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
