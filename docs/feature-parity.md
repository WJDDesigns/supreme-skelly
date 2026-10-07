# Feature parity checklist

Everything the original Ultra Skelly Controller (v3.1 beta) does. Ticked items are implemented.

**Devices and connection**
- [x] Profiles: Ultra Skelly v2, Animated Skelly, 12ft Skelly, Lethal Lily, Ultra Santa (plus "unknown"). Each profile defines movements, light channels, light modes, eye effects and wake-tone timing.
- [x] BLE scan with countdown, ranked device picker, per-device nicknames, auto-connect, RSSI monitor, connection heartbeat, auto-reconnect.
- [x] Reads firmware version, volume, storage capacity, Bluetooth speaker name, PIN and live state. Can set the speaker name and PIN, and can factory-reset the device.
- [ ] Headless Kiosk Mode: on launch it auto-connects and turns on Live Mode and voice wake.

**Controls tab**
- [x] Movement buttons per profile (head, arms, torso, all; Lily has wrist/elbow; Santa differs), hold-to-repeat, and "all movements" toggle.
- [ ] Eyes: icon grid of eye effects per profile, an eye-byte calibration tool, and a picker for which eyes Live AI may use.
- [x] Lights: target selector (eyes, chest, lantern and so on per profile), modes (Static, Strobe, Pulsing, Flickering, Chasing, Sparkle, Rainbow), colour wheel, hex input, brightness, speed, lights on/off.
- [x] Volume, Live Mode toggle (the Skelly becomes a Bluetooth speaker), standalone Idle Mode (plays a random checked sound on a timer).

**Sounds tab (Sound Library)**
- [ ] Sync the on-device sound list; playlist with checkboxes, drag-to-reorder, per-sound delay, hide factory sounds, preview, delete, restore factory sounds.
- [x] Upload: import any audio file, loudness-normalise, encode to the Skelly's MP3 profile, chunked BLE upload with verification (spec: docs/sound-upload.md). Recording from the mic is still to do.
- [ ] Per-sound "Live Performance": which movement, eyes, light colour/mode/speed fire when that sound plays.
- [ ] Text-to-speech via ElevenLabs, then upload.
- [ ] Play playlist with start watchdog and progression driven by device events.

**ElevenLabs tab**
- [ ] API key, multiple saved agent profiles, verify agent, account/subscription status.
- [ ] Agent tool setup: creates or repairs `play_sound`, `fire_relay` and `describe_scene` tools on your agent, plus prompt guidance; a built-in prompt editor that publishes to the agent.

**Live AI tab**
- [ ] Real-time ElevenLabs conversation through the Skelly's speaker, with mic input and device pickers.
- [ ] Body automation while the AI speaks: head, arms, eyes and lights fire at random intervals within min/max seconds per part.
- [ ] Voice wake (mic level threshold) with wake tone, idle timeout, interrupt sensitivity, barge-in thresholds, cross-agent silence cap.
- [ ] Echo cancellation, realtime loudness normaliser, output boost.
- [ ] Voice Lab: pitch/"troll" effects with presets (Robot, Demon, Ghost and more) for live mic passthrough.
- [ ] Mirrored second speaker with sync-offset slider and its own volume.
- [ ] Record conversations (audio, optional camera video, muxed).
- [ ] Audio meter, transcript.

**Vision tab**
- [ ] Camera source: USB camera or RTSP stream; rotation, autofocus, zoom.
- [ ] Detects people, dogs, poses (wave/raised hand), hand gestures (thumbs up/down), shirt colour and shirt text (OCR), motion and position.
- [ ] Ignore zones drawn on the preview; performance tiers that disable heavy models on weak machines.
- [ ] Visitor memory, scene description sent to the AI as context, auto-start a conversation when someone walks up, presence-based idle actions.
- [ ] Optional GPU (CUDA) install and uninstall.

**Relays tab**
- [ ] USB HID and FTDI relay boards; named relays for fog machines, strobes and so on; test, all-off, pulse timing, cooldowns, max-on time; the AI can fire them.

**Activity Monitor and Activity Log**
- [ ] Live state dashboard, clock, log with optional routine-polling noise, diagnostics folder, send diagnostics.
