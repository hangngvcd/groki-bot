<!-- SPDX-License-Identifier: MIT -->
# Gateway local mini-games (tickets 27/30)

Gateway owns unlocks and timing; games never use send_task or a gbot session.
One evolve commit grants one immediate round even when forms skip levels. A
second evolution during pending/active play grants no queued round. Rotation
G1 → G2 → G3 advances on grant, including waste. Parent pin_minigame overrides
the selected game; empty means rotation. Redo never reclaims a round.

Invites expire after 15 seconds. Restart abandons pending/active slots. Daily
reset clears play state. No later queue survives a decline, double press or expiry.

## Host → device

All frames have `type: xiaoya`, `cmd: minigame`:

| action | fields | rendering |
| --- | --- | --- |
| enter | game: G1/G2/G3, form: 1..4 | local game mode |
| cue | none | anchor.cue + ring flash + beep |
| leaves | count: 0..3 | immediate leaves overlay |
| breathe | phase: in/out | body and accessories breathe |
| exit | reason: done/quit/decline | companion mode |

## Device → host

`{"type":"xiaoya","event":"minigame_press","kind":"short"}`
(or `kind: double`). `event: boot_press` with the same kind is also routed
while a game is active. Other xiaoya events retain their existing routing.
Short presses are game input; double presses abandon with no replacement.

## Local-loop integration boundary

Forms _commit returns game_unlock. homework_done exposes invite_spoken and a
local_game_handoff note. `begin_invite_after_evolve()` describes the handoff;
the local-loop owner must stop the voice session, wait for form animation,
then call `consume_invite_start()` immediately and send its messages through
forms Sender / send_device. Call `tick()` regularly, sending returned messages;
press methods also return messages. Speak returned `spoken` through local audio,
including G3's 「休息得好好」. Tests use a fake clock and a list sender.
G1 uses five rounds with a 2s miss timeout. G2 beeps every 0.6s, then accepts
presses for 6s. G3 runs sixteen 4s phases (64s), without score.

**Firmware follow-up (not implemented or flashed in this branch):** parse
cmd:minigame; render face.overlay.game_* and anchor.cue; play sfx.game_go /
sfx.game_hit, invite and completion clips; breathe scale; distinguish BOOT short
and double while in minigame mode. Firmware must report animation completion;
a runtime local-loop owner must suspend voice/wake routing and drive tick and
local audio. The gateway handoff is a stub until that integration exists;
returning a tool note alone does not suspend a live voice session.
