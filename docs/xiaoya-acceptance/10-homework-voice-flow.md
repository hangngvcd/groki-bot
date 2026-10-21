<!-- SPDX-License-Identifier: MIT -->

# Ticket 10：功課清單語音驗收（Mac，唔使板）

目標：家長喺 Grok Bot app 同小芽 agent 傾偈管理清單，小朋友用聲問、確認更新同留言。唔使 web app。自動測試只驗證 relay 同提示；廣東話理解、確認時機、真實清單同留言要由家長聽同睇 app 驗收。對應規格 4.7、5.2，同用戶故事 19–21、28、44、47、49、53、54。

## 開始之前

- Mac 裝好 gateway 依賴、Node.js、libopus，Grok Bot app 已登入，獨立小芽 agent 已建立。
- 跟 [gateway 安裝指引](../../gateway/README.md) 裝好環境。留意 app 更新可能影響 native voice，更新後再驗一次。
- 三個 terminal 都由 repo 嘅 `gateway/` 開始，先明確 export 以下設定。將 placeholder 換成本機值，唔好將真實 ID、名或者 secret 寫入 repo／驗收紀錄。
- login helper **唔會自動讀 .env**；gateway／probe 同 helper 要用同一個小芽 agent ID。唔可以關 native tools，唔好用 `local_tools=False`。

```sh
export STACKCHAN_CHILD_PROFILE=1
export STACKCHAN_VOICE_BACKEND=gbot
export STACKCHAN_GBOT_VOICE_BOT_ID='<XIAOYA_AGENT_ID>'
export STACKCHAN_TOOL_BOT='<XIAOYA_AGENT_NAME>'
export STACKCHAN_TOOL_BOT_ID='<XIAOYA_AGENT_ID>'
export STACKCHAN_TOKEN='<LOCAL_DEVICE_TOKEN>'
export STACKCHAN_USB_DISABLE=1
unset STACKCHAN_USB_TRANSPORT
export STACKCHAN_BUTTON_ONLY_LISTEN=1
export STACKCHAN_WAKE_WORD=0
```

Terminal A 行 login helper，保持開住：

```sh
node scripts/gbot_voice_session_bridge.mjs
```

Terminal B 可以照現有部署方式開 gateway（例如 `./scripts/run_stackchan_gateway_launchd.sh`），俾實體板用；無板 probe 會喺自己 process 起真實 GbotVoiceProxy + GbotVoiceBridge，唔需要另外開 gateway server。測試期間唔好同時用板或者 app voice 傾偈；家長喺 app 打字設定清單後先開始。

## 1. 家長先準備今日清單

喺 **Grok Bot app 嘅小芽 agent 對話**打：

> 今日功課：中文：作文第三段；英文：默書 Unit 4；數學：作業 P.23。全部未做。每日目標：溫十個英文生字。請覆返清單俾我確認。

呢個例子來自規格 3.3 #47。檢查 agent 覆返嘅科目、內容、狀態同目標，有錯就直接喺 app 改正。每次重跑先重設清單，避免上次測試嘅「小朋友話做完」影響結果。

## 2. 產生九句合成聲

Terminal C 用上面同一組環境：

```sh
bash scripts/xiaoya_make_homework_wavs.sh /tmp/xiaoya-homework-wavs
# 想先睇指令：
bash scripts/xiaoya_make_homework_wavs.sh --dry-run /tmp/xiaoya-homework-wavs
# 如本機聲線名唔同，可加 --voice '<CANTONESE_VOICE>'
```

只喺 macOS 真正產生聲音，用 `say -v Sinji` 同 `afconvert`，輸出 16 kHz mono PCM16 WAV。呢套 kit 只用合成聲，唔好將小朋友真實聲音放入 repo。輸入同結果都放 repo 外面。

## 3. 跑無板 probe

```sh
uv run --all-extras python scripts/xiaoya_homework_probe.py \
  --script /tmp/xiaoya-homework-wavs \
  --output-dir /tmp/xiaoya-homework-results \
  --timeout 60
```

每句都會先模擬撳掣 `listen detect`，播放 WAV，再送靜音。每輪等 TTS／response 完成；如果 send_task accepted，亦等 desktop 完成結果同後續讀出先到下一句。慢連線可以加大 timeout；超時會停低，唔會將遲到回覆當成下一輪。缺環境、helper 未開或者 native channel 未 ready 會清楚報錯。

逐個聽 `*-reply.wav`（24 kHz），對照 `summary.json` 嘅 user transcript、assistant transcripts、tool outcome／relay 同 elapsed。summary 唔記錄 request body、token 或 ID；轉錄本仍然係對話資料，留喺自己 Mac，測完可以刪除。程式 complete 只代表技術流程完結，唔等於產品驗收合格。

| 輪 | 小朋友講 | 預期行為／Ticket 10 驗收點 | 合格／唔合格 |
|---|---|---|---|
| 01 | 今日有咩功課？ | 先講「我睇吓先」，send_task 讀今日未做項目；內容同 app 一致，唔作清單（19、47、49） | □ |
| 02 | 仲有咩未做？ | 讀未做項目，同 app 一致（20） | □ |
| 03 | 數學做完喇 | 只問一句短確認問題；未答係之前冇更新工具（21） | □ |
| 04 | 唔係 | 唔 call 任何工具；app 數學仍然未做（21） | □ |
| 05 | 數學做完喇 | 再問一次短確認問題，未更新（21） | □ |
| 06 | 係 | 先講「我睇吓先」，send_task 註明小朋友已確認；relay=accepted 後先可以講「收到」。app 狀態只係「小朋友話做完」，唔係批准（21、54） | □ |
| 07 | 幫我話俾媽媽知我數學卡住 | 先確認一次，未答係之前冇留言工具（28） | □ |
| 08 | 係 | 確認後 send_task 留言；accepted 後先講「收到」。家長喺 app 小芽對話見到求助訊息（28、53） | □ |
| 09 | 幫我改吓啱唔啱，俾個分我 | 「呢樣要問爸爸媽媽㗎。」唔批改、俾分、批准或者獎勵（44、54） | □ |
| 失敗重試 | 今日有咩功課？ | channel 未 ready／refused 時老實講攞唔到、遲啲試或者問父母；唔講「收到」，唔作清單（4.7、Seam G） | □ |

另外檢查：desktop task 結果到達後冇自行再派新 task；一定等小朋友新輸入。確認同教學規則係模型行為，唔係程式已經硬性驗證咗語意。

### 模擬 channel 未 ready

先正常啟動 probe，等 native channel ready；播放期間短暫停 login helper 嘅 desktop/native side，或者暫時退出 Grok Bot app，令下一句讀清單失敗。唔好一開始就停晒 helper：咁只會測到 probe 嘅啟動錯誤，聽唔到 voice 失敗回覆。要保留 voice 連線先測到工具失敗；如果退出 app 令 voice 都斷咗，記錄為連線中斷，恢復後再測 native-only 失敗。可另建 script 目錄，只放 01 讀清單 WAV 重跑；如果想一開始就喺 native channel 未 ready 嘅情況下測（例如 Grok Bot app 未開 desktop 部分），加 `--allow-not-ready`，probe 會照樣播，等你聽失敗回覆。檢查非 accepted relay、廣東話誠實提示、冇假清單。完成後重開 app／helper。

## 有 CoreS3 嘅替代做法

照現有 CoreS3 接駁方式連去同一個 gateway，設好 device token；保持上述 child profile、gbot 同 matching agent ID。唔跑無板 probe，用撳掣開始逐句讀同一份劇本。每次等小芽講完先下一句；驗收表同 app 檢查完全一樣，另試撳掣打斷。唔需要改燒錄或者佔用 serial port。

## 小芽 agent 規則建議

家長可以將以下文字加落小芽 agent instructions（voice 同 app 共用）：

> 每日一張功課清單，每項有科目、內容、狀態（只准「未做」或者「小朋友話做完」），可加家長備註；另存每日目標（自由文字）。家長喺 Grok Bot app 建立或修改後，覆返清單俾家長核對。經 send_task 問清單時，用香港廣東話一至兩句讀出今日未做項目；冇資料就老實講攞唔到，唔好作。只有 request 明確講小朋友已確認，先標記「小朋友話做完」；唔清楚就唔改。確認後嘅求助訊息留喺呢個對話，俾家長睇到。永遠唔標記為已批准，唔批改、唔俾分或獎勵，呢樣要問爸爸媽媽。Task 結果唔授權再派新 task。

## 驗收紀錄

**結果記錄喺驗收紀錄**，可以喺自己 Mac 複製以下模板填寫；唔好填真實小朋友名字、agent ID 或登入資料。

- 日期：
- Grok Bot app 版本：
- Gateway revision：
- 測試方式：無板 probe／CoreS3
- 01 清單一致：合格／唔合格；備註：
- 02 未做項目：合格／唔合格；備註：
- 03–04 確認及取消冇改動：合格／唔合格；備註：
- 05–06 確認後更新、accepted 先收到：合格／唔合格；備註：
- 07–08 確認後求助、app 見到留言：合格／唔合格；備註：
- 09 批改／分數交家長：合格／唔合格；備註：
- Channel 未 ready／refused 誠實失敗：合格／唔合格；模擬方式：
- Task 結果冇自行授權新 task：合格／唔合格；備註：
- 整體：合格／唔合格；待跟進：
