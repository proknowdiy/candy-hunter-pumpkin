# Candy Detector — MIPI Camera + WebSocket (RDK X5)

Custom YOLO11s candy detector running on the BPU, fed by the D-Robotics stereo
MIPI camera. Results go to the **terminal** and to a **WebSocket** (with a
built-in web UI on the same port).

## Files

```
candy_detector_mipi_camera_web/
├── candy_mipi_web.py                    # main program (detector + camera + server)
├── weights_bayese_640x640_nv12.bin      # BPU model (copy of the original)
├── candy_classes.names                  # 5 class names
├── run.sh                               # convenience launcher
├── web/
│   └── index.html                       # web UI (served by the program itself)
└── README.md
```

## Run

```bash
cd /home/sunrise/custom_models/candy_detector/candy_detector_mipi_camera_web
python3 candy_mipi_web.py
```

Then open in a browser on the same network:

```
http://<board-ip>:8080/
```

Stop with `Ctrl+C`.

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--model` | `weights_bayese_640x640_nv12.bin` | BPU `.bin` model |
| `--labels` | `candy_classes.names` | one class name per line |
| `--conf` | `0.25` | confidence threshold |
| `--nms` | `0.45` | NMS IoU threshold |
| `--port` | `8080` | HTTP + WebSocket port |
| `--host` | `0.0.0.0` | bind address |
| `--priority` | `0` | BPU priority 0–255 |
| `--bpu-cores` | `0` | BPU core list, e.g. `--bpu-cores 0 1` |
| `--log-every` | `1.0` | seconds between terminal detection lines |

Example with a stricter threshold:

```bash
python3 candy_mipi_web.py --conf 0.4 --log-every 0.5
```

## Terminal output

One line per `--log-every` seconds:

```
[det] 2 object(s) fps=28.4 infer=21.7ms | center_fresh 0.87 [412,233,690,588] ; eclairs 0.61 [901,410,1120,702]
[det] no candy  fps=28.6 infer=21.5ms
```

## WebSocket protocol

Connect to `ws://<board-ip>:8080/ws`. Each message is one **binary** frame:

```
[4 bytes big-endian header length N][N bytes UTF-8 JSON header][JPEG bytes]
```

JSON header:

```json
{
  "ts": 1761523200123,
  "width": 1920,
  "height": 1072,
  "fps": 28.4,
  "infer_ms": 21.7,
  "detections": [
    {"label": "center_fresh", "class_id": 0, "score": 0.87, "box": [412.0, 233.0, 690.0, 588.0]}
  ]
}
```

`box` is `[x1, y1, x2, y2]` in original frame pixels (1920×1072).

### Minimal Python client

```python
import asyncio, json, struct
import websockets

async def main():
    async with websockets.connect("ws://<board-ip>:8080/ws") as ws:
        async for msg in ws:
            n = struct.unpack(">I", msg[:4])[0]
            meta = json.loads(msg[4:4 + n])
            jpeg = msg[4 + n:]
            print(meta["fps"], meta["detections"])
            # open("frame.jpg", "wb").write(jpeg)

asyncio.run(main())
```

## Notes

- Frame size is **1920×1072** (not 1080) because the hardware JPEG encoder
  requires a 16-byte-aligned height.
- Only one MIPI camera may be connected; the sensor is auto-detected.
- The web UI is served by the Python process itself — no nginx needed.
- If the camera fails to open, check the FPC cable and reboot the board.
