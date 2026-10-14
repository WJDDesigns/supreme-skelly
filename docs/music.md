# Music: Skelly sings and dances

The **Music** page turns Skelly into a singer. Add songs from YouTube, Spotify or Apple Music,
put them in playlists, and he sings them: his mouth moves with the words, he dances to the
beat, and his lights can follow the music.

![The Music page](images/music-page.png)

## How it works

![How a song becomes a performance](images/music-flow.svg)

Skelly's jaw is moved by his own electronics from whatever plays through his speaker. If he
played the whole song, his mouth would flap to the drums and guitars too. So when you add a
song, the mini PC **separates the singer from the band** (with an AI model that runs on the
mini PC itself, no cloud). While the song plays:

- **Skelly's speaker gets the voice.** His mouth opens on the words and closes when the singer stops.
- **Your other speakers get the band.** These are the extra speakers you added in
  **Settings > Sound**. If Skelly is your only speaker, he plays the whole song, with the
  voice and music balanced as in the original recording (see "Music on Skelly's speaker" below).
- **The beat drives the dance and the lights.** Every beat of the song is found ahead of
  time, so moves and colour changes are sent to land on the beat you hear. Bluetooth
  speakers play about a third of a second late, and that's allowed for.

Getting a song ready takes a few minutes per song on a small mini PC (an Intel N100 splits a
4-minute song in roughly 3 to 5 minutes). It happens once; after that the song plays straight away.
While Skelly is singing, getting songs ready pauses, so the music never skips; it carries on
when he stops.

## Adding songs

Paste one of these into **Add songs**, or type a song's name:

| What you paste | What happens |
|---|---|
| A YouTube or YouTube Music link | A video link adds just that song (even if YouTube tacked a "Mix" onto it); a playlist page link adds the whole playlist |
| A Spotify song, album or playlist link | The track names are read from Spotify, and each song is fetched from YouTube |
| An Apple Music song, album or playlist link | Same as Spotify |
| A song name, e.g. "Monster Mash" | The best match on YouTube |
| Your own files (MP3, M4A, WAV, FLAC) | Drop them on the page |

**Why not play straight from the Spotify or Apple Music app?** Both services encrypt their
audio, so nothing outside their own apps can get at it ahead of time to separate the singer
and find the beat. Reading the playlist and fetching each song by name gets the same songs
with none of that, and works without a paid account. Spotify playlists must be public; Apple
Music playlists must be shared.

If a song fails (rare: a video that isn't available, or YouTube having a bad day), tap
**Try again**, paste a different link, or drop in a file. The mini PC keeps its YouTube
downloader up to date on its own.

## Playlists

Tap **New playlist**, then add songs with **+ Playlist** next to each one, or pick the playlist
under **Add to** before pasting a link. **Sing** plays it in order, 🔀 shuffles, 🔁 repeats it all
night. **Edit** reorders songs or starts from any song.

While a song is playing, Skelly won't start a conversation with passers-by. Pressing **Start**
on the Conversation page stops the music.

## The show

| Setting | What it does |
|---|---|
| **Dance** | Head, arms and torso move to the beat: just his head in quiet parts, everything in the loud parts. His head always moves while he sings. |
| **Lights follow the music** | Colours change on the beat (every bar when it's quiet, every beat when it's loud) and brightness follows the loudness. His own look comes back after the song. |
| **Light colours** | Halloween, blood red, ghostly blues, party, or a different set for each song |
| **Voice volume** | How loud the singing is on Skelly. Every song is already evened out and turned up loud, so 100% is a good start |
| **Music on Skelly's speaker** | When Skelly is your only speaker: 100% is the song as recorded. Lower it for a cleaner mouth that moves only to the words. **Back to the song's own mix** puts every level back to the original balance. |
| **Voice and music on Skelly** | Together, or the voice on one side and the music on the other. If his mouth moves to the music, try each side while a song plays: if his jaw listens to one side only, one of them makes it follow just the words. |
| **Band on my other speakers** / **Singing on my other speakers too** | Where the band (and, optionally, the voice) plays |

### Timing

Every Bluetooth speaker has its own delay, so two sliders under **Timing** let you line things up:

- **Moves and lights:** if he dances behind the beat, slide left; ahead of it, slide right.
- **Other speakers:** if the band sounds behind Skelly's voice, slide left; ahead of it, slide right. Both sliders reach 3 seconds either way; a speaker on a different Bluetooth radio can lag by a second or more.

If the music on your other speakers skips, check the mini PC has a second Bluetooth radio (most have
one built in). Extra speakers pair on the radio Skelly isn't using. A speaker added before v0.7.8
needs removing and adding again in **Settings > Sound** to move over.

Small steps (20 to 50 ms) make a visible difference. Every slider changes the song that is playing straight away, so line things up while he sings. The settings are saved and used for every song.

## Under the hood

- Voice separation: the MDX-Net "Kim Vocal 2" model from
  [Ultimate Vocal Remover](https://github.com/Anjok07/ultimatevocalremovergui), run with ONNX
  Runtime on the CPU at low priority so the rest of the app stays responsive.
- Downloads: [yt-dlp](https://github.com/yt-dlp/yt-dlp).
- Beat tracking: an onset envelope, tempo from its autocorrelation and beat positions by
  dynamic programming, in plain numpy (`server/skelly/beats.py`).
- Songs are kept as small Opus files in `data/music` on the mini PC (about 8 MB per song).
  Removing a song deletes its files.

Only download music you have the right to use.
