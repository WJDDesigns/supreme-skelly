"""Capabilities of each supported animatronic.

Movement bits, light channels, light modes and eye slots differ per device;
the UI and API are driven entirely from these profiles.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Movement:
    key: str
    label: str
    bit: int


@dataclass(frozen=True)
class Light:
    key: str
    label: str
    channel: int


@dataclass(frozen=True)
class LightMode:
    value: int
    label: str


@dataclass(frozen=True)
class Eye:
    value: int
    label: str


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    casual_name: str
    ble_names: tuple[str, ...]
    live_audio_names: tuple[str, ...] = ()
    movements: tuple[Movement, ...] = ()
    lights: tuple[Light, ...] = ()
    light_modes: tuple[LightMode, ...] = ()
    eyes: tuple[Eye, ...] = ()
    wake_tone_ms: int | None = None
    notes: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return asdict(self)


SKELLY_MODES = (LightMode(1, "Static"), LightMode(2, "Strobe"), LightMode(3, "Pulsing"))

_SKELLY_EYES = (
    "Blue Eyes", "Hazel Eyes", "Green Eyes", "Orange Eyes", "Red Eyes", "Grey Eyes",
    "Yellow Reptile Eye", "Orange Reptile Eye", "Rainbow Swirl", "Flames", "Gold Star",
    "Skull and Crossbones", "Fireworks", "American Flag", "Heart", "Four-Leaf Clover",
    "Snowflake", "Confetti",
)

# The official 12ft app's byte-to-eye labels are wrong; this order was
# verified by stepping through bytes 1-20 on real hardware.
_SKELLY_12FT_EYES = (
    "American Flag", "Heart", "Four-Leaf Clover", "Snowflake", "Ice Eye",
    "Peppermint Swirl", "Cyber Eye", "Blue Eyes", "Hazel Eyes", "Green Eyes",
    "Brown Eyes", "Orange Eyes", "Grey Eyes", "Yellow Reptile Eye",
    "Orange Reptile Eye", "Rainbow Swirl", "Flames", "Gold Star",
    "Skull and Crossbones", "Fireworks",
)


def _eyes(labels: tuple[str, ...]) -> tuple[Eye, ...]:
    return tuple(Eye(i + 1, label) for i, label in enumerate(labels))


_SKELLY_MOVES = (
    Movement("head", "Head", 0x01),
    Movement("arms", "Arms", 0x02),
    Movement("torso", "Torso", 0x04),
    Movement("all", "All", 0xFF),
)
_SKELLY_LIGHTS = (Light("chest", "Chest", 0), Light("head", "Head", 1))

ULTRA_SKELLY_V2 = Profile(
    key="ultra_skelly_v2",
    name="Ultra Skelly v2",
    casual_name="Skelly",
    ble_names=("Ultra Skelly v2",),
    live_audio_names=("Ultra Skelly V2 Live",),
    movements=_SKELLY_MOVES,
    lights=_SKELLY_LIGHTS,
    light_modes=SKELLY_MODES,
    eyes=_eyes(_SKELLY_EYES),
)

ANIMATED_SKELLY = Profile(
    key="animated_skelly",
    name="Animated Skelly",
    casual_name="Skelly",
    ble_names=("Animated Skelly",),
    live_audio_names=("Animated Skelly Live",),
    movements=_SKELLY_MOVES,
    lights=_SKELLY_LIGHTS,
    light_modes=SKELLY_MODES,
    eyes=_eyes(_SKELLY_EYES),
    notes=("Assumed to match Ultra Skelly v2 until hardware testing says otherwise.",),
)

SKELLY_12FT = Profile(
    key="skelly_12ft",
    name="12ft Skelly",
    casual_name="Skelly",
    ble_names=("12ft Skelly",),
    live_audio_names=("12ft Skelly (Live)",),
    movements=(Movement("head", "Head", 0x02),),
    lights=(Light("chest", "Chest", 0),),
    light_modes=SKELLY_MODES,
    eyes=_eyes(_SKELLY_12FT_EYES),
    notes=("Only head movement is physically present.",
           "Six chest LEDs act as one channel."),
)

LETHAL_LILY = Profile(
    key="lethal_lily",
    name="Lethal Lily",
    casual_name="Lily",
    ble_names=("Lethal Lily",),
    movements=(
        Movement("wrist", "Wrist", 0x01),
        Movement("elbow", "Elbow", 0x02),
        Movement("head", "Head", 0x10),
        Movement("eyes", "Eyes", 0x20),
        Movement("all", "All", 0xFF),
    ),
    lights=(Light("lantern", "Lantern", 0),),
    light_modes=(LightMode(1, "Flickering"), LightMode(2, "Pulsing"), LightMode(3, "Chasing")),
)

ULTRA_SANTA = Profile(
    key="ultra_santa",
    name="Ultra Santa",
    casual_name="Santa",
    ble_names=("Ultra Santa",),
    movements=(
        Movement("eyes", "Eyes", 0x01),
        Movement("head", "Head", 0x02),
        Movement("torso", "Torso", 0x04),
        Movement("arms", "Arms", 0x08),
        Movement("all", "All", 0xFF),
    ),
    lights=(Light("light", "Light", 0),),
    # The official app labels mode 1 "Static" but it flickers; mode 4 is the real static.
    light_modes=(LightMode(1, "Flickering"), LightMode(2, "Sparkle"),
                 LightMode(3, "Pulsing"), LightMode(4, "Static")),
    wake_tone_ms=125,
    notes=("Sparkle and Pulsing modes are not yet confirmed on hardware.",),
)

UNKNOWN = Profile(
    key="unknown",
    name="Unknown Animatronic",
    casual_name="Skelly",
    ble_names=(),
    notes=("Fallback profile; no device-specific commands are assumed.",),
)

PROFILES: tuple[Profile, ...] = (ULTRA_SKELLY_V2, SKELLY_12FT, ANIMATED_SKELLY, LETHAL_LILY, ULTRA_SANTA)


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


_BY_BLE = {_norm(n): p for p in PROFILES for n in p.ble_names}
_BY_KEY = {p.key: p for p in (*PROFILES, UNKNOWN)}


def by_ble_name(name: str | None) -> Profile:
    return _BY_BLE.get(_norm(name), UNKNOWN)


def by_key(key: str | None) -> Profile | None:
    return _BY_KEY.get(key or "")


def is_supported(name: str | None) -> bool:
    return _norm(name) in _BY_BLE
