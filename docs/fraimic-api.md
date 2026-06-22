# Fraimic API & Format Reference

Reverse-engineered from the Fraimic web app and direct frame probing. Documented here so it doesn't live only in an AI's memory.

---

## Local Frame API

Each Fraimic frame runs an HTTP server on port 80 (ESP32-based). Access it at `http://{frame_ip}/`.

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/portal` | Main portal page with links to upload, WiFi, device setup, dashboard |
| GET | `/upload` | Upload form (HTML) |
| POST | `/upload` | Upload a `.bin` image — see below |
| GET | `/api/info` | Device status as JSON |
| POST | `/api/refresh` | Trigger display refresh |
| GET | `/info` | HTML device information page |
| GET | `/wifi` | WiFi configuration page |
| POST | `/api/image` | Returns 501 `{"error":"unsupported_content_type"}` — avoid; sending `application/octet-stream` body causes the frame to hang for 45+ seconds |

### POST /upload

Sends a new image to the frame.

```
POST http://{frame_ip}/upload
Content-Type: multipart/form-data

field name: image
filename:   image.bin  (or any .bin name)
content:    raw .bin file (see Image Format below)
max size:   1MB
```

Python example:
```python
import requests
with open("image.bin", "rb") as f:
    requests.post(
        f"http://{frame_ip}/upload",
        files={"image": ("image.bin", f, "application/octet-stream")},
        timeout=90,
    )
```

### POST /api/refresh

Triggers the display to render the last uploaded image. Call this after `/upload`.

```
POST http://{frame_ip}/api/refresh
```

Response: `{"status": "refresh_started"}`

### GET /api/info

Returns device status. Example response shape:

```json
{
  "battery_pct": 73,
  "firmware_version": "...",
  "device_id": "...",
  "wifi_ssid": "...",
  "ip_address": "192.168.x.x"
}
```

---

## Image Format (.bin)

The frame expects raw binary files with no header and no compression.

### Spec

| Property | Value |
|----------|-------|
| Resolution (13.3" standard) | 1600 × 1200 px |
| File size | 960,000 bytes |
| Bits per pixel | 4 |
| Pixels per byte | 2 |
| Byte order | High nibble = left pixel, low nibble = right pixel |
| Scan order | Left-to-right, top-to-bottom |
| Compression | None |
| Header | None |

For a frame of width W and height H: total bytes = `W × H / 2`

### Spectra 6 Color Palette

| Nibble value | Color  | Approximate RGB |
|:---:|--------|-----------------|
| 0x0 | Black  | (0, 0, 0) |
| 0x1 | White  | (255, 255, 255) |
| 0x2 | Green  | (0, 255, 0) |
| 0x3 | Blue   | (0, 0, 255) |
| 0x4 | Red    | (255, 0, 0) |
| 0x5 | Yellow | (255, 255, 0) |
| 0x6–0xF | Undefined — do not use |

### Byte packing example

For 4 pixels: Black(0), White(1), Red(4), Yellow(5)
```
byte 0: 0x01  →  high nibble=0 (Black),  low nibble=1 (White)
byte 1: 0x45  →  high nibble=4 (Red),    low nibble=5 (Yellow)
```

### Conversion

See `local-server/app.py` → `image_to_bin()`. Pipeline:

1. Decode image with Pillow, convert to RGB
2. Optional rotation
3. Resize to frame dimensions (fill/fit/stretch)
4. Quantize to Spectra 6 palette with Floyd-Steinberg dithering
5. Pack 2 palette indices per byte

**Important Pillow gotcha:** When building the quantize palette image, pad the 256-entry palette by cycling through the 6 colors (`SPECTRA6[i % 6]`), not with zeros. Padding with zeros causes Pillow to assign nibble values > 5 for near-black pixels (multiple "black" entries compete), which renders as garbage on the display. After quantizing, clamp: `px[i] % 6`.

---

## Cloud API

Base URL: `https://origin.fraimic.com/api/v1`

**CORS:** No public CORS headers — only works from `app.fraimic.com` origin. Use a bookmarklet injected into that page (see `fraimic-controller.html`).

### Auth

- Backend: Supabase project `sclpedxwezoiwzesfdps`
- Supabase URL: `https://sclpedxwezoiwzesfdps.supabase.co`
- Token location: `localStorage` on `app.fraimic.com` under key `sb-sclpedxwezoiwzesfdps-auth-token`
- All requests: `Authorization: Bearer <access_token>`

### Key Endpoints

#### List frames
```
GET /account/devices
→ { devices: [{ device_id, device_name, battery_pct, ip_address, last_seen_at, display_type, ... }] }
```

#### Rename a frame
```
POST /account/device-name
Body: { device_id, name }
```

#### Upload to a specific frame (3-step flow)

This bypasses the physical tap requirement.

**Step 1 — Presign:**
```
POST /upload/image/presign?content_type=image%2Fjpeg
→ { success, url, fields, key, upload_id, message }
```
`fields` contains: `Content-Type`, `x-amz-server-side-encryption`, `key`, `AWSAccessKeyId`, `x-amz-security-token`, `policy`, `signature`

**Step 2 — Upload to S3:**
```
POST https://fraimic-prod-user-files.s3.amazonaws.com/
FormData: all fields from Step 1 + append "file" as the image
→ 204 on success
```

**Step 3 — Lock to device:**
```
POST /upload/image/lock-to-device
Body: { device_id: "<uuid>", upload_id: "<from step 1>" }
→ true on success
```

#### Other cloud endpoints
```
GET  /gallery?page=1&page_size=N          — user's image gallery
GET  /albums                               — user's albums
GET  /discover?limit=N&offset=N           — public art discovery
POST /discover/send-to-canvas             — send discovered image: { source, source_object_id }
GET  /upload/image/refresh                — used in normal (non-targeted) upload flow
```

### S3 Bucket
- Name: `fraimic-prod-user-files` (us-east-1)
- Path pattern: `users/{user_id}/{year}/{month}/{day}/{timestamp}-{uuid}/original.{ext}`

---

## Notes

- The frame's ESP32 HTTP server has limited concurrency. Send requests sequentially with a small delay between them; parallel requests cause 45-second timeouts.
- The cloud API is not needed for the local controller — the frame's built-in HTTP server handles everything.
- Frame resolution for the larger Fraimic frames is not yet confirmed. The `width` and `height` fields in `local-server/frames.json` let you configure per-frame resolution.
