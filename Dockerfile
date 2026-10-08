# Supreme Skelly: headless controller + web UI.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SKELLY_WEB_DIR=/app/web \
    SKELLY_PORT=8420

# ffmpeg decodes whatever audio file you upload (mp3, wav, m4a, ogg, flac...) and reads
# the camera; pulseaudio-utils (parec/pacat/pactl) reach the mic and Skelly's speaker
# through the host's PipeWire.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY server ./server
RUN pip install --no-cache-dir .
# Face models (OpenCV Zoo): YuNet finds faces, SFace fingerprints them. ~40 MB, fetched once at build.
RUN mkdir -p /app/models && python -c "import urllib.request as u; b='https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/'; \
    [u.urlretrieve(b + p, '/app/models/' + p.split('/')[-1]) for p in ('face_detection_yunet/face_detection_yunet_2023mar.onnx', 'face_recognition_sface/face_recognition_sface_2021dec.onnx')]"
COPY web ./web

# Runs as root so it can talk to the host's BlueZ over the mounted D-Bus socket.
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8420/api/health'); sys.exit(0)"
CMD ["supreme-skelly"]
