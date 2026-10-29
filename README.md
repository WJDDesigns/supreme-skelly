# Supreme Skelly 💀

Bring your Home Depot Skelly to life from your phone. Supreme Skelly runs on a small
always-on computer next to your animatronic and gives you a web page where you can move
him, change his eyes and lights, play sounds, have him **talk with visitors using AI**,
**greet people he sees on camera**, and **sing and dance to your playlists**.

Works with the Skelly-family Bluetooth animatronics: Ultra Skelly v2, Animated Skelly,
12 ft Skelly, Lethal Lily and Ultra Santa.

<p align="center">
  <img src="docs/images/controls.png" alt="The Controls page: a live drawing of Skelly and one-tap looks" width="72%">
  &nbsp;
  <img src="docs/images/phone.png" alt="Supreme Skelly on a phone" width="22%">
</p>

## Every model is its own character

Supreme Skelly notices which animatronic it's talking to and becomes that character: the live
preview draws it, and until you write your own personality it introduces itself, greets
visitors and calls people over as that character.

<p align="center">
  <img src="docs/images/character-skelly.png" alt="Skelly in the live preview" width="30%">
  <img src="docs/images/character-lily.png" alt="Lethal Lily, a witch with a glowing lantern" width="30%">
  <img src="docs/images/character-santa.png" alt="Ultra Santa" width="30%">
</p>

- **Skelly** (Ultra Skelly v2, Animated Skelly, 12 ft Skelly): a spooky, punny skeleton.
- **Lethal Lily**: a cackling witch who calls visitors "dearie". Her lantern glows in the drawing.
- **Ultra Santa**: a jolly Santa who counts down to Christmas instead of Halloween.

The moves, lights and eye choices on the Controls page always match the connected model.

## See what he's up to at a glance

The card at the bottom of the sidebar shows what Skelly is doing right now: **Chatting**,
**Listening**, **Thinking**, **Singing**, **Performing** a sound, **Fogging**, **Waiting** for
visitors (when Vision is on), **Someone's here**, **Resting**, or **Offline**. It updates live,
with a little equalizer while he talks or sings, and still tells you whether Live Mode is on.

<p align="center">
  <img src="docs/images/live-status.png" alt="The sidebar status card: Chatting, Listening, Singing, Waiting and Resting" width="90%">
</p>


- **Your Skelly** (switched on, within Bluetooth range).
- **A small Linux computer** that stays on: any mini PC (an Intel N100 box is plenty) or a
  spare laptop running **Ubuntu Server 24.04** or **Debian 12**. It needs Bluetooth; a USB
  Bluetooth dongle works if it has none.
- Optional, for conversations: a **USB microphone** near Skelly and an account with one of
  the AI services below.
- Optional, for Vision: a **USB webcam**, any camera with an RTSP stream, or **UniFi Protect**.
- Optional, for fog: a **Wi-Fi relay** (Shelly, Sonoff in LAN mode, Tasmota or ESPHome) wired
  across the fog button on your fog machine's remote.

### Hardware we've used that works

This is the setup the project is built and tested on every day. Other hardware should work too.

| What | We use | Notes |
|---|---|---|
| Animatronic | Home Depot **6 ft Ultra Skelly v2** | Connects and reconnects on its own. Lily, Santa and the 12 ft Skelly are supported but not tested on real hardware yet. |
| Mini PC | **Beelink Mini S12 Pro** (Intel N100) with Ubuntu Server 24.04 | Runs everything, including face recognition on one camera, without breaking a sweat. |
| Bluetooth | **Realtek RTL8761B USB Bluetooth dongle** | Stronger signal than the mini PC's built-in radio. Pick it in Settings > Bluetooth radio. |
| Getting the radio close to Skelly | **USB-over-Ethernet extender** (4 USB 2.0 ports, up to 50 m over one Cat5/6 cable, no drivers) | Puts the Bluetooth dongle and mic right next to Skelly while the mini PC stays indoors. If the dongle freezes, turn off USB power saving (see Settings for tinkerers). |
| Microphone | A plain **USB microphone** on the extender | |
| Speaker | **Skelly's own Bluetooth speaker** | Paired automatically. Extra Bluetooth speakers can play along. |
| Cameras | **UniFi Protect** cameras on a UniFi NVR | No live video decoding needed: Protect's own person detection and snapshots drive Vision. |
| Fog *(being tested)* | **AGPTEK LED-500** fog machine + **Shelly Plus Uni** relay on a 12 V adapter | The relay is wired across the fog button on the machine's remote. See the [fog guide](docs/fog.md). |

## Quick start (about 10 minutes)

1. On the mini PC, open a terminal (or SSH in) and paste this one line:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/WJDDesigns/supreme-skelly/main/get.sh | bash
   ```

   It installs everything it needs (Bluetooth, sound, Docker), then starts Supreme Skelly.
   It will ask for your password once so it can install things.

2. When it finishes it prints an address like `http://my-mini-pc.local`. Open it on your
   phone or computer (same Wi-Fi).

3. The first time, it asks you to **pick a password** for the page and confirms your
   **time zone**. That's it.

4. **Switch Skelly on.** He's found and connected automatically, and everything comes
   back on its own after a power cut or reboot.

**Updates are automatic.** The mini PC checks for a new version every 15 minutes and installs
it by itself, keeping your settings, keys and faces. The version you're on is shown at the
bottom of the page and in **Settings > System**, where you can also tap **Update now** or switch
automatic updates off. (Running the install line again also updates.)

## Making him talk

Open **Conversation** and pick an "AI brain". You can switch any time:

| Option | You need | Notes |
|---|---|---|
| ElevenLabs agent | ElevenLabs key | Most natural; uses ElevenLabs credits |
| OpenAI Realtime | OpenAI key | Fast, one service does everything |
| Claude | Anthropic key + an ElevenLabs or OpenAI key for voice | Cheapest per chat; short spoken replies |

Add keys under **Settings > API keys**. Each one has a link and a one-line "how to get it".
Keys never leave the mini PC, and the page only ever shows their last 4 characters.
**Settings > Usage** shows what each night cost, and **Quiet hours** keeps him from chatting
at night. Conversations always end after a few minutes so nothing can run up a bill, and a
chat Skelly started himself ends shortly after he stops talking if nobody answers. When a
chat ends, he always finishes his last sentence, and if the visitor's last words (a "bye" or
"I love you") haven't been answered yet, he answers them warmly before going quiet. If your
ElevenLabs credits run out, he says so and carries on with OpenAI's voice and hearing.

<img src="docs/images/conversation.png" alt="Chat bubbles with the time and a camera picture on each" width="360" align="right">

Every chat bubble shows **when it was said and what the camera saw at that moment**, so you can
look back and see why he said what he did. Tap a picture to see it big. Pictures stay on the
mini PC and are deleted after a week. Saved recordings show the same bubbles.

**Settings > Sound** picks the mic, Skelly's speaker and any extra Bluetooth speakers, with level
meters and volume caps. **Keep speakers awake** plays silence so Bluetooth speakers don't chime
each time he starts talking. When the mini PC has two Bluetooth radios, extra speakers pair on the
one Skelly isn't using, so music on them doesn't skip. Skelly's own speaker never shows up in the
scan, and an extra speaker that's off, out of range or reconnecting never stops Skelly's voice.
The extra speakers are kept for music: chats use Skelly's own speaker unless **Extra speakers when he
talks** is on, and the mini PC reconnects them about once a minute if they drop.
<br clear="right">

**Recordings** can be filtered by how much the visitor joined in: **Conversations** (they
talked back at least twice), **Quick hellos** (they said one thing) and **Ignored him** (he
said his line and they walked on). Each one shows how many times the visitor replied and
their first words. Coughs, wind and passing cars that speech-to-text turns into "thank you"
or "um" don't count as replies. Your choice of filter is remembered.

Each recording has a small picture from its video on the left, with its length. Tap the
picture or **Play** and the video opens right under that recording, with the chat bubbles
below it, so you stay where you were in the list. With UniFi Protect, recordings use the camera's
Medium stream: the 4K High stream arrives too unevenly over Wi-Fi and played back choppy.

The picture and the sound are recorded separately and joined, lined up, when the chat ends.
Recording them together let the sound hold the camera back, so videos came out in slow
motion: a few seconds of picture stretched over a minute, then a frozen frame while the chat
carried on. If the mini PC restarts mid-chat, the pieces are joined the next time it starts.

<img src="docs/images/recording-split.svg" alt="Before: one recorder held the camera back behind the sound, so 8 seconds of picture stretched over 76 and froze. Now picture and sound are recorded apart and joined in step." width="720">

<img src="docs/images/recordings-filter.png" alt="Recordings list with filter chips, a picture and reply count on each recording, and the video playing right under the one that was tapped" width="560">

### He learns from his chats

Every chat is logged on the mini PC with who the camera saw and how it went (did they answer,
how long they stayed). Once a night, Claude reads the new chats and keeps a short list of
tips: openers that worked, questions kids love, things to stop doing. Skelly follows them in
every conversation. **What Skelly has learned** on the Conversation page shows the tips; edit
or delete any of them, add your own (yours are never dropped), or press **Review chats now**.
It needs an Anthropic key and costs about a cent or two a night. Chats older than 30 days are
deleted.

Whatever the tips say, Skelly keeps track of the questions he has asked in each chat. Once a
visitor answers one, he reacts to their answer and moves on instead of asking it again. He also keeps bone puns rare:
none in his opening line, and at most one a chat.

<img src="docs/images/learning.png" alt="What Skelly has learned: tips like ask kids a riddle early, and ask dog owners the dog's name, each editable, plus a box to add your own" width="720">

## Seeing visitors (Vision)

Open **Vision** and pick where the picture comes from: a USB webcam, an RTSP camera link,
or UniFi Protect (add a Protect API key in Settings and your console's address on the
Vision page, then tick which cameras count). Skelly can then notice people, call passers-by
over, and greet people by name once they've told him (or welcome them back by name next time).

**Check with AI** has an AI look at each picture to confirm it's a person and describe them,
for costume compliments. It costs a little per look. Switch it off for a free mode: with UniFi
Protect, Skelly then goes by UniFi's own person detection. **Recent sightings** lists everyone
the cameras spotted, with their picture and what Skelly did about it.

**Greetings made for them.** That one look also notices delivery drivers (and the company,
when the uniform or van shows it), how many grown-ups and kids there are, and what they're
doing: carrying a parcel, walking a dog, pushing a stroller. Skelly's first words are written
about them instead of a stock hello, and the whole chat knows the details. In the free mode
he takes one look only when he's about to start a chat. **Delivery drivers** lets you pick a
quick thank-you (the default, he won't hold them up), a normal chat, or leaving them be.

<img src="docs/images/personal-greetings.svg" alt="Before: every visitor got the same stock hello. Now a delivery driver, a family with a dog and two joggers each get a line about them." width="720">

Each sighting shows what made those visitors different:

<img src="docs/images/sightings-tags.png" alt="Recent sightings with chips such as Delivery: UPS, 2 adults, 1 kid, Walking a dog and Costume: witch" width="720">

**Answer when spoken to** (on by default) keeps Skelly listening between chats for two
minutes after a camera sees someone. A visitor who talks to him then gets an answer, even
if he just said goodbye or was resting between visitors. A chat he started himself waits
30 seconds for an answer, and stays open while voices are reaching his mic.

<img src="docs/images/answer-when-spoken-to.svg" alt="Before: Skelly gave up after 15 seconds and ignored kids talking to him. Now he keeps listening and answers." width="720">

**Special guests.** Want a big welcome for a neighbor or a friend? On the Vision page, press
**Add from a photo** under **People Skelly knows** and pick a clear picture of their face (a
profile picture works; add a few for better odds). Then press **Greeting** on their card and
write what Skelly should say the moment he recognises them. Put a few lines and he picks a
different one each visit; `{name}` becomes their first name. **About them** is kept in mind for
the rest of the chat, and **Fog when they arrive** gives them a puff from the fog machine. Anyone
Skelly has met can get a special greeting too, not just people added from photos.

<p align="center">
  <img src="docs/images/special-guests.png" alt="People Skelly knows: four people, one added from a photo with a Special greeting badge, and an Add from a photo button" width="56%">
  &nbsp;
  <img src="docs/images/special-greeting.png" alt="The Greeting window: two special greeting lines, notes about them, and Fog when they arrive switched on" width="40%">
</p>

A photo is a starting point: a camera at night sees a face differently than a bright profile
picture does. Each time Skelly does spot them, he keeps the new angle, so he gets surer with
every visit. If they tell him their name at the door, that face is added to the same person.

**Face memory stays on the mini PC:** faces are recognised on the mini PC itself and never
sent anywhere. You can rename or forget anyone, or everyone, on the Vision page. Recording
faces may be regulated where you live; check your local rules and consider a sign telling
visitors they're on camera.

## Watching and listening (Viewer)

Open **Viewer** to watch the camera live and hear the conversation as it happens: the
visitors through Skelly's mic and Skelly's own voice, each with its own volume slider and
mute button. Press **Listen** to start the sound. The sliders only change what you hear on
the phone or computer you're watching on; Skelly's speaker and mic stay as they are. The
words show up alongside as they're spoken, and the picture goes full screen with one tap.

<p align="center">
  <img src="docs/images/viewer.png" alt="The Viewer page: the live camera, sliders for the mic and Skelly's voice, and the words as they're spoken" width="68%">
  &nbsp;
  <img src="docs/images/viewer-phone.png" alt="The Viewer page on a phone" width="24%">
</p>

The picture is the camera shown on the Vision page. A USB or RTSP camera has to be running
there; a UniFi Protect camera streams smoothly here (about 10 pictures a second) even though
Vision only takes a snapshot from it every couple of seconds. Nothing extra runs on the mini
PC unless someone has the page open, and the sound only flows while someone is listening.

### Recordings

Turn on **Record conversations** on the Conversation page to save each chat as a video with
sound, played back next to its chat bubbles. The mic stands right beside Skelly, so it hears his
speaker too, about half a second after his voice leaves the mini PC (Bluetooth lag). Recording
both used to make him sound doubled. **Recording sound** now picks how it's mixed:

- **Clean** (the default): his voice straight from the mini PC, and the mic turned down while he
  talks and for his speaker's lag after, so he's heard once. Visitors are heard between his lines.
- **Room mic only**: just what the mic heard, like standing in the yard.

The lag is the one Skelly measures live while he talks, so it follows your speaker.

<img src="docs/images/recording-sound-setting.png" alt="The Recording sound picker on the Conversation page, set to Clean" width="420">

<img src="docs/images/recording-sound.svg" alt="Before: his voice plus a late echo through the mic sounded doubled. Now the mic dips while he talks, so he's heard once." width="720">

## Singing and dancing (Music)

The **Music** page makes Skelly sing. Paste a YouTube, Spotify or Apple Music link (a song,
album or playlist) or just type a song's name, build playlists, and press **Sing**. The mini
PC separates the singer from the band, so his mouth moves only with the words, finds every
beat so he dances in time, and can make his lights follow the music. The band plays on any
extra speakers you've added in Settings > Sound.

<p align="center">
  <img src="docs/images/music-page.png" alt="The Music page: now playing, adding songs, playlists" width="68%">
  &nbsp;
  <img src="docs/images/music-phone.png" alt="The Music page on a phone" width="24%">
</p>

![How a song becomes a performance](docs/images/music-flow.svg)

While he sings, Skelly's mic listens to his speaker and your other speakers and keeps them in
step (**Keep in sync while singing**), so the band never drifts away from his voice.

<img src="docs/images/live-sync.svg" alt="Skelly's mic hears his voice and the band, and the band is lined up with his voice" width="720">

The **[music guide](docs/music.md)** covers which links work, playlists, the show settings,
lining up the timing, and fixing skips or a clipped first word.

## Fog on cue

The **Fog** page drives a fog machine through a Wi-Fi relay on your network (no cloud).
Wire the relay's dry-contact output across the fog button on the machine's **battery
remote**, not across the machine's power: the heater has to stay on to stay warm, and the
remote's button is low voltage. Some wired remotes carry mains voltage; leave those alone.

Then pick the relay type and its IP address, tap **Check connection** and **Fog!**. Automatic
fog can fire when a visitor walks up, and can keep the fog topped up: pick which camera
watches the fog (the Vision camera or any UniFi Protect camera), draw a fog area on
the picture, tap **Calibrate** while there's no fog, and the fog meter shows how
thick it is. Topping up keeps going even while Skelly is switched off. Every automatic burst
respects a rest time, an hourly limit and quiet hours.

![The Fog page](docs/images/fog-page.png)

The **[fog guide](docs/fog.md)** has a wiring diagram for a Shelly Plus Uni and step-by-step setup.

## Make it yours

**Settings > Wallpaper** has wallpapers (graveyard, pumpkin patch, spider web and more) and a
see-through setting for the cards. The Device page shows today's AI use with a week graph,
and **Settings > Usage** breaks it down by night.

## Security

- **Password:** set on first run; change or turn it off in **Settings > Password and time zone**.
  Without one, anyone on your Wi-Fi can open the page.
- **Keep it on your home network.** Don't forward its port to the internet. For remote
  access use a VPN such as Tailscale or WireGuard.
- **Your data stays local:** settings, API keys (readable only by root), faces and
  recordings live in the `data` folder on the mini PC and nowhere else.
- Requests from other websites can't control Skelly, even while your browser has the page open.

**Forgot the password?** On the mini PC run:

```bash
sudo rm /opt/supreme-skelly/data/auth.json
```

The page then opens without a password and you can set a new one in Settings.

## Other ways to run it

**Windows (no Docker):** install [Python](https://www.python.org/downloads/) (tick "Add
python.exe to PATH"), download this project, then double-click `start-windows.bat`. It
opens the page at `http://localhost:8420`. Conversations and Vision work best on Linux.

**Already cloned it?** Run `./install.sh` from the folder.

**Try it without a Skelly:** set `SKELLY_SIMULATE=1` in `docker-compose.yml` for a fake one.

**Fresh mini PC from a USB stick (advanced, experimental):** `deploy/usb/make-usb-image.sh`
builds an Ubuntu installer that wipes the disk and sets everything up unattended. It asks
you for the mini PC's login password while building.

## Settings for tinkerers

These go in `docker-compose.yml` (or the environment when running natively).

| Setting | Default | |
|---|---|---|
| `SKELLY_PORT` | `8420` | Web page / API port |
| `SKELLY_EXTRA_PORTS` | `80` in Docker | More ports serving the same page |
| `SKELLY_SIMULATE` | `0` | `1` = fake device for testing |
| `SKELLY_AUTOCONNECT` | `1` | Find and connect to Skelly automatically |
| `SKELLY_BT_ADAPTER` | (auto) | Which Bluetooth radio, by MAC or `hci0`; also pickable in Settings > Bluetooth radio |
| `SKELLY_DATA_DIR` | `/data` in Docker | Where settings, keys and faces are kept |
| `SKELLY_LOG_LEVEL` | `INFO` | |

**Bluetooth through a USB-over-Ethernet extender** (to keep the radio close to Skelly)
works. If the dongle freezes after a few minutes, USB power saving is the usual cause: add
`usbcore.autosuspend=-1` to `GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub`, then run
`sudo update-grub` and reboot.

## Troubleshooting

- **Page won't open at `.local`:** use the IP address the installer printed instead. Some
  routers don't pass `.local` names between networks.
- **Skelly isn't found:** make sure he's on and not connected to the phone app, then press
  Scan on the Device page. With two Bluetooth radios, pick the right one in Settings > Bluetooth radio.
- **No sound from Skelly when talking:** Settings > System > Restart sound.
- **See what's happening:** `sudo docker logs -f supreme-skelly`

## Develop

```bash
pip install -e ".[dev]"
pytest
ruff check .
SKELLY_SIMULATE=1 supreme-skelly   # then open http://localhost:8420
```

Plain Python (FastAPI) service in `server/skelly`, a no-build web page in `web/`.

**Publishing a release:** bump `VERSION` (and `version` in `pyproject.toml`) and merge to
`main`. GitHub Actions tags and publishes the release, and every box with automatic updates
installs it.
Protocol notes are in [docs/protocol.md](docs/protocol.md).

## Credits and license

Protocol knowledge builds on the community reverse engineering in
[martinecker/SkellyUltraWebController](https://github.com/martinecker/SkellyUltraWebController)
(ISC) and [martinecker/SkellyUltra](https://github.com/martinecker/SkellyUltra). Face
models come from [OpenCV Zoo](https://github.com/opencv/opencv_zoo), the free local voice
from [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx), the singer/band separation
model from [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui), and
song downloads use [yt-dlp](https://github.com/yt-dlp/yt-dlp).

MIT licensed (see [LICENSE](LICENSE)). Not affiliated with Home Depot or the makers of any
other Skelly app. Use at your own risk.
