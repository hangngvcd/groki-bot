<!-- SPDX-License-Identifier: MIT -->
# Gateway local mini-games (tickets 27/30)

Gateway owns unlocks and timing; games never use send_task or a gbot session.
One evolve commit grants one immediate round even when forms skip levels. A
second evolution during pending/active play grants no queued round. Rotation
G1 → G2 → G3 advances on grant, including waste. Parent pin_minigame overrides
the selected game; empty means rotation. Redo never reclaims a round.

Invites expire after 15 seconds. Restart abandons pending/active slots. Daily
reset clears play state and the per-game taught_today list. No later queue survives a decline, double press or expiry.

## Host → device

All frames have `type: xiaoya`, `cmd: minigame`:

| action | fields | rendering |
| --- | --- | --- |
| enter | game: G1/G2/G3, form: 1..4, teach: bool (optional) | local game mode; first-play local teaching VO |
| cue | none | anchor.cue + ring flash + beep |
| leaves | count: 0..3 | immediate leaves overlay |
| breathe | phase: in/out | body and accessories breathe |
| exit | reason: done/quit/decline | companion mode |

## Device → host

- `{"type":"xiaoya","event":"minigame_utterance"}` reports a mic utterance/energy event: G1 scores latency and G2 counts utterances. G3 ignores input and only breathes.
- `{"type":"xiaoya","event":"minigame_voice_exit"}` quits active play or declines a pending invite.
- `{"type":"xiaoya","event":"minigame_press","kind":"double"}` is the BOOT double-click exit backup. `event: boot_press` with `kind: double` also quits active play or declines pending invites.
- `minigame_press` and `boot_press` with `kind: short` are no-ops during active or pending games: they never score or decline. Device SoftAP long-press behavior is unchanged.

Voice exit is primary and local, without an LLM/gbot session. When local ASR text
exists, the pre-session hook full-matches 唔玩啦、退出、唔要玩、唔玩、不要玩、不玩了
after removing spaces and punctuation. Longer sentences do not match. Exit works
mid-way through all three games, including G3.

On first play of each game today the gateway sends `teach: true`, records that
game in persisted `taught_today`, and sends `teach: false` on subsequent starts.
Declining an invite does not mark it taught. Missing `teach` is allowed by the
contract. Prefer device local VO: G1「閃親就出聲」; G2「我響幾多下，你就叫幾多下」;
G3「跟我慢慢唞」. The gateway does not open a voice session for teaching.

## Local-loop integration boundary

Forms _commit returns game_unlock. homework_done exposes invite_spoken and a
local_game_handoff note. `begin_invite_after_evolve()` describes the handoff;
the local-loop owner must stop the voice session, wait for form animation,
then call `consume_invite_start()` immediately and send its messages through
forms Sender / send_device. Call `tick()` regularly, sending returned messages;
utterance and exit handlers also return messages. Speak returned `spoken` through local audio,
including G3's 「休息得好好」. Tests use a fake clock and a list sender.
G1 uses five rounds with a 2s miss timeout. G2 beeps every 0.6s, then accepts
utterances for 6s. G1 latency thresholds remain 0.4/0.8s; G2 awards 3 leaves
for an exact count, 2 for ±1, otherwise 0. G3 runs sixteen 4s phases (64s), without score.

**Firmware follow-up (not implemented or flashed in this branch):** parse
cmd:minigame; render face.overlay.game_* and anchor.cue; play sfx.game_go /
sfx.game_hit, invite and completion clips; breathe scale; report mic utterance/energy and local voice-exit events; distinguish BOOT short
and double while in minigame mode; play teaching VO when teach is true. Firmware must report animation completion;
a runtime local-loop owner must suspend voice/wake routing and drive tick and
local audio. The gateway handoff is a stub until that integration exists;
returning a tool note alone does not suspend a live voice session.
