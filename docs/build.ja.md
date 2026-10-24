[English](build.md) | [中文](build.zh-CN.md) | [日本語](build.ja.md)

[Path 1: firmware only](voice-backends.md#path-1-firmware-only)

# Web Flasher / 設定ページ

GitHub Releases に置かれたファームウェアをブラウザから書き込めます (Chrome / Edge):

- **書き込み**: <https://josssphzhou.github.io/groki-bot/>
- **BLE 設定**: <https://josssphzhou.github.io/groki-bot/settings.html> (Web Bluetooth、デスクトップ Chrome / Edge のみ)
- **Wi-Fi 設定**: デバイスを Wi-Fi に繋いだ後 `http://stackchan-XXXXXX.local/` (mDNS)
- **iOS / SoftAP 設定**: 本体ボタン (boards により方法が異なる、後述) で AP モードに入り、
  LCD に表示される Wi-Fi QR を iPhone Camera で読む → captive portal で設定ページが自動表示

タグ `vX.Y.Z` を push すると CI が 4 ボード分ビルドして Release を作り、
Pages サイトに反映されます。

## クイックスタート

Docker でのビルドを推奨します。ホストに ESP-IDF を入れる必要はありません。
先に Docker Desktop（または Docker デーモン）を起動してください。

### Docker（推奨）

イメージは `espressif/idf:v5.5.4` です。初回の pull は約 14 GB です。
公式イメージに Node は入っていません。Makefile がコンテナ内で Node.js 18+
を自動インストールします。成果物は `build-cores3/` です。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # M5Unified の 1 行修正を適用
make build-docker BOARD=cores3
```

`BOARD=` を `stopwatch` に置き換えられます。本版でビルドを検証済みなのは
`cores3` と `stopwatch` です。`atoms3r` / `atoms3` はまだ再ビルドしていません。以前の失敗原因
(顔アニメ資源が 1 MB の storage パーティションに載らない) は資源の削除で解消済みです。詳細は
[既知の問題 第 4 条](known_issues.md)。Docker 経路でも同じ `BOARD` を
渡す必要があり、対応する sdkconfig デフォルト列が読み込まれ、成果物は
`build-<board>/` に置かれます。

フラッシュは Docker では行いません。いまビルドしたファームを書くには:

```sh
make flash BOARD=cores3 PORT=/dev/ttyACM0
make monitor BOARD=cores3 PORT=/dev/ttyACM0
```

`make flash` にはホスト側の ESP-IDF が必要です（IDF 環境を読み込んでから
`idf.py flash`）。ホストに IDF が無い場合は、公開済みリリースをブラウザ
書き込みページで焼いてください:
<https://josssphzhou.github.io/groki-bot/>。
このページは GitHub Release を読むだけで、`build-cores3/` のファイルを
直接選べません。

### ホスト ESP-IDF

環境: ESP-IDF 5.5（5.5.4 で検証）。Espressif の手順で入れたあと、IDF
ソースディレクトリで `./install.sh esp32s3` を実行し、`source export.sh`
してください。Makefile の既定は `IDF_PATH=$(HOME)/esp-idf/5.5.4` です。
ビルド機の PATH に Node.js 18+ が必要です（CMake 設定時に `node` を探し、
`.avdsl` をバイトコードへコンパイルします。コンパイラは
`tools/avatar_dsl/` にあります）。システムの `python3` が 3.14 だと、
IDF 5.5 は対応する仮想環境を探し、未導入なら make はすぐ失敗します。

```sh
git clone https://github.com/JosssphZhou/groki-bot.git
cd groki-bot
git submodule update --init --recursive
tools/apply-m5-patches.sh                    # M5Unified の 1 行修正を適用
make set-target BOARD=cores3                 # 初回のみ (BOARD 別に build dir が分かれる)
make build     BOARD=cores3
make flash     BOARD=cores3 PORT=/dev/ttyACM0
make monitor   BOARD=cores3 PORT=/dev/ttyACM0
```

ホストの `make build BOARD=<略号>` の成果物は `build-<board>/` です。

CMake が `Could not find NODE_EXECUTABLE` と出した場合、今の環境
（Docker イメージを含む）の PATH に `node` がありません。ソース欠落では
ありません。

`tools/apply-m5-patches.sh` は upstream M5Unified の
`RTC_PowerHub_Class::setAlarmIRQ` で GCC 14 の `-Werror=maybe-uninitialized`
に引っかかる `buf` 初期化を当てるだけです。

OpenAI / Gemini の API キーはビルドに埋め込まず、BLE / Wi-Fi 設定 IF から
実行時に投入します (NVS に保存)。`sdkconfig.defaults.local` (gitignore 済み) で
コンパイル時デフォルトを与えることもできます。

spotpear128cam のリリース・Web 書き込み・OTA：タグ公開で `firmware-<tag>-spotpear128cam.zip` を添付し、Web では SpotPear ESP32-S3 1.28-CAM (小芽) を選択；端末は自分のボード用リリースのみ表示し `firmware/<tag>/spotpear128cam/stackchan_idf.bin` を取得。4 MiB × 2 OTA スロットで起動ロールバックと mark-valid は実装済み、旧版→新版の Wi-Fi OTA と故障イメージのロールバックは実機検証待ち。保護者の Grok Bot 経由更新は ticket 24 の今後の作業で、新しい保護者設定ページは追加しません。
