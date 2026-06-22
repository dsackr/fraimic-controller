# Fraimic Local Controller

A self-hosted replacement for the Fraimic cloud — run on a Raspberry Pi Zero and push images directly to specific Fraimic e-ink frames over your local WiFi, no cloud required.

## Why

The Fraimic app doesn't let you target a specific frame. It uploads an image and then waits for you to physically tap a frame to wake it — if multiple frames are awake, you can't control which one receives the image. This controller bypasses the cloud entirely and pushes images directly to whichever frame you choose.

## How it works

Fraimic frames run a local HTTP server (ESP32-based). This project's Flask server sits on a Pi Zero on the same WiFi network and:

1. Converts any standard image (JPG, PNG, WEBP, etc.) to the Fraimic `.bin` format — raw 4bpp packed pixels, Spectra 6 palette, 1600×1200 for the standard 13.3" frame
2. POSTs the `.bin` directly to the selected frame at `http://{frame_ip}/upload`
3. Triggers `POST http://{frame_ip}/api/refresh` to update the display

## Features

- Web UI accessible from any device on your network
- Local image library with thumbnail grid
- Drag-and-drop image upload
- Per-frame targeting — select exactly which frame receives the image
- Battery level and online/offline status per frame
- Network scan to auto-discover frames
- Resize modes: fill (crop), fit (letterbox), or stretch
- Rotation (0°, 90°, 180°, 270°)
- Dithered preview before sending
- Configurable resolution per frame (for when larger frames arrive)

## Requirements

- Raspberry Pi Zero W (or any Linux machine on the same WiFi as your frames)
- Python 3.9+
- Fraimic frames on the same local network

## Install

```bash
# Copy this repo to the Pi
git clone https://github.com/dsackr/fraimic-controller.git
cd fraimic-controller/local-server

# Install and start (sets up systemd service)
bash install.sh
```

Then open `http://<pi-ip>:5000` from any browser on your network.

To change the default port, edit `/etc/systemd/system/fraimic.service`:
```
ExecStart=/home/pi/fraimic-controller/local-server/venv/bin/python3 app.py 8080
```
Then `sudo systemctl daemon-reload && sudo systemctl restart fraimic`.

## Manual run (without systemd)

```bash
cd local-server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 app.py          # port 5000
python3 app.py 8080     # custom port
```

## Image format

Fraimic frames expect raw binary files with no header:

- **Resolution:** 1600×1200 (standard 13.3" frame) → 960,000 bytes
- **Encoding:** 4 bits per pixel, 2 pixels per byte
- **Byte packing:** high nibble = left pixel, low nibble = right pixel
- **Color palette (Spectra 6):**

| Index | Color  |
|-------|--------|
| 0x0   | Black  |
| 0x1   | White  |
| 0x2   | Green  |
| 0x3   | Blue   |
| 0x4   | Red    |
| 0x5   | Yellow |

Conversion uses Pillow's Floyd-Steinberg dithering quantized to this 6-color palette.

## Also included

`fraimic-controller.html` — a bookmarklet for the Fraimic cloud approach. Drag it to your bookmarks bar and use it on `app.fraimic.com` to target a specific frame without physically tapping it. Requires being logged into the Fraimic web app.

## Frame local API

Discovered by reverse engineering. Useful if you want to build on this:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/upload` | POST | Upload `.bin` file (multipart, field: `image`) |
| `/api/refresh` | POST | Trigger display refresh |
| `/api/info` | GET | Device status (battery, WiFi, firmware) |
| `/portal` | GET | Built-in web portal |
