# Desk camera presence — ticket 23

Gateway policy lives in `stackchan_mcp/camera_presence.py`. Product default is
OFF (`parent_settings.camera_presence = False`). It does not start the Mac
camera, including in the child profile. Board OV5640 detections are the intended
product input. Existing demo reactions remain a separate demo-only path.

`homework_list` with unfinished items starts a study session. Repeated reads do
not restart the grace period. `start_homework_session()` is also available to
future explicit study entry points. Any forms commit showing `done == total > 0`
ends detection. A later parent ON enables it again; merely reading a completed
list does not override that ON. Parent OFF overrides subsequent study entry
until day's completion clears the session override. Parent preferences are
stored separately from session arming: an explicit OFF must work even when the
stored preference was already False. A stored manual ON is restored at restart;
session arming, timers and clip rotation otherwise remain in memory.

After enabling, suppress prompts for 10 seconds. Qualified `/track` hits (existing
confidence threshold 0.5) feed `note_face`; the existing idle poll calls `tick`
every 0.5 seconds. Missing face hits for 120 seconds produces exactly one
`vo.leave_wait.1` (「我喺度等你呀」). There is no countdown, nag or negative face.
Further absence is silent. On return, play one clip, rotating through
`vo.welcome_back.1`–`vo.welcome_back.6`, then `vo.joke.1`–`vo.joke.3`.
These are reserved local Cantonese clip IDs, each at most 3 seconds; final audio
assets are a firmware follow-up. Use one line, no follow-up question, no LLM.
Short absences do not prompt. A pending redo replaces that return's welcome once;
forms redo currently supplies a gentle generic line. `set_pending_redo` also
accepts a local `vo.*` clip ID or a future parent-provided line.

Detection frames are **never saved or uploaded**. The policy accepts only face
hit metadata, holds monotonic timestamps and flags, and has no image, capture,
storage, upload or LLM path. `/track` receives numeric detection metadata, never
frames. Board firmware must perform detection locally and send only metadata.
An enabled detector must keep reporting visible people: loss of the detector
stream currently looks like absence, so firmware should disable/suppress on a
known detector failure. Ticket 33 explicit homework photos are a separate path
that may save/upload photos; they must never reuse detection frames.

## Suppression

All of photo, evolve animation, minigame, speaking, first-boot intro and enable
grace suppress leave/welcome. Suppression pauses absence accounting (resume
starts a fresh continuous absence window). Returns during suppression are
consumed silently, with no delayed welcome; redo remains pending for a later
qualified return. Concurrent reasons are independent.

Wired now: gateway photo HTTP transaction (try/finally), device speaking state,
local TTS status, active minigame state. Public `set_suppress(reason, active)`
allows the ticket 33 workflow to pause from photo guidance through completion or
cancellation, beyond the narrower HTTP transaction. Board lifecycle events are
accepted locally through the existing xiaoya device event handler:

```json
{"type":"xiaoya","event":"presence_activity","reason":"photo","active":true}
{"type":"xiaoya","event":"presence_activity","reason":"photo","active":false}
```

`reason` may be `photo`, `evolve`, or `intro`. Emit matching begin/end events,
including cancellation. Evolve and first-boot intro detection are deferred until
firmware emits these events; the gateway cannot infer device animation duration.
The detector should continue reporting presence while prompts are suppressed.

## Protocol stubs and firmware follow-ups

Effective ON/OFF transitions are drained by the idle poll using forms' existing
session-ready sender. Current state is also resent on `form_state` after reconnect:

```json
{"type":"xiaoya","cmd":"camera_presence","on":true}
{"type":"xiaoya","cmd":"camera_presence","on":false}
{"type":"xiaoya","cmd":"presence_line","kind":"welcome","clip":"vo.welcome_back.1","spoken":""}
```

`kind` is `leave_tip`, `welcome`, or `redo`. Prefer the local clip. `spoken` is
only populated for a pending redo text; local rendering is required, never open
an LLM session for these messages. Disconnected audio prompts are not queued
for replay. Device-side suppression should also guard playback races.

Firmware follow-ups:

- Run OV5640 person/face detection locally only while effective ON; send numeric
  detections to gateway `/track` (or add an equivalent local person-event adapter).
  Never save/upload detection frames. No Mac tracker autostart is added here.
- Parse `camera_presence`; display the outer-ring `camera_on` icon only for ON,
  remove it for OFF, and gate the board detector accordingly.
- Implement `presence_line` with the local leave/welcome/joke clip pools above
  and a one-line redo renderer. Author Cantonese clips at most 3 seconds.
- Emit photo/evolve/intro lifecycle events, covering first boot and cancellations;
  ticket 33 should suppress for the full photo interaction, not just upload.
- No firmware was flashed by this ticket.

## 42b: device metadata hits and readiness

```json
{"type":"xiaoya","event":"presence_hit","confidence":0.7}
{"type":"xiaoya","event":"presence_status","running":true,"hits_enabled":false}
```

`presence_hit` accepts only type/event/confidence fields, a finite numeric
confidence in [0.5, 1.0], and never image fields. It calls `note_face()` locally;
any resulting welcome/redo action is drained by the existing presence poller.
Both events stop in the local ESP32 handler and never reach cloud handlers.

`presence_status` requires boolean running/hits_enabled fields. A failed probe,
or a working camera without an enabled detector, suppresses leave/welcome
policy with `camera_detector`. Every new device connection starts suppressed
until its status arrives. The firmware camera badge can light after a successful
OV5640 probe while this policy remains suppressed. Parent/session ON remains a
request, not proof of a running detector. Existing UI phase suppression remains.

The experimental firmware heuristic is OFF by default. It samples tiny grayscale
frames locally at <=1 Hz only while presence is requested and photo UI is inactive.
It emits confidence 0.7 for a bounded mean/variance/mid-brightness band; a textured
empty desk can pass. **Heuristic only until face model.** Do not enable leave/
welcome acceptance until dad has checked both occupied and empty scenes live.
No presence frames are saved, uploaded, or made available through HTTP.
