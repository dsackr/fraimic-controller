#!/usr/bin/env python3
"""
Fraimic Local Controller
Raspberry Pi web server — replace Fraimic cloud for local image management.

Usage:
  python3 app.py           # runs on port 5000
  python3 app.py 8080      # custom port

Then open http://<pi-ip>:5000 from any device on the same WiFi.
"""

import os
import json
import socket
import concurrent.futures
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from flask import Flask, request, jsonify, send_file, send_from_directory
from PIL import Image

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
LIBRARY_DIR = BASE_DIR / "library"
FRAMES_FILE = BASE_DIR / "frames.json"
STATIC_DIR  = BASE_DIR / "static"

LIBRARY_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR))

# ── Spectra 6 Palette ────────────────────────────────────────────────────────
#
# The Fraimic display uses 4 bits per pixel to encode one of 6 colors.
# Nibble value → display color:
#   0x0  Black
#   0x1  White
#   0x2  Green
#   0x3  Blue
#   0x4  Red
#   0x5  Yellow
#
# RGB target values below are approximate display output (tuned for dithering).
# If colors look off on your frame, adjust these values.

SPECTRA6 = [
    (0,   0,   0),    # 0: Black
    (255, 255, 255),  # 1: White
    (0,   255, 0),    # 2: Green
    (0,   0,   255),  # 3: Blue
    (255, 0,   0),    # 4: Red
    (255, 255, 0),    # 5: Yellow
]

# Numpy array for fast perceptual distance calculation
SPECTRA6_NP = np.array(SPECTRA6, dtype=np.float32)

# Perceptual weights for RGB distance (luminance-weighted)
RGB_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _nearest_color(r, g, b):
    """Return Spectra 6 palette index nearest to (r, g, b)."""
    diffs = SPECTRA6_NP - np.array([r, g, b], dtype=np.float32)
    dists = (diffs * diffs * RGB_WEIGHTS).sum(axis=1)
    return int(np.argmin(dists))


def _atkinson_dither(img_array, width, height):
    """
    Atkinson dithering to Spectra 6 palette.
    Returns flat uint8 array of palette indices (length = width * height).

    Atkinson distributes 1/8 of the quantization error to 6 neighbors
    (total 6/8 — the remaining 2/8 is discarded). This preserves highlights
    better than Floyd-Steinberg and is preferred for limited-palette displays.

    Error diffusion pattern (each neighbor gets 1/8):
        .  X  1  1
        1  1  1  .
        .  1  .  .
    """
    buf = img_array.astype(np.float32)   # (H, W, 3) — modified in place
    out = np.zeros(height * width, dtype=np.uint8)

    for y in range(height):
        for x in range(width):
            r = float(np.clip(buf[y, x, 0], 0, 255))
            g = float(np.clip(buf[y, x, 1], 0, 255))
            b = float(np.clip(buf[y, x, 2], 0, 255))

            idx = _nearest_color(r, g, b)
            out[y * width + x] = idx

            pr, pg, pb = SPECTRA6[idx]
            er = (r - pr) / 8
            eg = (g - pg) / 8
            eb = (b - pb) / 8

            # 6 neighbors, each gets 1/8 of the error
            for dx, dy in ((1,0),(2,0),(-1,1),(0,1),(1,1),(0,2)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    buf[ny, nx, 0] += er
                    buf[ny, nx, 1] += eg
                    buf[ny, nx, 2] += eb

    return out

# ── Frame defaults ────────────────────────────────────────────────────────────

DEFAULT_WIDTH  = 1600   # 13.3" standard Fraimic frame
DEFAULT_HEIGHT = 1200

ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif"}

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_frames():
    if FRAMES_FILE.exists():
        return json.loads(FRAMES_FILE.read_text())
    return []

def save_frames(frames):
    FRAMES_FILE.write_text(json.dumps(frames, indent=2))

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

def resize_image(img, width, height, mode="fill"):
    """
    Resize PIL image to (width, height).

    mode='fill'    — crop-to-fill, centered (no black bars)
    mode='fit'     — letterbox/pillarbox (black bars, full image visible)
    mode='stretch' — force resize ignoring aspect ratio
    """
    if mode == "stretch":
        return img.resize((width, height), Image.LANCZOS)

    src_w, src_h = img.size
    tgt_ratio = width / height
    src_ratio  = src_w / src_h

    if mode == "fill":
        if src_ratio > tgt_ratio:
            # Source wider → shrink height to match, crop width
            new_h = height
            new_w = int(src_ratio * height)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - width) // 2
            return img.crop((left, 0, left + width, height))
        else:
            # Source taller → shrink width to match, crop height
            new_w = width
            new_h = int(width / src_ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            top = (new_h - height) // 2
            return img.crop((0, top, width, top + height))

    else:  # fit
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        if src_ratio > tgt_ratio:
            new_w = width
            new_h = int(width / src_ratio)
        else:
            new_h = height
            new_w = int(src_ratio * height)
        resized = img.resize((new_w, new_h), Image.LANCZOS)
        x = (width - new_w) // 2
        y = (height - new_h) // 2
        canvas.paste(resized, (x, y))
        return canvas

def image_to_bin(image_data, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT,
                 resize_mode="fill", rotate=0):
    """
    Convert raw image bytes → Fraimic .bin format.

    .bin format spec:
      • No header, no compression
      • width × height / 2 bytes total  (1600×1200 = 960,000 bytes)
      • Each byte encodes 2 pixels:
          high nibble (bits 7-4) = left pixel color index
          low  nibble (bits 3-0) = right pixel color index
      • Color indices 0-5 per SPECTRA6 table above

    Conversion pipeline:
      1. Decode input image (any PIL-supported format)
      2. Optional rotation
      3. Resize to frame dimensions
      4. Floyd-Steinberg dithering to Spectra 6 palette (via Pillow quantize)
      5. Pack 2 palette indices per byte
    """
    img = Image.open(BytesIO(image_data)).convert("RGB")

    if rotate:
        img = img.rotate(-rotate, expand=True)  # positive = clockwise

    img = resize_image(img, width, height, mode=resize_mode)

    # Atkinson dithering → palette indices 0-5
    px = _atkinson_dither(np.array(img), width, height)

    # Pack 2 pixels per byte: high nibble = even pixel, low nibble = odd pixel
    n = width * height
    out = bytearray(n // 2)
    for i in range(0, n, 2):
        out[i >> 1] = (int(px[i]) << 4) | int(px[i + 1])

    return bytes(out)

def send_bin_to_frame(frame_ip, bin_data, do_refresh=True):
    """
    POST .bin data to frame's /upload endpoint.
    Optionally triggers /api/refresh to update the display.
    """
    resp = requests.post(
        f"http://{frame_ip}/upload",
        files={"image": ("image.bin", bin_data, "application/octet-stream")},
        timeout=90,
    )
    resp.raise_for_status()

    if do_refresh:
        try:
            requests.post(f"http://{frame_ip}/api/refresh", timeout=15)
        except Exception:
            pass  # Refresh failure is non-fatal

    return resp.status_code

# ── Routes: Static UI ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")

# ── Routes: Frames ────────────────────────────────────────────────────────────

@app.route("/api/frames", methods=["GET"])
def frames_list():
    return jsonify(load_frames())

@app.route("/api/frames", methods=["POST"])
def frames_add():
    data = request.json or {}
    if not data.get("ip"):
        return jsonify({"error": "ip required"}), 400

    frames = load_frames()
    for f in frames:
        if f["ip"] == data["ip"]:
            f.update(data)
            save_frames(frames)
            return jsonify(f)

    frame = {
        "ip":     data["ip"],
        "name":   data.get("name", data["ip"]),
        "width":  data.get("width",  DEFAULT_WIDTH),
        "height": data.get("height", DEFAULT_HEIGHT),
    }
    frames.append(frame)
    save_frames(frames)
    return jsonify(frame), 201

@app.route("/api/frames/<frame_ip>", methods=["PATCH"])
def frames_update(frame_ip):
    data = request.json or {}
    frames = load_frames()
    for f in frames:
        if f["ip"] == frame_ip:
            f.update(data)
            save_frames(frames)
            return jsonify(f)
    return jsonify({"error": "not found"}), 404

@app.route("/api/frames/<frame_ip>", methods=["DELETE"])
def frames_delete(frame_ip):
    frames = [f for f in load_frames() if f["ip"] != frame_ip]
    save_frames(frames)
    return jsonify({"ok": True})

@app.route("/api/frames/<frame_ip>/info", methods=["GET"])
def frames_info(frame_ip):
    """Proxy to frame's /api/info — avoids browser CORS restriction."""
    try:
        r = requests.get(f"http://{frame_ip}/api/info", timeout=5)
        return jsonify(r.json())
    except requests.exceptions.ConnectionError:
        return jsonify({"error": "offline"}), 503
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/frames/<frame_ip>/refresh", methods=["POST"])
def frames_refresh(frame_ip):
    """Trigger display refresh without uploading a new image."""
    try:
        r = requests.post(f"http://{frame_ip}/api/refresh", timeout=15)
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route("/api/scan", methods=["POST"])
def frames_scan():
    """
    Scan the local /24 subnet for Fraimic frames.
    Returns a list of {ip, info} for each frame found.
    Warning: takes 5-15 seconds on Pi Zero.
    """
    local_ip = get_local_ip()
    prefix   = ".".join(local_ip.split(".")[:3])

    def probe(i):
        ip = f"{prefix}.{i}"
        if ip == local_ip:
            return None
        try:
            r = requests.get(f"http://{ip}/api/info", timeout=0.8)
            if r.status_code == 200:
                info = r.json()
                # Heuristic: Fraimic frames report battery_pct and firmware_version
                if any(k in info for k in ("battery_pct", "firmware_version", "device_id")):
                    return {"ip": ip, "info": info}
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as ex:
        results = list(ex.map(probe, range(1, 255)))

    found = [r for r in results if r]
    return jsonify(found)

# ── Routes: Library ───────────────────────────────────────────────────────────

@app.route("/api/library", methods=["GET"])
def library_list():
    images = []
    try:
        for p in sorted(LIBRARY_DIR.iterdir(),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            if p.suffix.lower() in ALLOWED_EXTS:
                images.append({
                    "name":     p.name,
                    "url":      f"/library/{p.name}",
                    "size":     p.stat().st_size,
                    "modified": int(p.stat().st_mtime),
                })
    except Exception:
        pass
    return jsonify(images)

@app.route("/api/library", methods=["POST"])
def library_upload():
    """Upload an image file to the library."""
    if "image" not in request.files or not request.files["image"].filename:
        return jsonify({"error": "no image"}), 400
    f = request.files["image"]
    dest = LIBRARY_DIR / f.filename
    f.save(str(dest))
    return jsonify({"name": f.filename, "url": f"/library/{f.filename}"}), 201

@app.route("/api/library/<filename>", methods=["DELETE"])
def library_delete(filename):
    p = LIBRARY_DIR / filename
    if p.exists() and p.suffix.lower() in ALLOWED_EXTS:
        p.unlink()
        return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404

@app.route("/library/<filename>")
def library_serve(filename):
    return send_from_directory(LIBRARY_DIR, filename)

# ── Routes: Send & Preview ────────────────────────────────────────────────────

@app.route("/api/send", methods=["POST"])
def send_image():
    """
    Convert and send an image to a specific Fraimic frame.

    multipart/form-data fields:
      frame_ip      (required) target frame IP address
      image         uploaded image file, OR
      library_name  filename from the local library
      save          'true' → save uploaded image to library
      resize_mode   'fill' (default) | 'fit' | 'stretch'
      rotate        0 (default) | 90 | 180 | 270  (degrees clockwise)
      refresh       'true' (default) → trigger display refresh after upload
    """
    frame_ip = request.form.get("frame_ip", "").strip()
    if not frame_ip:
        return jsonify({"error": "frame_ip required"}), 400

    # Get frame resolution from config
    frames    = load_frames()
    frame_cfg = next((f for f in frames if f["ip"] == frame_ip), {})
    width     = int(frame_cfg.get("width",  DEFAULT_WIDTH))
    height    = int(frame_cfg.get("height", DEFAULT_HEIGHT))

    resize_mode = request.form.get("resize_mode", "fill")
    rotate      = int(request.form.get("rotate", 0))
    do_refresh  = request.form.get("refresh", "true").lower() == "true"

    # Resolve image data
    if "image" in request.files and request.files["image"].filename:
        f          = request.files["image"]
        image_data = f.read()
        if request.form.get("save", "false").lower() == "true":
            (LIBRARY_DIR / f.filename).write_bytes(image_data)
    elif request.form.get("library_name"):
        p = LIBRARY_DIR / request.form["library_name"]
        if not p.exists():
            return jsonify({"error": "library image not found"}), 404
        image_data = p.read_bytes()
    else:
        return jsonify({"error": "provide 'image' file or 'library_name'"}), 400

    try:
        bin_data = image_to_bin(image_data, width, height, resize_mode, rotate)
        send_bin_to_frame(frame_ip, bin_data, do_refresh=do_refresh)
        return jsonify({"ok": True, "bytes": len(bin_data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/preview", methods=["POST"])
def preview_image():
    """
    Return a PNG showing how the image will look after Spectra 6 dithering.
    Same form fields as /api/send, plus:
      max_width  max preview width in pixels (default 800)
    """
    width       = int(request.form.get("width",  DEFAULT_WIDTH))
    height      = int(request.form.get("height", DEFAULT_HEIGHT))
    resize_mode = request.form.get("resize_mode", "fill")
    rotate      = int(request.form.get("rotate", 0))
    max_w       = int(request.form.get("max_width", 800))

    if "image" in request.files and request.files["image"].filename:
        image_data = request.files["image"].read()
    elif request.form.get("library_name"):
        p = LIBRARY_DIR / request.form["library_name"]
        if not p.exists():
            return jsonify({"error": "not found"}), 404
        image_data = p.read_bytes()
    else:
        return jsonify({"error": "provide image or library_name"}), 400

    img = Image.open(BytesIO(image_data)).convert("RGB")
    if rotate:
        img = img.rotate(-rotate, expand=True)
    img = resize_image(img, width, height, mode=resize_mode)

    px = _atkinson_dither(np.array(img), width, height)
    rgb_pixels = [SPECTRA6[int(i)] for i in px]
    rgb = Image.new("RGB", (width, height))
    rgb.putdata(rgb_pixels)

    # Scale down for browser preview
    prev_h = int(height * max_w / width)
    rgb = rgb.resize((max_w, prev_h), Image.NEAREST)

    buf = BytesIO()
    rgb.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

# ── Routes: Server status ─────────────────────────────────────────────────────

@app.route("/api/status", methods=["GET"])
def server_status():
    lib_count = sum(
        1 for p in LIBRARY_DIR.iterdir()
        if p.suffix.lower() in ALLOWED_EXTS
    ) if LIBRARY_DIR.exists() else 0
    return jsonify({
        "ok":          True,
        "local_ip":    get_local_ip(),
        "library_dir": str(LIBRARY_DIR),
        "library_count": lib_count,
    })

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    local_ip = get_local_ip()
    print(f"\n  Fraimic Local Controller")
    print(f"  Running at http://{local_ip}:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
