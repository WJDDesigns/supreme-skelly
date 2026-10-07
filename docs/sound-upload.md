# Ultra Skelly Controller: upload / sound-management spec (reverse-engineered)

Source: CPython 3.13 bytecode of `skelly_protocol.pyc`, `skelly_bluetooth.pyc`,
`skelly_audio_tones.pyc`, `ultra_skelly_controller.pyc` (disassembled with
python3.13 `dis`; helper scripts were kept out of the repo). Tags:
**[BC]** = confirmed from bytecode (constants and control flow read directly),
**[BC+run]** = also byte-compared by running the original pure builder against ours
(`cmp.py`), **[INF]** = inferred.

Notation: `fn16le(name)` = UTF-16LE of the device filename. The device filename is
`name.strip().lstrip("\\")`, with `.mp3` appended if it is missing (case-insensitive check)
(`device_recording_name`) [BC]. CRC = CRC-8/MAXIM (reflected 0x8C, init 0) over `AA..payload` [BC].
The original's `packet()` does NOT pad. Each builder pads (or not) on its own, as noted per packet [BC].

---------------------------------------------------------------------------------------
## 1. Upload sequence (`SkellyBluetooth.upload_recording`)

### 1.1 Packets
| Cmd | Original builder | Exact bytes | Tag |
|---|---|---|---|
| C0 | `recording_start_packet(size, chunk_count, name)` | `AA C0 size(4,BE) count(2,BE) 5C 55 fn16le CRC`. No length byte, no padding. size 0..2^32-1, count 0..65535 | [BC+run] |
| C1 | `recording_chunk_packet(index, chunk)` | `AA C1 index(2,BE) data CRC`. **Not padded**, so the final chunk can be shorter than 6 bytes | [BC+run] |
| C2 | `recording_finish_packet()` | `AA C2 00x8 CRC` | [BC+run] |
| C3 | `recording_confirm_packet(name)` | `AA C3 5C 55 fn16le CRC`. No length byte | [BC+run] |
| C4/C5 | none | Never built or sent. Resume works through the C0 reply instead (see 1.4) | [BC] (constants 196/197 absent) |

All writes use GATT write-without-response (`write_gatt_char(..., response=False)`) on AE01,
wrapped in a 10 s `wait_for`. A timeout raises "BLE write timed out" [BC].

### 1.2 Chunk size [BC]
`chunk_size = max(20, min(239, client.mtu_size - 3 - 5))` (bytes of MP3 data per C1).
With the usual MTU 247 this gives **239 data bytes**, so a C1 frame is 244 bytes, which equals the ATT payload.
`chunk_count = ceil(len(mp3)/chunk_size)`, and it raises an error if the count exceeds 65535. All C1 frames are prebuilt
in a dict `index -> frame` before sending. Index is 0-based.

### 1.3 Ordering, pacing, replies [BC]
The whole upload runs under `command_lock` (no other BLE commands are interleaved).
1. **Pre-check D2**: arm a waiter for `BB D2` (4.0 s), send `AA D2`, then await it. If
   `last_files_reported` (D2 byte `data[6]` = payload[4]) is **>= FILES_REPORTED_UPLOAD_HARD_STOP (30)**
   and `FILES_REPORTED_UPLOAD_HARD_STOP_ENFORCED` (True), abort with status "Upload Blocked". The
   log says the firmware corrupts custom metadata once that counter reaches 30, and that only a factory reset clears it.
2. **C0**: arm a waiter for `BB C0` with **10.0 s** timeout *before* writing, then write C0.
   - No reply: RuntimeError "Timeout waiting for transfer start acknowledgement." + `DEMO_MODE_UPLOAD_TIMEOUT_HINT`.
   - `failed = data[2]` (payload[0]; defaults to 1 if the reply is short). Non-zero raises "Device rejected transfer start."
   - `written = int.from_bytes(data[3:7], BE)` (payload[1:5]). **Resume**: `start_index = min(count, written // chunk_size)`.
     The send starts at that chunk (it logs "reference add-file resume" when the value is non-zero).
3. **C1 loop** from `start_index` to `count-1`: check that the link is still connected, write the frame, then **`sleep(0.111)`**
   (111 ms fixed pacing). **No reply is awaited per chunk**, and `BB C1` notifications are ignored
   (they are only stored in the generic response map). Progress = round((i+1)*100/count).
4. **C2**: arm a waiter for `BB C2` with a **240.0 s** timeout, then write C2.
   - No reply: RuntimeError "Timeout waiting for transfer end acknowledgement." + hint.
   - `failed = data[2]`. If non-zero: `tail = clamp(int.from_bytes(data[3:5],BE), 0, count)`, and
     **every chunk from `tail` to `count-1` is resent**, with **`sleep(0.012)`** (12 ms) between them. C2 is
     **not** resent afterwards and nothing is re-checked. The code moves straight on to C3.
5. **C3**: arm a waiter for `BB C3` with a **10.0 s** timeout, then write C3. No reply raises "Timeout waiting for confirm
   transfer acknowledgement." + hint. `data[2] != 0` raises "Device failed to confirm transfer."
   Log: "C0 -> C1 -> C2 -> C3 acknowledged using 239-byte chunks, 111 ms pacing, and 12 ms tail-resend pacing."
6. **Verification** (still under the lock). `verify_uploaded_record` runs a full D0/D1 refresh (`_request_device_sounds`),
   finds the D0 row whose filename equals the device filename, and reads its metadata. Row is *ready* unless
   `db(data[57])==255 and length(data[10:12])==0 and action(data[12])==0` (a placeholder row).
   - Phase A: sleep 2.0 s, then verify once.
   - Phase B (still a placeholder): status "Waiting for File Finalize", sleep 10.0 s, verify.
   - Phase C: disconnect, sleep 1.5 s, reconnect to the same device, sleep 3.0 s, verify.
   - Still not ready: status "Upload Incomplete". The row stays visible as INCOMPLETE, can be deleted,
     but cannot be configured or played. Ready: status "Upload Verified". The function returns the metadata
     plus `_local_duration_seconds` (from the MP3 frame count).
7. **After** (controller `upload_done`): send `request_capacity` (D2), store the duration keyed by serial, and
   re-render the playlist. A failure is logged as "Upload to Skelly FAILED: ...".

`_wait_for_protocol_response(cmd, timeout)`: the notification handler stores the latest `BB <cmd>` frame
(whole frame, so `data[2]` = first payload byte) and sets an asyncio Event [BC].

### 1.4 Things NOT done around upload [BC]
- No live-mode disable, no pause/stop of playback, no C4 cancel on error (an exception just
  releases the lock and sets status "Upload Error"). C5 is never used.
- `upload_recording` accepts movement_mask/light_target/light_mode/light_rgb/brightness/speed/eye_slot
  arguments but **never uses them**. No performance packets are sent during upload.
- Pre-upload, the original only validates the MP3: `inspect_skelly_mp3_profile` runs and is logged, and its failure
  is logged and ignored. A file that is missing or empty causes an abort.

### 1.5 Constants
| Name | Value | Tag |
|---|---|---|
| DEMO_MODE_UPLOAD_TIMEOUT_HINT | " If Skelly was recently factory reset, it may still be in demo mode -- hold the physical button on Skelly for 7 seconds to exit demo mode, then try again." (appended to every C0/C2/C3 timeout error) | [BC] |
| FILES_REPORTED_UPLOAD_HARD_STOP | 30 (D2 `files_reported`, payload[4]) | [BC] |
| FILES_REPORTED_UPLOAD_HARD_STOP_ENFORCED | True | [BC] |
| FILES_REPORTED_DEVICE_CEILING (controller) | 30 | [BC] |
| RECORDING_MANAGEMENT_ENABLED | True (if False, the upload button shows "temporarily disabled") | [BC] |
| C0 / C2 / C3 / D2 reply timeouts | 10 s / 240 s / 10 s / 4 s | [BC] |
| C1 pacing / tail-resend pacing | 111 ms / 12 ms | [BC] |

---------------------------------------------------------------------------------------
## 2. Audio preparation

### 2.1 Decode (imports) [BC]
`choose_recording_file` accepts `*.mp3 *.wav *.flac *.ogg *.aiff *.aif`. **Every** file, even an MP3,
is re-encoded. A subprocess (`--decode-audio`, 180 s timeout) uses `pedalboard.io.AudioFile` to dump float32
PCM plus `meta.json`. Files longer than **MAX_RECORDING_SECONDS = 300.0 s** are rejected ("too_long"), and
empty files are rejected too. The float data is converted with `clip(x*32767, -32768, 32767)` to int16 and passed to `_encode_effect_recording_mp3`.
No ffmpeg is involved anywhere in the upload path. [BC]

### 2.2 `_encode_effect_recording_mp3(pcm, sr, ch, bitrate_kbps=UPLOAD_BITRATE_KBPS, apply_auto_normalize=True)` [BC]
1. Down-mix: if there are multiple channels, keep **the single channel with the highest RMS** (no averaging).
2. Resample to **44100 Hz** by **linear interpolation**: `n_out = max(1, round(n*44100/sr))`, positions from
   `linspace(0, n-1, n_out)`.
3. Remove DC: subtract the mean.
4. Loudness normalisation (when apply is True and rms>1 and peak>1):
   `gain = min(10.0, UPLOAD_TARGET_RMS/rms, 31100/peak)` with **UPLOAD_TARGET_RMS = 14400.0**
   (approximately -7.1 dBFS RMS [INF calc]), peak ceiling **31100** (approximately -0.4 dBFS), max gain x10 (+20 dB). The gain can
   also attenuate.
5. Soft clip, **always applied**: `x = 31100*tanh(x/31100)`.
6. **Wake tone prepended** (see 2.3), then clip to int16.
7. Encode with **lameenc**: `set_bit_rate(64)`, in/out rate 44100, `set_channels(1)`, `set_quality(2)`.
   That gives **MPEG-1 Layer III, 44.1 kHz, mono, 64 kbps CBR** (lameenc defaults to CBR [INF]).
   **UPLOAD_BITRATE_KBPS = 64** [BC]. Note: the log and validation strings still say "32 kbps". That text is stale,
   and validation uses the 64 that is passed in.
8. Write the file to the temp recordings dir as `<sanitized>.mp3`. If the name exists, add `_2`, `_3`, and so on. Then run
   `validate_skelly_mp3_profile(path, expected_bitrate_kbps=64)`.
- ID3: lameenc writes no ID3 tags [INF]. The validator tolerates an ID3v2 header: it skips `10+syncsafe(size)` bytes,
  plus 10 more if the footer flag (byte 5 & 0x10) is set [BC]. Nothing strips tags explicitly.

### 2.3 Wake tone (`skelly_audio_tones.generate_speaker_wake_tone_samples(sr, ms)`) [BC]
- Duration: `SPEAKER_WAKE_TONE_MS = 450` from skelly_audio_tones. The controller assigns 250 to the same name first,
  but the later import overrides it, so the effective value is **450** [BC]. `DeviceProfile.wake_tone_ms` overrides it:
  **Ultra Santa = 125**, all others None, so they use 450. The UI toggle `speaker_wake_tone_enabled` (default **True**)
  set to off gives 0 ms, meaning no tone.
- Signal: `f(t) = 170 + 18*sin(2*pi*5*t)` Hz (170 Hz carrier with a ±18 Hz, 5 Hz wobble), phase =
  `2*pi*cumsum(f)/sr`, amplitude **900** int16 (approximately -31 dBFS peak [INF calc]).
- Envelope: linear fade-in and fade-out of `max(1, int(0.05*sr))` samples (50 ms) each.
- It is **prepended** to the processed voice, after normalisation and soft clip, so it is not normalised.

### 2.4 Profile inspection (`inspect_skelly_mp3_profile`) [BC]
Built-in parser. It skips ID3v2, then scans for sync `hdr & 0xFFE00000`. Before the first frame it accepts only
MPEG-1 (version 3) Layer III (layer 1). Once frames are found it raises on a version or layer change, on a reserved or free-format
bitrate, or when `(sr, channels, bitrate)` differs from the first frame. In practice that means **VBR is rejected**.
Frame length = `144*bitrate//sr + padding`. Channels = 1 if mode==3, else 2. Duration = `frames*1152/sr`.
It returns `(sr, ch, bps, duration)`. `validate_skelly_mp3_profile(path, kbps=32 default)` requires sr==44100,
ch==1, |bitrate - kbps*1000| <= 2000.

### 2.5 Filename rules [BC]
- `sanitize_skelly_upload_name(raw)`: strip, drop a trailing `.mp3`, remove everything outside `[A-Za-z0-9_-]`, and
  truncate to `MAX_SAFE_DEVICE_NAME_LENGTH - 4` = **16 chars**. If empty, fall back to `Rec-` + 6 random `[0-9a-z]`.
- `MAX_SAFE_DEVICE_NAME_LENGTH = 20` **including `.mp3`**. The error text says longer names that share a prefix
  with another recording can corrupt the device's sound metadata. Other checks: no `\/:*?"<>|`,
  and UTF-16LE length <= 240 bytes. `.mp3` is auto-appended.

---------------------------------------------------------------------------------------
## 3. Per-sound "Live Performance" (`linked_existing_*`, `save_sound_performance`)

### 3.1 Layout (`linked_existing_file_packet(cmd, data, cluster, name)`) [BC+run]
`AA cmd data cluster(4,BE) L 5C 55 fn16le CRC`, where `L = len(5C 55 + fn16le)` (one byte, <= 255).
The whole payload is padded to 8 bytes if shorter (it never is in practice). The docstring cites a real btsnoop capture
(2026-08-22) proving the `L` byte. Its absence in the web-controller reference misaligned every field.
| Cmd | data bytes |
|---|---|
| CA movement | `mask&FF, 00` (mask passed through `device_movement_value`, which only truncates to a byte; Lethal Lily uses bits 0x10/0x20) |
| F9 eye | `slot, 00` |
| F3 brightness | `target, value` |
| F2 mode | `target, mode` |
| F6 speed | `target, value` (raw) |
| F4 color | `target, R, G, B, 00` (cycle byte always 0) |
Target: chest = **0**, mouth = **1** (in `save_sound_performance`). 255 = all in other contexts.
The sound is addressed by **cluster (from D0 data[4:8]) + device filename**. If cluster <= 0, the save is refused.

### 3.2 Send order and pacing [BC]
Under the command lock: movement, eye, mouth brightness, mouth mode, [mouth speed if mode!=1], chest
brightness, chest mode, [chest speed if mode!=1], mouth color, chest color. Each is followed by
**sleep 0.35 s**, with no replies awaited. Then sleep 0.2 s and run a full D0/D1 refresh.

### 3.3 Comparison with protocol.py
`movement/eye/light_mode/brightness/speed/color(…, cluster, filename)` produce **identical bytes**
(verified for CA, F9, F3, F2, F6, F4, and channel -1 gives FF). Differences:
- Ours does not auto-append `.mp3`. Passing a bare stem yields a different (wrong) name/L byte (verified DIFF).
- The original never sends cycle=1 in per-file F4. Ours allows it (behaviour unverified).
- The older `linked_*_packet` (not "existing") variant uses a different capture format: `AA cmd …7-byte cmd… (len+6) len 5C fn16le`
  with only a single 0x5C. It is not imported or used by the bluetooth layer. Ignore it.

---------------------------------------------------------------------------------------
## 4. Playlist (C9), order query (D1), delete (C7)

### 4.1 D1 request: `sound_order_request_packet` = `AA D1 00x8 CRC` [BC+run]. Matches `query(0xD1)`.
D1 **reply parsing in the original** [BC]: `count = int.from_bytes(data[2:4], "little")`, then 16-bit
**little-endian** words from `data[4:-1]`. The original labels these "diagnostic only".

### 4.2 C9: `sound_playlist_entry_packet(total, position, serial, name)` [BC+run]
`AA C9 total(1) position(1) serial(2,BE) 5C 55 fn16le CRC`. **No length byte**, no padding.
Constraints: 1<=total<=255, 1<=position<=total (**1-based**), serial clamped 0..65535. The docstring says
it is built "exactly like SkellyUltraWebController.updateFileOrder()" (not capture-verified).
Usage: **`apply_sound_playlist` is hard-disabled** ("this firmware corrupts custom sound metadata":
rewriting C9 turned valid D0 rows into metadata_id=255 placeholders). Playback order is kept locally and
individual C6 commands are used. The only C9 use left is after a delete (4.3).

### 4.3 C7: `sound_delete_packet(serial, cluster)` [BC+run]
`AA C7 serial(2,BE) cluster(4,BE) 00 00 CRC` (padded to 8). serial = D0 serial (data[2:4]),
cluster = D0 data[4:8] (the "delete token"). Factory sounds are refused client-side.
Flow [BC]: take the lock (5 s timeout), D2 baseline (4 s), arm the `BB C7` wait (6 s), write C7 (5 s write timeout).
The reply is OK if `data[2]==0`. Then sleep 0.45 s, refresh D0, and check the filename is gone. Next comes a **C9 commit**:
one C9 per surviving entry, `total = number of survivors`, positions 1..N, **80 ms** apart. Survivors are
the UI's remaining playlist, filtered to serials that still exist in D0, falling back to the D1 words. Then send D1
(5 s wait), sleep 0.25, sleep 0.4, refresh D0 again, and send D2 to log the files_reported delta.

### 4.4 Comparison with protocol.py
- `delete_file(serial, cluster)`: **identical** [BC+run].
- `set_order(total, position, serial, filename)`: **MISMATCH**. Ours inserts a length byte
  (`filename_ref(with_length=True)`) before `5C 55`. The original has none. (The original copied the web
  controller, which was also wrong for the linked_existing packets, so the truth is unproven. Treat C9 as dangerous
  per 4.2 either way.)

---------------------------------------------------------------------------------------
## 5. Other mismatches spotted in protocol.py (parse side)
- `parse` D1: ours reads count = payload[0] and BE serials from payload[1:]. Original: LE count in payload[0:2],
  LE words from payload[2:]. [BC]
- `parse` D0 `db_pos = u(54,55)` (= raw[56]). The original's `db`/metadata_id = raw[**57**]. `eye` raw[55] matches.
  The original also reads `length = raw[10:12]` (ours ignores it; it is needed for the placeholder/"ready" check). [BC]
- C0/C2 reply parsing (`failed`, `written` = payload[1:5]; `last_index` = payload[1:3]) matches the original. [BC]
- `transfer_chunk` pads a final chunk shorter than 6 bytes with zeros (an extra MP3 byte count). The original never pads. [BC+run]
- `MAX_FILENAME_LENGTH = 33` vs the original's 20 including `.mp3` (16-char stem). Ours also does not append `.mp3`. [BC]
