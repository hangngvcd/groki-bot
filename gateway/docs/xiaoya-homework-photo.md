# 功課影相 (ticket 33)

The child calls homework_done with an optional title/#id. A single unfinished
item is selected automatically; ambiguity asks 「係邊樣做完？」 once, listing at
most three choices. No unfinished items returns a gentle empty response.
Resolved items enter photo state with
「好嘢！擺份功課喺我前面俾我影低先。」

After positioning, call homework_photo_snap. A capture failure, dark or blank
image asks for repositioning; alignment may be supplied by an injected assessor.
There are three attempts total (first attempt plus at most two retries).
Success or exhausted retries calls existing forms.mark_done, preserving
level_up/game_unlock/invite_spoken/local_game_handoff. Firmware plays local praise
and celebration; the agent says only 收到. No parent approval is needed for the
existing evolution game handoff. homework_photo_cancel clears state without
marking done; listening can invoke it when the child aborts.

HomeworkPhoto accepts capture(), assess(path), send(bot, text), forms and presence.
capture returns JPEG bytes (saved by save_frame) or an adapter-managed local path.
No Mac camera starts. The default capture adapter explicitly fails until board
integration exists; unconnected boards therefore follow the no-photo fallback.
Generic take_photo and HTTP uploads remain blocked under child profile.
Voice tools accept no image paths or image bytes from the model.

Parent helper bot: 05d81ca9-b265-4d98-b754-d3ecd4693095.
GrokBotClient.send receives a fixed text payload, never a chat transcript:

    【鈴鈴提示】娜娜做完功課，請睇相。
    功課：中文
    相：/local/captures/homework/homework_....jpg (or 冇相)
    時間：2026-10-27 20:00

photo_sent means the path-bearing message was accepted, not that dad read it.
Send errors return parent_notified=false; never say dad received it.
The current gbot client supports text only: cloud attachment ingestion/retention
by the parent-helper adapter remains an integration follow-up. A local path alone
does not upload JPEG bytes. Cloud records must remain after Mac cleanup.

Private Mac JPEG temp files live under ~/.stackchan/captures/homework.
Each saved frame has a daemon 24h deletion timer. Startup/save/completion also
remove expired homework_*.jpg files, covering restarts (while gateway is stopped,
cleanup resumes on next startup). Adapter-supplied paths need their own retention.
Presence metadata frames never enter this storage helper.

Photo guidance sets presence.set_suppress("photo", True); completion (finally)
and cancellation clear it. Generic capture HTTP uses independent photo_upload
suppression so an HTTP transaction cannot clear the longer guidance window.

Parent API accepts {"redo":["中文"],"reason":"再睇第二題"}; CLI supports
homework redo 中文 --reason 再睇第二題. Items become pending, without form downgrade
or reclaiming games. Optional gentle reasons append to a title-specific line;
shaming reasons are omitted. CameraPresence.consume_pending_redo() returns a
PresenceAction and clears it once. Desk return already consumes this; button/wake
owners should call the same hook and render locally (no LLM session).

Firmware/runtime follow-ups:

- Connect OV5640 one-frame snap adapter to HomeworkPhoto.capture; persist only
  explicit homework snaps. Add alignment assessment if available.
- Deliver returned device_messages via existing forms sender; implement these
  stubs, with local guidance playback and snap readiness:
  {"type":"xiaoya","cmd":"homework_photo","phase":"guide"}
  {"type":"xiaoya","cmd":"homework_photo","phase":"shutter","sfx":"sfx.shutter","face":"face.camera"}
  {"type":"xiaoya","cmd":"homework_photo","phase":"done"}
  {"type":"xiaoya","cmd":"homework_photo","phase":"cancel"}
- Implement shutter sound, camera flash, local celebration/praise and existing
  evolve-animation/game handoff. Listen cancellation must call cancel.
- Emit matched presence_activity photo begin/end events including cancel, as
  documented in xiaoya-camera-presence.md. Do not end suppression mid-flow.
- Render consume_pending_redo once on button/wake, sharing desk-return state.
- Parent-helper integration must ingest the local JPEG for cloud storage/send;
  real device/cloud delivery requires hardware acceptance testing.

No firmware was flashed; no game logic was reimplemented.

## 42b: OV5640 WS text capture

The default `HomeworkPhoto.capture` is now the lazy `PhotoCapture` adapter.
The current ESP32 connection binds its forms sender and event loop at hello;
replacement/disconnect fails pending requests. Voice snap awaits capture on a
worker thread, so the receive loop remains available. Forms state and UI result
processing stay on the event loop. An explicit cancel rejects late results before
saving; simultaneous snaps are rejected.

The adapter sends shutter UI first, then a separate capture authority:

```json
{"type":"system","command":"xiaoya.photo","nonce":"<32 URL-safe random characters>"}
```

Guide/shutter/done/cancel phases never authorize sensor capture. Only one pending
nonce exists; its deadline is 18 seconds total (send included, maximum 20).
Only events from the active device connection with an exact pending nonce are
accepted. Unsolicited, stale and replayed results are ignored. Transport identity
and authorization are inherited from the existing device connection; no new
HTTP capture endpoint or cloud upload is introduced.

A single frame may arrive as WS **text JSON**:

```json
{"type":"xiaoya","event":"homework_photo","status":"frame","nonce":"<echo>","mime":"image/jpeg","encoding":"base64","data":"<base64>"}
```

The SpotPear firmware uses bounded chunks instead:

```json
{"type":"xiaoya","event":"homework_photo","status":"uploading","nonce":"<echo>","mime":"image/jpeg","encoding":"base64","bytes":12345,"chunks":5}
{"type":"xiaoya","event":"homework_photo","status":"chunk","nonce":"<echo>","i":0,"data":"<base64>"}
{"type":"xiaoya","event":"homework_photo","status":"complete","nonce":"<echo>"}
```

`i` is zero-based and strictly sequential; each chunk is independently base64
encoded (firmware: up to 3072 decoded bytes / 4096 base64 characters). Gateway
requires exact announced byte and chunk counts, strict base64 and JPEG SOI/EOI,
and caps decoded JPEG bytes at 1 MiB and chunk count at 512. Firmware caps the
encoded JPEG at 200 KiB before any data is sent. Binary WS remains Opus-only.
Malformed/oversized uploads and matching `camera_unavailable`, `capture_failed`
or `too_large` statuses raise OSError into the existing retry/冇相 path. Pending
buffers are cleared on completion, cancellation, timeout and connection change.

JPEG events are consumed before any cloud handler. Only this explicit homework
path saves a private local photo under `~/.stackchan/captures/homework`, using
the existing 24-hour cleanup policy. Presence frames are never accepted here.
The parent helper notification still uses the existing saved-path delivery;
this change does not verify Mac permissions or parent attachment delivery live.
