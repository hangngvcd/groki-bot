# 電池校準（爸爸操作）

Gateway 要開家長 API（`STACKCHAN_PARENT_API=1`），沿用家長 token，
只限 localhost。小朋友語音工具冇校準權限；家長 Grok Bot MCP 有
`xiaoya_battery_cal`，門檻可用 `xiaoya_set_setting` 改。

先充滿，開始時拔 USB，用固定負載一路放電到自動關機。
**假設電量百分比同時間成正比**；負載大幅改變、半途充電或提早停機嘅資料唔適合。
Gateway 無法單靠 raw 驗證有冇真係充滿／關機，爸爸要確認。

```sh
stackchan-parent battery-cal start --interval 30
# 保持 gateway 開住，等部機放電至關機
stackchan-parent battery-cal stop
stackchan-parent battery-cal fit
# fit 只顯示建議，唔套用。可指定 CSV；用 start 回傳嘅實際路徑：
stackchan-parent battery-cal fit /tmp/sc-state/battery_cal/20261029-120000.csv
stackchan-parent battery-cal apply /tmp/sc-state/battery_cal/20261029-120000.csv
# 或爸爸明確輸入六個實測 raw（以下只係格式示例）：
stackchan-parent battery-cal apply --raw 1800,2000,2200,2400,2600,3000
stackchan-parent battery-threshold 20
stackchan-parent battery-cal reset
```

CSV 喺 `$STACKCHAN_STATE_DIR/battery_cal/YYYYmmdd-HHMMSS.csv`，
未設 state dir 就用 `~/.stackchan`。只記 `battery_raw` 嘅
`t_wall,uptime_s,raw,mv,pct`，唔記對話或其他事件。一次只錄一個 run，
stop 後重開新 run；gateway 重啟會停止取樣記錄，舊 CSV 保留。

Fit 至少 60 分鐘、60 個樣本，uptime 必須一直增加（重啟資料會拒絕）。
先用五點滑動中位數去雜訊，按時間取 0/20/40/60/80/100% raw；
六點必須嚴格遞增、1000–3500、總跨度至少 150，否則拒絕。
套用／reset 會儲存設定，連線中即推送，下次 `form_state` 連線就緒亦重送。
reset 只還原 curve，低電門檻維持爸爸設定（5–50%，預設 20%）。

協定全部用 `type:xiaoya`：`battery_cal` 帶 `on,interval_s`（5–600），
`battery_curve` 帶 `raw`（六點或 null），`battery_threshold` 帶 `pct`。
`battery_raw` 事件只留本機；`mv/pct=-1` 可表示未知。
現有 `battery` 事件容許新增欄位，例如 `charging_likely`。
