# Raspberry Pi 4/5 deployment notes (tile player)

- Use **Raspberry Pi OS Lite** or a minimal Debian-based image.
- Ensure `mpv` is built with DRM/KMS or X11 support depending on your kiosk stack.
- Hardware decode (H.264/H.265) on Pi:
  - mpv option: `--hwdec=v4l2m2m`
- GPU memory split:
  - Set to at least 128–256MB (depending on 1080p/4K workload).
- Kiosk session:
  - Lightweight X session (openbox) or DRM/KMS fullscreen with `--fs`.
- Disable screen blanking and power management.
- Prefer wired Ethernet; lock down Wi‑Fi if present.
