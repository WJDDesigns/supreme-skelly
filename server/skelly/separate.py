"""Split a song into the singer's voice and the band, on the mini PC's own CPU.

Uses an MDX-Net vocal model (UVR's "Kim Vocal 2", ONNX) with ONNX Runtime, which is already
installed for the local voice. The model looks at ~6 s of stereo audio at a time as a
spectrogram and returns the part of it that is voice; the band is the song minus the voice.

Run as ``python -m skelly.separate MIX.f32 RATE OUT_DIR`` so the heavy maths happens in its
own process and never stalls the event loop (Bluetooth, Live AI and the web UI keep going).
"""

from __future__ import annotations

import logging
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

RATE = 44100
MODEL_URL = ("https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx")
MODEL_NAME = "Kim_Vocal_2.onnx"
N_FFT = 7680
HOP = 1024
DIM_F = 3072
DIM_T = 256
CHUNK = HOP * (DIM_T - 1)  # samples the model sees at once (~5.9 s)
TRIM = N_FFT // 2  # edge samples of each chunk that are thrown away (the model is unsure there)
STEP = CHUNK - 2 * TRIM
COMPENSATE = 1.009  # this model returns the voice very slightly quiet
THREADS = int(os.environ.get("SKELLY_SEPARATE_THREADS", "3"))  # leave a core for everything else

_WINDOW = np.hanning(N_FFT + 1)[:-1].astype(np.float32)  # periodic Hann, as torch.stft uses


def model_path(models_dir: Path) -> Path:
    """The vocal model, downloaded once (~65 MB) into the models folder."""
    path = models_dir / MODEL_NAME
    if not path.exists():
        models_dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        log.info("downloading the vocal model to %s", path)
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(path)
    return path


def _stft(x: np.ndarray) -> np.ndarray:
    """[channels, CHUNK] float32 -> [channels, N_FFT/2+1, DIM_T] complex, like torch.stft(center=True)."""
    pad = N_FFT // 2
    x = np.pad(x, ((0, 0), (pad, pad)), mode="reflect")
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
    frames = x[:, idx] * _WINDOW  # [ch, T, N_FFT]
    return np.fft.rfft(frames, axis=-1).transpose(0, 2, 1)


def _istft(spec: np.ndarray, length: int) -> np.ndarray:
    """Inverse of _stft: [channels, N_FFT/2+1, DIM_T] -> [channels, length]."""
    frames = np.fft.irfft(spec.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WINDOW  # [ch, T, N_FFT]
    total = N_FFT + HOP * (DIM_T - 1)
    out = np.zeros((spec.shape[0], total), np.float32)
    norm = np.zeros(total, np.float32)
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += frames[:, t]
        norm[t * HOP:t * HOP + N_FFT] += _WINDOW ** 2
    pad = N_FFT // 2
    out = out[:, pad:pad + length]
    return out / np.maximum(norm[pad:pad + length], 1e-8)


def _to_model(spec: np.ndarray) -> np.ndarray:
    """[2, F, T] complex -> [4, DIM_F, T] float: L real, L imag, R real, R imag."""
    s = spec[:, :DIM_F]
    return np.stack([s[0].real, s[0].imag, s[1].real, s[1].imag]).astype(np.float32)


def _from_model(out: np.ndarray) -> np.ndarray:
    spec = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
    spec[0, :DIM_F] = out[0] + 1j * out[1]
    spec[1, :DIM_F] = out[2] + 1j * out[3]
    return spec


class Separator:
    def __init__(self, model: Path, threads: int = THREADS) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), opts, providers=["CPUExecutionProvider"])
        self.input = self.session.get_inputs()[0].name

    def vocals(self, mix: np.ndarray, progress=None) -> np.ndarray:
        """mix: [2, n] float32 at 44.1 kHz. Returns the voice, same shape."""
        n = mix.shape[1]
        padded = np.pad(mix, ((0, 0), (TRIM, TRIM + STEP - n % STEP)))
        out = np.zeros_like(padded)
        starts = range(0, padded.shape[1] - CHUNK + 1, STEP)
        for i, start in enumerate(starts):
            chunk = padded[:, start:start + CHUNK]
            spec = _to_model(_stft(chunk))
            pred = self.session.run(None, {self.input: spec[None]})[0][0]
            wave = _istft(_from_model(pred), CHUNK)
            out[:, start + TRIM:start + CHUNK - TRIM] = wave[:, TRIM:CHUNK - TRIM]
            if progress:
                progress((i + 1) / len(starts))
        return out[:, TRIM:TRIM + n] * COMPENSATE


def main(argv: list[str]) -> int:
    """Child process: read raw float32 stereo at 44.1 kHz, write vocals.f32 and band.f32 next to it."""
    src, out_dir, models = Path(argv[0]), Path(argv[1]), Path(argv[2])
    mix = np.fromfile(src, np.float32).reshape(-1, 2).T.copy()
    sep = Separator(model_path(models))
    last = -1

    def progress(p: float) -> None:
        nonlocal last
        pct = int(p * 100)
        if pct != last:
            last = pct
            print(f"progress {pct}", flush=True)

    voice = sep.vocals(mix, progress)
    voice.T.astype(np.float32).tofile(out_dir / "vocals.f32")
    (mix - voice).T.astype(np.float32).tofile(out_dir / "band.f32")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv[1:]))
