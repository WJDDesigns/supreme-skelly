# Skelly BLE protocol notes

GATT service `0000ae00-…`, write `0000ae01-…` (write without response),
notify `0000ae02-…`.

Commands: `AA <cmd> <payload padded to ≥ 8 bytes> <crc8>` where crc8 is
CRC-8/MAXIM (reflected poly 0x8C, init 0). Replies start `BB <cmd>`; `FE DC … EF`
is a keepalive.

Writes have no ack, and back-to-back writes can be dropped, so the service
spaces them 40 ms apart.

| Cmd | Purpose | Payload |
|---|---|---|
| E0 | Query params | → channels[6], PIN[4], "wifi"[8], show mode, …, name len, name |
| E1 | Query live state | → movement, 6×light[7], eye |
| E5 | Query volume | → volume |
| E6 | Query speaker name | → len, ASCII |
| EE | Query firmware | → version byte (e.g. 0x44 = v68) |
| D0 | List files | → one reply per file: serial[2], cluster[4], total[2], 2 pad, movement, 6×light[7], eye, db pos, …, UTF-16LE name from byte 59 |
| D1 | Play order | → count, serial[2]… |
| D2 | Capacity | → free KB[4], file count, mode |
| CA | Movement | mask, 00, cluster[4], filename ref |
| F9 | Eye | icon, 00, cluster[4], filename ref |
| F2 / F3 / F6 | Light mode / brightness / speed | channel (FF = all), value, cluster[4], filename ref |
| F4 | Colour | channel, R, G, B, cycle, cluster[4], filename ref |
| FA | Volume | 0–255 |
| FB | PIN + speaker name | PIN[4 ASCII], 8 zero bytes, name len, ASCII name |
| FD | Live Mode (Classic BT speaker) | 01 |
| C0–C5 | Upload: start, chunk, end, confirm, cancel, resume | |
| C6 | Play/stop file | serial[2], 01/00 |
| C7 | Delete | serial[2], cluster[4] |
| C8 | Factory reset (demo mode; hold Skelly's button 7 s after) | |
| C9 | Set play order | total, position, serial[2], filename ref |

A filename ref is `len, 5C 55, UTF-16LE name` (or `00` for "live, no file").
Upload commands C0/C3 use the bare `5C 55 …` without the length byte.
Filenames over 33 characters upload "successfully" but never appear.

Light speed on the wire is inverted: 0 (and 255) fastest, 254 slowest. The UI
uses 0 = slowest, 254 = fastest.

## Per-device differences

See `server/skelly/profiles.py`. Highlights:

- Lethal Lily movement bits: wrist 0x01, elbow 0x02, head 0x10, eyes 0x20.
- Ultra Santa movement bits: eyes 0x01, head 0x02, torso 0x04, arms 0x08; light
  mode 1 actually flickers, mode 4 is static.
- 12ft Skelly: head only (0x02), one chest channel, and the official app's eye
  labels are wrong; the order in the profile was verified on hardware.
