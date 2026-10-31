# voxora-voice-runtime

Pure-logic building blocks for the Voxora Studio real-time voice pipeline.

**Status: honest scope.** This crate contains deterministic, fully tested logic
for turn-taking, scripted bridge orchestration, and WAV inspection. It does
**not** yet connect to a real media gateway (RTP/WebSocket audio). Anything
that is not implemented is explicitly not implemented; see the roadmap in
`docs/roadmap.md`.

## Modules

- `turn` — deterministic turn-taking state machine between a user and an agent.
- `bridge` — scripted call bridge that produces ordered telephony-style events
  with simulated latency, mirroring the Python mock telephony adapter.
- `wav` — minimal PCM16 WAV header inspection (sample rate, channels, duration).

## Contract notes

The bridge emits the same event vocabulary the Python orchestrator stores per
call: `dialing`, `ringing`, `answered`, `turn_started`, `turn_completed`,
`ended`. Latency stages match the orchestrator schema: `audio_received`,
`speech_endpoint`, `stt_result`, `agent_response`, `tts_first_audio`,
`first_audio_transmitted`, `turn_completed`.
