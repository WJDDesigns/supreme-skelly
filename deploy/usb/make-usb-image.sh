#!/usr/bin/env bash
# Builds a hands-free Ubuntu Server 24.04 installer image for the mini PC.
#   ./make-usb-image.sh            -> supreme-skelly-installer.iso
# Flash the result to a USB stick (e.g. balenaEtcher), boot the mini PC from it,
# and it installs Ubuntu + Bluetooth + Docker with no questions, then reboots.
# Needs: curl, xorriso (macOS: brew install xorriso; Ubuntu: apt install xorriso).
set -euo pipefail
cd "$(dirname "$0")"

RELEASE_URL="https://releases.ubuntu.com/24.04"
OUT="${OUT:-supreme-skelly-installer.iso}"
# Default password "skelly" (SHA-512 crypt). Override with PASSWORD_HASH=$(openssl passwd -6).
PASSWORD_HASH="${PASSWORD_HASH:-\$6\$skellysalt1\$qCrOhYutuDmjG0ehV67.HwUUhbAQk73ILySkjhLU6AUghWuYnHMphqp41tR4ckwbEZPRUJrFYOhrAFrg5hrp9/}"

command -v xorriso >/dev/null || { echo "Please install xorriso first." >&2; exit 1; }

echo "==> Finding the latest Ubuntu Server 24.04 image"
curl -fsSL "$RELEASE_URL/SHA256SUMS" -o SHA256SUMS
ISO_NAME="$(grep -o 'ubuntu-24\.04[.0-9]*-live-server-amd64\.iso' SHA256SUMS | sort -V | tail -1)"
[ -n "$ISO_NAME" ] || { echo "Couldn't find the server image in SHA256SUMS" >&2; exit 1; }

if [ ! -f "$ISO_NAME" ]; then
  echo "==> Downloading $ISO_NAME (about 3 GB)"
  curl -fL --retry 5 -C - "$RELEASE_URL/$ISO_NAME" -o "$ISO_NAME"
fi
echo "==> Checking download"
EXPECTED="$(grep " \*\?$ISO_NAME\$" SHA256SUMS | awk '{print $1}')"
if command -v sha256sum >/dev/null; then ACTUAL="$(sha256sum "$ISO_NAME" | awk '{print $1}')"
else ACTUAL="$(shasum -a 256 "$ISO_NAME" | awk '{print $1}')"; fi
[ "$EXPECTED" = "$ACTUAL" ] || { echo "Checksum mismatch; delete $ISO_NAME and retry." >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/nocloud"
sed "s|__PASSWORD_HASH__|$PASSWORD_HASH|" user-data > "$WORK/nocloud/user-data"
cp meta-data "$WORK/nocloud/meta-data"

echo "==> Packing Supreme Skelly onto the image"
REPO_ROOT="$(cd ../.. && pwd)"
if git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1; then
  git -C "$REPO_ROOT" archive --format=tar.gz -o "$WORK/supreme-skelly.tar.gz" HEAD
else
  tar -czf "$WORK/supreme-skelly.tar.gz" -C "$REPO_ROOT" --exclude=.git --exclude='*.iso' --exclude=data .
fi

echo "==> Adding the unattended setup to the boot menu"
xorriso -osirrox on -indev "$ISO_NAME" -extract /boot/grub/grub.cfg "$WORK/grub.cfg" >/dev/null 2>&1
chmod u+w "$WORK/grub.cfg"
# Boot straight into the unattended install after 3 seconds. The seed path is quoted
# because GRUB ends a command at a bare ";", which drops the path and leaves the
# installer asking questions.
sed -i.bak \
  -e 's/^set timeout=.*/set timeout=3/' \
  -e 's|linux\(.*\)/casper/vmlinuz  *---|linux\1/casper/vmlinuz autoinstall "ds=nocloud;s=/cdrom/nocloud/" ---|' \
  "$WORK/grub.cfg"
grep -qF 'autoinstall "ds=nocloud;s=/cdrom/nocloud/"' "$WORK/grub.cfg" || { echo "Couldn't patch grub.cfg" >&2; exit 1; }

echo "==> Writing $OUT"
rm -f "$OUT"
xorriso -indev "$ISO_NAME" -outdev "$OUT" \
  -map "$WORK/nocloud" /nocloud \
  -map "$WORK/nocloud/user-data" /autoinstall.yaml \
  -map "$WORK/supreme-skelly.tar.gz" /supreme-skelly.tar.gz \
  -map "$WORK/grub.cfg" /boot/grub/grub.cfg \
  -boot_image any replay >/dev/null 2>&1
echo
echo "Done: $OUT"
echo "Flash it to a USB stick, plug it into the mini PC with a keyboard, power on and press F7 to pick the USB."
echo "It wipes the disk, installs everything and reboots. A few minutes after that, open http://skelly.local:8420"
echo "Remote access: ssh skelly@skelly.local (password: skelly; change it with passwd)."
