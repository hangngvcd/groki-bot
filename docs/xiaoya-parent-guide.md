<!-- SPDX-License-Identifier: MIT -->
# 小芽家長指南

呢份指南俾家長喺 Mac 管理小芽。以下 `scripts/` 命令都喺 repo 嘅 `gateway/` 目錄執行。Mac 要先裝好 gateway 依賴、uv、Node.js 同 Grok Bot app，並登入自己嘅帳戶。「小白」wake word 要先跟 [gateway README](../gateway/README.md) 下載 KWS model 去 `gateway/models/kws`；冇 model 時，因為服務一直開住 `STACKCHAN_BUTTON_ONLY_LISTEN=1`，gateway 會變「只准撳掣」，唔會將聲音全部轉發。

## 一次性安裝

先將設定範本複製去 repo 外面：

```sh
mkdir -p ~/.stackchan
chmod 700 ~/.stackchan
cp scripts/xiaoya-services.env.example ~/.stackchan/xiaoya-services.env
chmod 600 ~/.stackchan/xiaoya-services.env
```

用文字編輯器填好檔案。`YOUR_AGENT_ID` 係「小芽」agent 嘅 ID，兩處一定要完全一樣；`YOUR_AGENT_NAME` 係佢個名。`generate-a-long-random-token` 要換成至少 16 字嘅裝置 token，並同部機設定一致。唔好將 token 貼入聊天、截圖或者 repo。每行用 `KEY=VALUE`，值直接寫，唔加引號，亦唔會展開 `$HOME` 或執行命令。uv／node 如果唔喺一般安裝位置，用 `UV_BIN`／`NODE_BIN` 填完整路徑。

```sh
scripts/xiaoya_services.sh check
scripts/xiaoya_services.sh install
```

`check` 會遮住 token 同 agent ID。安裝會建立兩個登入後自動啟動嘅背景服務，同 `~/.stackchan/bin/stackchan-parent`。設定檔必須保持 `chmod 600`；服務用「小白」喚醒、兒童模式、Grok 語音、家長 API，並停用 USB，唔會開裝置 serial port。家長 API 嘅獨立 token 由 gateway 喺 Mac 自己產生，唔使填入服務設定或 plist。

如果 `gateway/.env` 仲喺度，腳本會提醒：同名設定以 plist 為準，但 `.env` 可以加入其他設定。安裝前檢查有冇唔需要嘅舊設定。

另外建立獨立嘅「小芽家長助手」Grok Bot，跟[家長助手 persona](xiaoya-parent-bot-persona.md)設定，Mac shell 工具要開家長逐次批准。「小芽」小朋友 agent 唔應該有 Mac shell 權限。首次幫部機設定 Wi-Fi，先用部機自己嘅 setup page；日常操作跟下面流程。

## 每日開始前檢查

Mac 要醒住、登入咗使用者帳戶，Grok Bot app 亦要已登入。喺 System Settings → Battery／Energy，開啟接電源時「Prevent automatic sleeping when the display is off」（各 macOS 版本名稱可能唔同）。臨時用可以開 Terminal 行 `caffeinate -i`，保持個命令行住；用完按 Ctrl-C。

問「小芽家長助手」：「小芽而家點？」批准佢執行狀態命令。健康答案會話部機已連線、語音正常、聆聽模式係叫「小白」或者撳掣。未有電量資料只代表韌體未報電量；未連線就先排解，唔好當已準備好。

## 點樣問 Grok Bot 睇狀態同改設定

可以問：「部機連咗未？」「音量改做 40」「五分鐘冇傾偈就休息」「關閉鏡頭偵測」「開啟通知」。家長助手會先讀現有值，同你確認「舊值 → 新值」，批准後用 `~/.stackchan/bin/stackchan-parent` 執行，再照結果答你。

「已儲存但部機未支援」代表設定已記低，但韌體仲未有對應功能，部機未必已經改咗。鏡頭偵測同通知預設關閉。詳細機制見[家長 bridge](xiaoya-parent-bridge.md)。`/debug/panel` 只係開發除錯用，日常唔使開。

## Wake word「小白」調校

先跟 [kws_eval 工具說明](../gateway/tools/kws_eval/README.md)，用合成語音做正樣本、用一般背景聲做負樣本，比較命中率同誤觸發。工具有產生樣本、掃 threshold／score 同分析結果嘅命令；合成語音成績唔等於小朋友真實表現。

真聲測試要先得到家長同小朋友同意，只留喺本機，唔上載、唔入 git。測完刪除原始錄音、轉換副本同測試結果。先觀察漏聽同電視誤觸發，再揀門檻，唔好只追求容易叫醒。

喺 `~/.stackchan/xiaoya-services.env` 修改 `STACKCHAN_KWS_THRESHOLD` 同 `STACKCHAN_KWS_SCORE` 後，要重新寫入 plist 再重開：

```sh
scripts/xiaoya_services.sh check
scripts/xiaoya_services.sh install
scripts/xiaoya_services.sh restart
```

如果仲唔穩定，加 `STACKCHAN_WAKE_WORD=0`，再行以上步驟，就變成「只准撳掣」（服務一直開住 `STACKCHAN_BUTTON_ONLY_LISTEN=1`，所以 wake word 關咗或者 model 壞咗，都只會喺撳掣之後先送聲）。家長助手可以睇聆聽模式，但暫時唔可以經家長 API 改 wake word 門檻。

## 斷線排解

| 現象 | 檢查 | 做法 |
| --- | --- | --- |
| 部機話連唔到 | Wi-Fi、Mac 同部機係咪同一網絡，gateway 有冇行 | 檢查部機嘅 gateway 位址，再行 `scripts/xiaoya_services.sh status` |
| Grok Bot 登出 | 開 app 睇登入狀態 | 重新登入，再 `scripts/xiaoya_services.sh restart` |
| login helper 冇行 | `scripts/xiaoya_services.sh status`；`~/Library/Logs/xiaoya/login-helper.log` | 確認 Node.js 路徑同 app 登入，再 restart |
| gateway 冇行 | `scripts/xiaoya_services.sh status`；`~/Library/Logs/xiaoya/gateway.log` | 確認 uv、gateway 依賴同設定；check 後重新 install |
| Mac 瞓咗 | Mac 係咪醒住、接電源、使用者已登入 | 喚醒並調整睡眠設定，再 restart |
| token 唔夾，出現 401 | 裝置 token 係咪同服務設定一致 | 喺本機修正 `STACKCHAN_TOKEN`，install 再 restart；唔好貼出 token。若係家長 API 認證錯，檢查本機 parent token 設定，唔好用裝置 token 代替 |
| wake word 唔靈 | 模式有冇變成只准撳掣、環境噪音、測試命中率 | 跟調校步驟；暫時用撳掣 |

Log 可能有診斷資料，分享前先檢查同遮住私人內容。`status` 會顯示兩個服務嘅運行摘要；有安裝 wrapper 就會再讀家長 API。

## Grok Bot app 更新後重測步驟

原生語音用緊特定 app 版本嘅內部 API，現有整合曾以 0.71.2 測試。每次更新 app 都做一次：

1. 確認 app 已登入，行 `scripts/xiaoya_services.sh restart`。
2. 問家長助手「小芽而家點？」確認服務同部機連線。
3. 跟 [ticket 10 功課語音 probe／checklist](xiaoya-acceptance/10-homework-voice-flow.md) 重測。
4. 叫「小白」，問「你係邊個？」做短語音測試，確認聽到、答到，同用緊正確 agent。

## 點樣喺 app 管理功課清單

喺 Grok Bot app 同「小芽」agent 傾偈，直接打功課清單，或者傳手冊相。叫佢讀返清單俾家長核對，確認科目、內容同次序，再逐項加、改、刪。可以問進度，或者睇小朋友留低嘅求助訊息。呢啲喺「小芽」對話處理；「小芽家長助手」負責裝置設定。小朋友話做完 ≠ 家長批准，要家長檢查後確認。

## 私隱提醒

日常語音流程唔儲存錄音；上面自願做嘅調校錄音要自己清理。鏡頭預設關閉。對話歷史跟 Grok Bot 嘅保留政策，第一階段未有自動七日刪除規則，唔好假設七日後一定消失。唔好將 token 或 agent ID 放入 persona 或聊天。

## 停用／移除服務

```sh
scripts/xiaoya_services.sh uninstall
```

會停止兩個 LaunchAgent，移除 plist 同家長 CLI wrapper。設定檔、家長 token、已儲存設定同 log 會保留喺本機；有需要先自行整理，唔好將佢哋上載。
