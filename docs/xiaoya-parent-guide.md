<!-- SPDX-License-Identifier: MIT -->
# 小芽家長指南（鈴鈴）

> 部機個角色而家叫「鈴鈴」（一隻白貓仔）。repo、服務名同 agent 名（「小芽」小朋友 agent、「小芽家長助手」）維持唔變。鈴鈴叫小朋友「娜娜」，練英文時叫「Elena」。

呢份指南俾家長喺 Mac 管理鈴鈴（小芽系統）。以下 `scripts/` 命令都喺 repo 嘅 `gateway/` 目錄執行。Mac 要先裝好 gateway 依賴、uv、Node.js 同 Grok Bot app，並登入自己嘅帳戶。「鈴鈴」wake word 要先跟 [gateway README](../gateway/README.md) 下載 KWS model 去 `gateway/models/kws`；冇 model 時，因為服務一直開住 `STACKCHAN_BUTTON_ONLY_LISTEN=1`，gateway 會變「只准撳掣」，唔會將聲音全部轉發。

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

`check` 會遮住 token 同 agent ID。安裝會建立兩個登入後自動啟動嘅背景服務，同 `~/.stackchan/bin/stackchan-parent`。設定檔必須保持 `chmod 600`；服務用「鈴鈴」喚醒、兒童模式、Grok 語音、家長 API，並停用 USB，唔會開裝置 serial port。家長 API 嘅獨立 token 由 gateway 喺 Mac 自己產生，唔使填入服務設定或 plist。

如果 `gateway/.env` 仲喺度，腳本會提醒：同名設定以 plist 為準，但 `.env` 可以加入其他設定。安裝前檢查有冇唔需要嘅舊設定。

另外建立獨立嘅「小芽家長助手」Grok Bot，跟[家長助手 persona](xiaoya-parent-bot-persona.md)設定，Mac shell 工具要開家長逐次批准。「小芽」小朋友 agent 唔應該有 Mac shell 權限。首次幫部機設定 Wi-Fi，先用部機自己嘅 setup page；日常操作跟下面流程。

## 每日開始前檢查

Mac 要醒住、登入咗使用者帳戶，Grok Bot app 亦要已登入。喺 System Settings → Battery／Energy，開啟接電源時「Prevent automatic sleeping when the display is off」（各 macOS 版本名稱可能唔同）。臨時用可以開 Terminal 行 `caffeinate -i`，保持個命令行住；用完按 Ctrl-C。

問「小芽家長助手」：「鈴鈴而家點？」批准佢執行狀態命令。健康答案會話部機已連線、語音正常、聆聽模式係叫「鈴鈴」或者撳掣。未有電量資料只代表韌體未報電量；未連線就先排解，唔好當已準備好。

## 點樣問 Grok Bot 睇狀態同改設定

可以問：「部機連咗未？」「音量改做 40」「五分鐘冇傾偈就休息」「關閉鏡頭偵測」「開啟通知」。家長助手會先讀現有值，同你確認「舊值 → 新值」，批准後用 `~/.stackchan/bin/stackchan-parent` 執行，再照結果答你。

「已儲存但部機未支援」代表設定已記低，但韌體仲未有對應功能，部機未必已經改咗。鏡頭偵測同通知預設關閉。詳細機制見[家長 bridge](xiaoya-parent-bridge.md)。`/debug/panel` 只係開發除錯用，日常唔使開。

## 今日用量（本機數字）

可以問家長助手：「今日用量點？」或者喺 Mac 行：

```sh
~/.stackchan/bin/stackchan-parent usage --text
```

回覆係香港日曆日嘅本機計數（例如叫醒次數、聽咗幾耐、功課相成功／失敗），**唔會**有對話內容、錄音或相片。最多保留約 14 日；午夜（香港時間）開新一日。


## Wake word「鈴鈴」調校

先跟 [kws_eval 工具說明](../gateway/tools/kws_eval/README.md)，用合成語音做正樣本、用一般背景聲做負樣本，比較命中率同誤觸發。工具有產生樣本、掃 threshold／score 同分析結果嘅命令；合成語音成績唔等於小朋友真實表現。

真聲測試要先得到家長同小朋友同意，只留喺本機，唔上載、唔入 git。測完刪除原始錄音、轉換副本同測試結果。先觀察漏聽同電視誤觸發，再揀門檻，唔好只追求容易叫醒。

鈴鈴講嘢期間（包括 Grok 語音同部機本地播放嘅自我介紹／升級聲）同講完後 0.5 秒，gateway 會暫停 wake word，免得聽到自己把聲叫醒自己；呢段時間可以撳掣插話。尾段長短可以用 `STACKCHAN_WAKE_SELF_MUTE_TAIL_S` 改（預設 `0.5`）。

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
2. 問家長助手「鈴鈴而家點？」確認服務同部機連線。
3. 跟 [ticket 10 功課語音 probe／checklist](xiaoya-acceptance/10-homework-voice-flow.md) 重測。
4. 叫「鈴鈴」，問「你係邊個？」做短語音測試，確認聽到、答到，同用緊正確 agent。

## 點樣喺 app 管理功課清單

喺 Grok Bot app 同「小芽」agent 傾偈，直接打功課清單，或者傳手冊相。叫佢讀返清單俾家長核對，確認科目、內容同次序，再逐項加、改、刪。可以問進度，或者睇小朋友留低嘅求助訊息。呢啲喺「小芽」對話處理；「小芽家長助手」負責裝置設定。小朋友話做完 ≠ 家長批准，要家長檢查後確認。

## 鈴鈴形態同功課進度

鈴鈴每日由「小苗貓」（F1）開始，跟今日功課完成比例長大，只會升唔會跌，第二日 00:00 重設：

| 形態 | 條件（完成／總數） |
| --- | --- |
| F1 小苗貓 | 未開始，或者今日未有功課（總數 0） |
| F2 小翼貓 | 完成至少三分之一 |
| F3 寶石貓 | 完成至少三分之二 |
| F4 星冠貓 | 全部完成 |

功課清單由家長設定（喺 Mac 行命令，或者叫「小芽家長助手」幫你加／刪；佢唔會報進度）。娜娜每做完一項同鈴鈴講，鈴鈴問一句確認，答「係」先記低做完；gateway 跟清單自動計形態，唔使 AI 判斷。加功課、要求重做都唔會令鈴鈴降級；刪功課會重新計，夠數就升級。半夜清單清空、返 F1。

```sh
~/.stackchan/bin/stackchan-parent homework                          # 睇今日清單同形態
~/.stackchan/bin/stackchan-parent homework add "中文作文" "數學 p.12"  # 加功課
~/.stackchan/bin/stackchan-parent homework remove "數學"             # 刪功課（可用 #編號）
~/.stackchan/bin/stackchan-parent homework redo "中文作文"           # 要求重做（唔會降級）
```

一次跳幾級（例如由 F1 直接到 F4），部機會逐級變身，每級相隔大約半秒，最後講一句讚賞。升級動畫喺「完成功課」嗰輪對話講完、返到陪伴狀態先播；如果當時喺小休或者小遊戲，會等完咗先播。播動畫期間同之後 0.5 秒唔會理 wake word，之後返陪伴表情。（影相同慶祝動畫未做，到時會插喺「記低做完」同升級之間。）

## 重播鈴鈴自我介紹

部機第一次開機會播一次自我介紹，之後唔再自動播。想再聽：

```sh
~/.stackchan/bin/stackchan-parent intro
```

部機未連線會答「鈴鈴未連線」。第一次開機嘅記錄存喺部機 NVS，熄機都唔會唔見，所以之後開機唔會再播（同有冇資源包無關）。如果資源包冇自我介紹聲（`vo.intro.1`），部機會叫 gateway 用免費 edge-tts 讀同一句（唔用 AI 模型）；gateway 唔得就略過，唔會顯示文字，亦唔會下次開機再試。

## 換鈴鈴圖片同聲音

鈴鈴每個形態、每個表情嘅圖片同埋自我介紹／升級聲，都放喺一個獨立嘅資源包，燒錄位置 `0xdb1000`，可以淨係換資源包、唔使重燒韌體。冇包或者某張圖唔啱，部機會自動用返內置畫出嚟嘅樣。製作同燒錄步驟見 [鈴鈴資源包說明](../tools/lingling_assets/README.md)。

## 考卷分析（36–42）

爸爸交娜娜嘅考卷、測驗、默書或者工作紙相，「小芽家長助手」用 Grok Bot 自己睇相，出短報告，完整報告存喺 Mac。v1 只做數學同中文生字。唔用其他 AI 或者付費服務，冇網頁。家長助手要先跟[家長助手 persona](xiaoya-parent-bot-persona.md)加「考卷分析」一節。

### 交相（兩條路）

1. 喺「小芽家長助手」對話直接 send 相，加一句，例如「娜娜今日數學測驗」。
2. 將相放入 Mac 嘅 `~/xiaoya-data/papers/inbox/`，再同家長助手講「分析考卷」。**唔會自動監察**，唔講就唔會分析。第一次用之前，行一次 `~/.stackchan/bin/stackchan-parent papers inbox`，gateway 就會開好資料夾。

貼士：每頁一張、影正、光猛；有改卷紅筆嗰版最有用；名、學校可以遮。一份卷多頁就一次過 send，講明「同一份」。睇唔清嘅頁，家長助手會叫你重影，唔會估。

### 報告同確認

- 對話會出一份最多 10 行嘅短報告：分數只抄卷面，列出最重要嘅弱項，每項標「清楚／唔肯定」。弱項**冇數目上限**：短報告放唔晒嘅，會寫「仲有 N 項，喺完整報告」。
- 完整報告存喺 `~/xiaoya-data/reports/`（Markdown），唔抄姓名同學校。inbox 嘅相分析完會搬去 `~/xiaoya-data/papers/`。
- 家長助手會問「要唔要將呢 N 個弱項加入鈴鈴陪練？」。**爸爸答好先會加入**；未確認嘅弱項鈴鈴睇唔到。
- 之後可以問「娜娜而家弱咩？」「睇返上次數學分析」，家長助手會讀 Mac 檔答，唔使重新睇相。想暫停、恢復或者刪除某項，直接同佢講。

### 鈴鈴點樣陪練

- 娜娜做完一項相關功課（例如數學）之後，鈴鈴會喺慶祝之後直接開練，**每日最多 2 題**（全部弱項加埋）。
- 唔可以拒絕：娜娜講「唔要」，鈴鈴會溫柔咁繼續。連續拒絕兩次，或者嗰題當日冇做完，都計入當日 2 題，唔會補。
- 鈴鈴可以講「練吓測驗嗰類題」，但**唔會提分數**，亦唔會講「你上次錯咗」。
- 加練**唔計**功課進度、唔令鈴鈴升級、唔解鎖遊戲；加練卡住亦唔會通知爸爸。
- 連續啱 3 次就當過關，鈴鈴讚一次，之後唔再練嗰項。
- 想娜娜今日多練幾題：同家長助手講，或者行 `stackchan-parent practice-extra 1`（1–5 題，只限今日）。

### 每週進度

每逢星期日，每日用量報告後面會多一段「【考卷弱點：本週進度】」：練緊嘅弱項、本週過關、練咗幾題、放棄幾題、未確認幾項、仲建議練邊啲。冇嘢報就唔會出。想即刻睇：`stackchan-parent usage --weekly --text`。

### 考試前溫書

同家長助手講「下週考中文」，佢會由弱項清單排好溫書次序（生字先、易錯優先）俾你睇。**你確認先**會放入功課清單做「溫書」項；溫書項同其他功課一樣，計功課進度同慶祝。

### 保留同刪除

- 資料只存喺 Mac 嘅 `~/xiaoya-data/`（`papers/` 相、`reports/` 報告、`weak_points.json` 弱項、`practice.json` 每日加練紀錄）。部機同 gateway log 冇相、冇分數。
- 相保留 **90 日**；報告同弱項保留到**學年尾（8 月 31 日）**。家長助手讀資料或者每日報告時會順手清走過期嘅。
- 講「刪晒娜娜考卷資料」：家長助手會先列出要刪幾多個檔，你確認先刪。

### 命令速查

```sh
~/.stackchan/bin/stackchan-parent papers inbox                 # inbox 有咩相
~/.stackchan/bin/stackchan-parent papers reports               # 報告清單
~/.stackchan/bin/stackchan-parent papers report REPORT_ID      # 讀一份報告
~/.stackchan/bin/stackchan-parent weak-points                  # 弱項清單同狀態
~/.stackchan/bin/stackchan-parent weak-points confirm ID...    # 確認加入陪練（pause／resume／delete 一樣）
~/.stackchan/bin/stackchan-parent practice-extra 1             # 今日加 1 題
~/.stackchan/bin/stackchan-parent papers revision --subject chinese_vocab   # 溫書建議
~/.stackchan/bin/stackchan-parent papers revision-add "溫書：生字 蝴、蝶"     # 確認後加入功課清單
~/.stackchan/bin/stackchan-parent papers purge                 # 即刻清過期資料
~/.stackchan/bin/stackchan-parent papers delete-all            # 列出要刪嘅檔；確認後加 --yes 數目
```

預設設定（資料夾 `~/xiaoya-data`、學年尾 8 月 31 日、星期日出每週進度）已經係爸爸揀好嘅，唔使改服務設定。

## 私隱提醒

日常語音流程唔儲存錄音；上面自願做嘅調校錄音要自己清理。鏡頭預設關閉。考卷資料嘅保留同刪除見上面「考卷分析」。對話歷史跟 Grok Bot 嘅保留政策，第一階段未有自動七日刪除規則，唔好假設七日後一定消失。唔好將 token 或 agent ID 放入 persona 或聊天。

## 停用／移除服務

```sh
scripts/xiaoya_services.sh uninstall
```

會停止兩個 LaunchAgent，移除 plist 同家長 CLI wrapper。設定檔、家長 token、已儲存設定同 log 會保留喺本機；有需要先自行整理，唔好將佢哋上載。


## 44b：家長通道補充

- Mac 先確認 `node --version` 至少 22.19，同 `gbot --version` 有回應。
  `STACKCHAN_PARENT_BOT_ID` 應指向爸爸專用助手；唔好用小朋友 agent。
  現有安裝腳本未自動檢查呢兩項，安裝前要手動核對。
- 喺 Grok Bot app 開啟家長助手聊天通知。`stackchan-parent set notifications on`
  係離席通知開關，預設 **OFF**；求助通知唔受呢個開關限制。
- 功課清單用家長助手或 `stackchan-parent homework add/remove/redo` 修改；
  小朋友 agent 只可以報進度，唔應用佢做家長後台。
- 電池校準見 [爸爸校準命令](../gateway/docs/xiaoya-battery-calibration.md)。
  功課 session 安全 timeout 可用 `stackchan-parent set session_idle_min 30` 改。

## 行開門檻同通知（23／44f）

`stackchan-parent set presence_leave_min 3` 可以改「行開幾多分鐘先當行開」，
可揀整數 1–30，預設 2 分鐘。鏡頭偵測有效開緊、又冇暫停時先會計。

`notifications` 預設 **OFF**。爸爸主動用
`stackchan-parent set notifications on` 開啟後，每段離席達門檻只送一次
「【鈴鈴提示】娜娜行開咗 X 分鐘。」去現有家長助手；返嚟後先重新計。
冇相、對話、信心值或檔案路徑。關返用 `stackchan-parent set notifications off`。
Ticket 32「只有求助／功課相即時推送」照舊，除非爸爸主動開啟通知。
