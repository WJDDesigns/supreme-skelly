"""Where songs come from: YouTube, Spotify and Apple Music links, a song name, or a file.

Spotify and Apple Music encrypt their audio (DRM), so nothing can record it from their apps
in a way that can be split into voice and band ahead of time. Instead a Spotify or Apple Music
link is read for its track names (no account or key needed), and each track is fetched
from YouTube by name with yt-dlp. YouTube links and plain song names go straight to yt-dlp.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

MAX_TRACKS = 100  # one link adds at most this many songs
MAX_SECONDS = 12 * 60  # longer than this is a mix or a live set, not a song
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/140.0 Safari/537.36")

SPOTIFY = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?|spotify:)(track|album|playlist)[/:]([A-Za-z0-9]+)")
APPLE = re.compile(r"music\.apple\.com/(?:([a-z]{2})/)?(album|playlist|song)/(?:[^/?#]+/)?([^/?#]+)")
YOUTUBE = re.compile(r"(?:youtube\.com|youtu\.be|music\.youtube\.com)/", re.I)


@dataclass
class Track:
    title: str
    artist: str = ""
    url: str | None = None  # something yt-dlp can fetch directly; None = search YouTube by name
    source: str = "youtube"  # youtube | spotify | apple | search | file

    @property
    def query(self) -> str:
        return self.url or f"ytsearch1:{self.artist + ' - ' if self.artist else ''}{self.title} official audio"

    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


def kind(text: str) -> str:
    text = text.strip()
    if SPOTIFY.search(text):
        return "spotify"
    if APPLE.search(text):
        return "apple"
    if YOUTUBE.search(text):
        return "youtube"
    if re.match(r"https?://", text):
        return "link"  # SoundCloud, Bandcamp and the many other sites yt-dlp knows
    return "search"


async def resolve(text: str) -> list[Track]:
    """Turn whatever was pasted into a list of songs to fetch."""
    text = text.strip()
    if not text:
        raise ValueError("Paste a link or type a song name")
    k = kind(text)
    if k == "spotify":
        tracks = await spotify_tracks(text)
    elif k == "apple":
        tracks = await apple_tracks(text)
    elif k in ("youtube", "link"):
        tracks = [Track(**t) for t in await ytdlp("list", text)]
    else:
        tracks = [Track(title=text, source="search")]
    if not tracks:
        raise LookupError("Couldn't find any songs at that link")
    return tracks[:MAX_TRACKS]


# -- Spotify ---------------------------------------------------------------------------

async def _get(url: str, **params) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA,
                                                                            "Accept-Language": "en"}) as http:
        r = await http.get(url, params=params or None)
        r.raise_for_status()
        return r


def _walk(node, found: list) -> None:
    """Every dict anywhere in a parsed JSON document."""
    if isinstance(node, dict):
        found.append(node)
        for v in node.values():
            _walk(v, found)
    elif isinstance(node, list):
        for v in node:
            _walk(v, found)


def _artists(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in value if a)
    if isinstance(value, dict):
        return str(value.get("name", ""))
    return ""


def parse_spotify_embed(page: str, what: str) -> list[Track]:
    """Track names from Spotify's embed page (its __NEXT_DATA__ JSON)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    tracks: list[Track] = []
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        dicts: list = []
        _walk(data, dicts)
        for d in dicts:  # playlists and albums: a trackList of {title, subtitle (artists)}
            if isinstance(d.get("trackList"), list) and d["trackList"]:
                for t in d["trackList"]:
                    if isinstance(t, dict) and t.get("title"):
                        tracks.append(Track(title=t["title"], artist=_artists(t.get("subtitle", "")),
                                            source="spotify"))
                break
        if not tracks and what == "track":
            for d in dicts:
                if d.get("type") == "track" and (d.get("name") or d.get("title")):
                    tracks.append(Track(title=d.get("name") or d["title"],
                                        artist=_artists(d.get("artists") or d.get("subtitle", "")), source="spotify"))
                    break
    if not tracks and what == "track":  # older pages: the page title is "Song - song by Artist"
        t = re.search(r'<meta property="og:title" content="([^"]+)"', page)
        d = re.search(r'<meta property="og:description" content="([^"]+)"', page)
        if t:
            artist = html.unescape(d.group(1)).split(" · ")[0] if d else ""
            tracks.append(Track(title=html.unescape(t.group(1)), artist=artist, source="spotify"))
    return tracks


async def spotify_tracks(link: str) -> list[Track]:
    what, ident = SPOTIFY.search(link).groups()
    try:
        page = (await _get(f"https://open.spotify.com/embed/{what}/{ident}")).text
        tracks = parse_spotify_embed(page, what)
    except httpx.HTTPError as exc:
        log.info("spotify embed failed: %r", exc)
        tracks = []
    if not tracks and what == "track":
        try:
            o = (await _get("https://open.spotify.com/oembed", url=f"https://open.spotify.com/track/{ident}")).json()
            if o.get("title"):
                tracks = [Track(title=o["title"], source="spotify")]
        except (httpx.HTTPError, ValueError) as exc:
            log.info("spotify oembed failed: %r", exc)
    if not tracks:
        raise LookupError("Couldn't read that Spotify link. Is the playlist public?")
    return tracks


# -- Apple Music -------------------------------------------------------------------------

def parse_apple_page(page: str) -> list[Track]:
    """Track names from an Apple Music web page (playlists): its JSON-LD or server data."""
    tracks: list[Track] = []
    for m in re.finditer(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in (data.get("track") or []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("name"):
                tracks.append(Track(title=item["name"], artist=_artists(item.get("byArtist")), source="apple"))
    if tracks:
        return tracks
    m = re.search(r'<script[^>]+id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            data = json.loads(m.group(1))
        except ValueError:
            data = None
        dicts: list = []
        _walk(data, dicts)
        seen = set()
        for d in dicts:
            title, artist = d.get("title"), d.get("artistName")
            if isinstance(title, str) and isinstance(artist, str) and (title, artist) not in seen:
                seen.add((title, artist))
                tracks.append(Track(title=title, artist=artist, source="apple"))
    return tracks


async def apple_tracks(link: str) -> list[Track]:
    country, what, ident = APPLE.search(link).groups()
    country = country or "us"
    song = re.search(r"[?&]i=(\d+)", link)
    try:
        if song or what == "song":  # one song: Apple's public lookup API
            r = (await _get("https://itunes.apple.com/lookup", id=song.group(1) if song else ident,
                            country=country)).json()
            return [Track(title=x["trackName"], artist=x.get("artistName", ""), source="apple")
                    for x in r.get("results", []) if x.get("trackName")][:1]
        if what == "album" and ident.isdigit():
            r = (await _get("https://itunes.apple.com/lookup", id=ident, entity="song", country=country)).json()
            tracks = [Track(title=x["trackName"], artist=x.get("artistName", ""), source="apple")
                      for x in r.get("results", []) if x.get("wrapperType") == "track" and x.get("trackName")]
            if tracks:
                return tracks
        tracks = parse_apple_page((await _get(link)).text)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.info("apple music lookup failed: %r", exc)
        tracks = []
    if not tracks:
        raise LookupError("Couldn't read that Apple Music link. Is the playlist shared publicly?")
    return tracks


# -- YouTube (and everything else yt-dlp knows) -------------------------------------------

def _ydl_opts(**more) -> dict:
    return {"quiet": True, "no_warnings": True, "noplaylist": False, "socket_timeout": 20,
            "js_runtimes": {"deno": {}, "node": {}}, **more}


def single_video(url: str) -> bool:
    return bool(re.search(r"[?&]v=[\w-]+|youtu\.be/[\w-]+|/shorts/[\w-]+", url))


def _ytdlp_list(url: str) -> list[Track]:
    """A YouTube link: one video, or every video in a playlist (names only, nothing downloaded).

    A link to one video counts as just that video, even when it carries a list (YouTube adds its
    endless auto "Mix" to links shared from the player); only a playlist page link adds them all.
    """
    import yt_dlp

    one = single_video(url)
    with yt_dlp.YoutubeDL(_ydl_opts(extract_flat="in_playlist", playlistend=MAX_TRACKS, noplaylist=one)) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = info.get("entries")
    if entries is None:
        return [Track(title=info.get("track") or info.get("title") or url, artist=info.get("artist") or "",
                      url=info.get("webpage_url") or url, source="youtube")]
    out = []
    for e in entries:
        if e and (e.get("url") or e.get("id")):
            out.append(Track(title=e.get("title") or e.get("id"), artist=e.get("artist") or "",
                             url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}", source="youtube"))
    return out


def download(track: Track, folder: Path) -> dict:
    """Fetch the best audio for a track into ``folder`` (blocking; run in a thread).

    Returns {"path", "title", "artist", "duration", "url"}.
    """
    import yt_dlp

    folder.mkdir(parents=True, exist_ok=True)

    def too_long(info, *, incomplete):
        if (info.get("duration") or 0) > MAX_SECONDS:
            return "longer than 12 minutes"
        return None

    opts = _ydl_opts(format="bestaudio/best", outtmpl=str(folder / "source.%(ext)s"), noplaylist=True,
                     match_filter=too_long, overwrites=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(track.query, download=True)
        if info.get("entries"):  # a search returns a one-entry playlist
            info = info["entries"][0]
        if not info:
            raise LookupError(f"No match on YouTube for {track.label()}")
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        found = sorted(folder.glob("source.*"))
        if not found:
            raise LookupError(f"Couldn't fetch {track.label()} (too long, or not available here)")
        path = found[0]
    named = track.source in ("spotify", "apple")  # keep the names the link gave, not YouTube's
    title = track.title if named else (info.get("track") or info.get("title") or "")
    artist = track.artist if named else (info.get("artist") or info.get("uploader") or "")
    return {"path": str(path), "title": title, "artist": re.sub(r" - Topic$", "", artist),
            "duration": info.get("duration"), "url": info.get("webpage_url")}


# -- yt-dlp in a child process, kept up to date ---------------------------------------------
# YouTube changes often and yt-dlp follows within days. Running it as a child process means a
# freshly upgraded copy is picked up straight away, without restarting the app.

UPGRADE_EVERY_S = 12 * 3600
_last_upgrade = 0.0


async def _child(*args: str) -> tuple[int, str, str]:
    server = str(Path(__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": server + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = await asyncio.create_subprocess_exec(sys.executable, *args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE, env=env)
    out, err = await asyncio.wait_for(proc.communicate(), 900)
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def upgrade_ytdlp() -> bool:
    """Fetch the newest yt-dlp (at most twice a day). True if it ran."""
    global _last_upgrade
    if time.monotonic() - _last_upgrade < UPGRADE_EVERY_S and _last_upgrade:
        return False
    _last_upgrade = time.monotonic()
    code, _, err = await _child("-m", "pip", "install", "-q", "-U", "yt-dlp[default,deno]")
    log.info("yt-dlp upgrade %s", "done" if code == 0 else f"failed: {err.strip()[-200:]}")
    return code == 0


async def ytdlp(action: str, *args: str):
    """Run list/download in a child process; on failure, upgrade yt-dlp once and try again."""
    for attempt in (1, 2):
        code, out, err = await _child("-m", "skelly.sources", action, *args)
        if code == 0:
            return json.loads(out.strip().splitlines()[-1])
        message = err.strip().splitlines()[-1] if err.strip() else "yt-dlp failed"
        message = re.sub(r"^(ERROR: )?(\[[^\]]+\] )?([\w-]+: )?", "", message)
        if attempt == 1 and not message.startswith("LOOKUP:") and await upgrade_ytdlp():
            continue
        raise LookupError(message.removeprefix("LOOKUP:"))


def _cli(argv: list[str]) -> int:
    action = argv[0]
    try:
        if action == "list":
            print(json.dumps([asdict(t) for t in _ytdlp_list(argv[1])]))
        elif action == "download":
            print(json.dumps(download(Track(**json.loads(argv[1])), Path(argv[2]))))
        else:
            return 2
    except LookupError as exc:
        print(f"LOOKUP:{exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # yt-dlp's own errors: one clean line for the parent to show
        print(str(exc).replace("\n", " "), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
