# 鈴鈴資源包（LLAP）

鈴鈴嘅形態圖片同本地語音（自我介紹、升級讚賞）放喺一個獨立資源包，燒錄喺 flash
`0xdb1000`（`model` partition + 0x1000）。換圖或者換聲唔使重燒韌體，亦唔使改 partition table。

* 冇燒資源包：部機用內置畫出嚟嘅鈴鈴（四個形態都有），自我介紹改為畫面文字。
* 某張圖缺少或者唔啱（唔係 240×240 PNG、CRC 唔啱）：嗰個表情自動用返內置畫法。
* 開機 log 會見到 `asset pack: N entries` 或者 `asset pack: none (fallback)`。

## Slot 名

| Slot | 內容 |
| --- | --- |
| `face.f<1-4>.<expr>` | 某形態某表情嘅臉（PNG，240×240，圓形以外留黑） |
| `face.<expr>` | 所有形態共用嘅表情（冇 `face.fN.<expr>` 時用） |
| `vo.intro.1` | 第一次開機自我介紹（PCM s16le mono，建議 16000 Hz） |
| `vo.evolve.f2` / `f3` / `f4` | 升到嗰個形態時嘅讚賞（跳級時只播最後一級） |

查找次序：`face.f<form>.<expr>` → `face.<expr>` → 內置畫法。

形態：1 小苗貓、2 小翼貓、3 寶石貓、4 星冠貓。
表情（`<expr>`）：`neutral happy sad angry doubt sleepy listening thinking excited curious confused surprised dizzy affection bored`。

注意：用靜態圖嘅表情冇眨眼同口型動畫（講嘢時嘴唔郁）；想保留動畫，就唔好放嗰個表情嘅圖。

## 格式（little-endian，同 `components/lingling_assets/assets.cpp` 一致）

```
header 16 B : "LLAP", u16 version=1, u16 count, u32 pack_size, u32 reserved=0
entry  52 B : char name[32] (NUL 補位), u32 offset, u32 size,
              u16 kind (1=PNG, 2=PCM s16le mono), u16 reserved=0,
              u32 sample_rate (PNG 填 0), u32 crc32 (zlib/IEEE)
blobs       : 4-byte 對齊
```

最大 `0x23F000`（2,355,200 B）。

## 製作步驟

```sh
# 1. 圖：用佔位圖，或者自己畫，命名 f<N>_<expr>.png（240×240）
python3 tools/lingling_assets/make_placeholders.py /tmp/ll-art
#    淨係換某幾張：將自己嘅 PNG 覆蓋 /tmp/ll-art/f3_happy.png 之類
#    所有形態共用：命名 <expr>.png，例如 happy.png

# 2. 聲：改 lines.json 嘅句子，再用 edge-tts 生成（要裝 edge-tts 同 ffmpeg）
python3 tools/lingling_assets/make_clips.py /tmp/ll-clips --python /path/to/python-with-edge-tts
#    或者自己錄：mono 16-bit WAV，檔名 vo.intro.1.wav 等

# 3. 打包（會檢查尺寸、格式同大小上限）
python3 tools/lingling_assets/build_pack.py /tmp/lingling-pack.bin --images /tmp/ll-art --clips /tmp/ll-clips
```

淨係要聲唔要圖：第 3 步唔加 `--images`。

## 燒錄

```sh
python -m esptool --chip esp32s3 -b 460800 write_flash 0xdb1000 /tmp/lingling-pack.bin
```

`idf.py flash` / `make flash` 只寫 `0xdb0000` 嘅 4 字節 `srmodels.bin`，唔會蓋到資源包；
但 `erase_flash` 會清走，之後要重燒。換包後重開部機即可。

## 測試

```sh
python3 -m unittest tools/lingling_assets/test_build_pack.py
cd components/lingling_assets/test/host && cmake -S . -B build -G Ninja && cmake --build build && ctest --test-dir build
```
