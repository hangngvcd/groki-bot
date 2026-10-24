# 15f: listening / talking / sleeping cue (鈴鈴, SpotPear 1.28" round)

| State | See | Hear |
|---|---|---|
| Wake (BOOT press or gateway wake word) | cyan ring (r 111-118, 7 px) breathing 70-100 % every 1.2 s; built-in face: sprout leaves perk up + tip twitches, eyes widen (x1.08 wide, x1.25 tall) | one soft two-tone "叮" (E6 then A6, 220 ms, peak 0.35 FS, 4 ms attack, 20 ms raised-cosine tail) at the user's volume |
| Listening after a reply (gate still open) | cyan ring + listening face again | nothing (no second chime) |
| Thinking (stt) | dim cyan ring | - |
| Speaking | blue ring; built-in face: drawn mouth opens with the audio level; PNG faces: whole face bobs up 0-5 px with the audio level (no mouth overlay, chosen so packs need no mouth art) | the reply |
| Idle / Sleepy | no ring; idle -> sleepy decay as before | - |

## How listening start is signalled
The gateway sends no `listen` message on wake. The firmware uses the status-LED
messages it already sends over ws_direct (`listen_cue.hpp`):
cyan (0,180,180) = gate open/listening, blue (0,80,180) = TTS, (0,0,0) = gate
closed, red = session dead. Cyan after off = fresh wake -> chime; cyan after a
reply = back to listening, no chime. A local BOOT wake chimes immediately and
marks the gate open so the gateway's following cyan does not chime again.

## Why the chime can't be heard as speech
Half-duplex like `play_local_clip`: `M5.Mic.end()` before the first sample,
`enter_listening()` (Speaker.end + 20 ms settle + mic re-prime) after the last.
The mic is off ~260 ms; the chime never enters the uplink. Synthesised in code
into the existing internal-RAM segment buffers: no allocation, no flash/NVS.

## Gateway
Firmware hello advertises `features.local_wake_chime=true` (SpotPear + companion
face only). The gateway then skips its own TTS wake beep. Old gateway + new
firmware = two beeps (device "叮" then the gateway's 880 Hz beep) until redeployed.
