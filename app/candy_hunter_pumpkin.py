#!/usr/bin/env python3

"""
Candy-Hunter Pumpkin
AI-Powered Halloween Candy Detection and Interactive Pumpkin
============================================================

Created by: Pro Know
YouTube: https://www.youtube.com/@ProKnow
Project Platform: D-Robotics RDK X5 + ESP32-C3
Copyright (c) 2026 Pro Know.
Licensed under the MIT License. See LICENSE in the project root.

PROJECT OVERVIEW
----------------
Detects candy using an AI model running on the RDK X5 and activates
an interactive pumpkin mechanism controlled by an ESP32-C3.

FEATURES
--------
- MIPI camera input and real-time AI inference on the RDK X5 BPU
- Candy detection, bounding-box decoding, and non-maximum suppression
- Hardware JPEG encoding and WebSocket video streaming
- Browser-based live preview and status interface
- USB serial communication with the ESP32-C3
- State-based audio feedback

PROCESSING PIPELINE
-------------------
MIPI camera (hobot_vio.libsrcampy)
    -> NV12 frame
    -> Resize to 640x640
    -> BPU inference (hbm_runtime)
    -> Decode detections and NMS
    -> JPEG encoding (hardware JPU)
    -> WebSocket stream and terminal logs

REQUIREMENTS
------------
- D-Robotics RDK X5 running a compatible Ubuntu environment
- Compatible MIPI camera and supported camera runtime
- Compatible BPU model and matching class-label file
- ESP32-C3 controller connected over USB serial
- Python dependencies, including pyserial
- mpg123 and a working PulseAudio environment for audio playback

USAGE
-----
    python3 app/candy_hunter_pumpkin.py
    python3 app/candy_hunter_pumpkin.py --conf 0.35 --port 8080

Open the web interface at:
    http://<RDK-X5-IP>:8080/

NOTES
-----
Hardware-specific libraries and runtime components may only be available
in the supported RDK X5 environment. See README.md for setup instructions.
"""

import argparse
import asyncio
import json
import os
import signal
import struct
import sys
import time
from http import HTTPStatus

import numpy as np
import websockets
from scipy.special import softmax
from websockets.datastructures import Headers
from websockets.http11 import Response

import hbm_runtime
from hobot_vio import libsrcampy as srcampy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, "/app/pydev_demo")

import utils.preprocess_utils as pre_utils  # noqa: E402
import utils.postprocess_utils as post_utils  # noqa: E402
import utils.common_utils as common  # noqa: E402

import threading

try:
    import serial
except ImportError:
    serial = None

# Project root is the parent directory of app/
PROJECT_ROOT = os.path.dirname(HERE)

DEFAULT_MODEL = os.path.join(
    PROJECT_ROOT, "models", "weights_bayese_640x640_nv12.bin"
)
DEFAULT_LABELS = os.path.join(
    PROJECT_ROOT, "models", "candy_classes.names"
)
WEB_DIR = os.path.join(PROJECT_ROOT, "web")

# Audio assets are stored inside the project.
AUDIO_DIR = os.path.join(PROJECT_ROOT, "assets", "audio")
TRICK_AUDIO = os.path.join(AUDIO_DIR, "trick or treat1.mp3")
YUMMY_AUDIO = os.path.join(AUDIO_DIR, "audio.mp3")


# Audio playback configuration
import subprocess




class PumpkinAudio:
    """State-driven audio controlled by ESP32 feedback."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = "STANDBY"
        self.current_process = None
        self.running = True
        self.yummy_timer = None

        self.thread = threading.Thread(
            target=self._audio_loop, daemon=True
        )
        self.thread.start()


    def set_state(self, state):
        state = state.strip().upper()

        with self.lock:
            self.state = state

            # Cancel any pending yummy timer from the previous implementation.
            if self.yummy_timer is not None:
                self.yummy_timer.cancel()
                self.yummy_timer = None

            # Stop the hunting loop whenever the ESP32 leaves HUNT.
            if state != "HUNT":
                self._stop_process_locked()

        print(f"[pumpkin] state feedback: {state}", flush=True)

        # The ESP32 reports CLOSING after the 5-second mouth-open hold.
        # Play yummy once at this point without blocking serial feedback.
        if state == "CLOSING":
            threading.Thread(
                target=self._play_one_shot,
                args=(YUMMY_AUDIO,),
                daemon=True
            ).start()


    def _play_yummy_if_still_open(self):
        with self.lock:
            self.yummy_timer = None
            if not self.running or self.state != "MOUTH_OPEN":
                return

        self._play_one_shot(YUMMY_AUDIO)

    def _stop_process_locked(self):
        proc = self.current_process
        self.current_process = None

        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass

    def _start_process(self, filename):
        env = os.environ.copy()
        env["XDG_RUNTIME_DIR"] = "/run/user/1000"
        env["PULSE_SERVER"] = "unix:/run/user/1000/pulse/native"

        return subprocess.Popen(
            ["mpg123", "-q", "-o", "pulse", filename],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            user="sunrise",
        )

    def _play_one_shot(self, filename):
        with self.lock:
            if not self.running:
                return
            self._stop_process_locked()
            proc = self._start_process(filename)
            self.current_process = proc

        try:
            proc.wait()
        finally:
            with self.lock:
                if self.current_process is proc:
                    self.current_process = None

    def _audio_loop(self):
        while self.running:
            with self.lock:
                hunting = self.state == "HUNT"
                proc = self.current_process

                if hunting and (proc is None or proc.poll() is not None):
                    self.current_process = self._start_process(TRICK_AUDIO)

            time.sleep(0.1)

    def close(self):
        with self.lock:
            self.running = False

            if self.yummy_timer is not None:
                self.yummy_timer.cancel()
                self.yummy_timer = None

            self._stop_process_locked()



# Camera capture: 1280x720 (downscaled from the OV5647 1920x1080 sensor mode).
# Lower capture size cuts JPEG encode cost and WebSocket payload, which is what
# caps FPS when a browser is connected. Height must stay 16-aligned for the JPU.
CAM_W, CAM_H = 1280, 720
CAM_FPS = 30

# YOLO11 DFL decode parameters.
# This model exports 6 outputs: per scale (80/40/20) one class tensor
# (1, G, G, num_classes) and one DFL box tensor (1, G, G, 4*16).
GRIDS = (80, 40, 20)
STRIDES = (8, 16, 32)
REG_MAX = 16
DFL_WEIGHTS = np.arange(REG_MAX, dtype=np.float32)[None, None, :]


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------
class CandyDetector:
    """YOLO11 detector for the custom candy model (Bayes-e, NV12 input)."""

    def __init__(self, model_path, labels, score_thres=0.25, nms_thres=0.45):
        self.model = hbm_runtime.HB_HBMRuntime(model_path)
        self.model_name = self.model.model_names[0]
        self.input_names = self.model.input_names[self.model_name]
        self.output_names = self.model.output_names[self.model_name]
        self.input_shapes = self.model.input_shapes[self.model_name]
        self.output_shapes = self.model.output_shapes[self.model_name]
        self.output_quants = self.model.output_quants[self.model_name]

        self.input_H = self.input_shapes[self.input_names[0]][2]
        self.input_W = self.input_shapes[self.input_names[0]][3]

        self.labels = labels
        self.classes_num = len(labels)
        self.score_thres = score_thres
        self.nms_thres = nms_thres
        self.resize_type = 1

        # Pair each class tensor with its box tensor by grid shape.
        self.branches = []
        for grid, stride in zip(GRIDS, STRIDES):
            cls_c = [n for n in self.output_names
                     if tuple(self.output_shapes[n]) == (1, grid, grid, self.classes_num)]
            box_c = [n for n in self.output_names
                     if tuple(self.output_shapes[n]) == (1, grid, grid, 4 * REG_MAX)]
            if len(cls_c) != 1 or len(box_c) != 1:
                raise RuntimeError(
                    f"cannot match outputs for {grid}x{grid}: cls={cls_c} box={box_c}"
                )
            self.branches.append((grid, stride, cls_c[0], box_c[0]))

    def set_scheduling_params(self, priority=None, bpu_cores=None):
        kwargs = {}
        if priority is not None:
            kwargs["priority"] = {self.model_name: priority}
        if bpu_cores is not None:
            kwargs["bpu_cores"] = {self.model_name: bpu_cores}
        if kwargs:
            self.model.set_scheduling_params(**kwargs)

    def pre_process(self, nv12_bytes, src_w, src_h):
        y, uv = pre_utils.split_nv12_bytes(nv12_bytes, src_w, src_h)
        y_r, uv_r = pre_utils.resize_nv12_yuv(y, uv, self.input_H, self.input_W)
        y_in = y_r[..., None][None, ...]
        uv_in = uv_r[None, ...]
        nv12 = np.concatenate((y_in.reshape(-1), uv_in.reshape(-1)), axis=0)
        nv12 = nv12.reshape((1, self.input_H * 3 // 2, self.input_W, 1))
        return {self.model_name: {self.input_names[0]: nv12}}

    def forward(self, tensor):
        return self.model.run(tensor)[self.model_name]

    def post_process(self, outputs, img_w, img_h):
        fp32 = post_utils.dequantize_outputs(outputs, self.output_quants)
        raw_thres = -np.log(1.0 / self.score_thres - 1.0)  # inverse sigmoid

        all_boxes, all_scores, all_ids = [], [], []
        for grid, stride, cls_name, box_name in self.branches:
            cls_t = np.asarray(fp32[cls_name])
            box_t = np.asarray(fp32[box_name])

            scores, ids, valid = post_utils.filter_classification(cls_t, raw_thres)
            if len(valid) == 0:
                continue

            # DFL: softmax over the 16 bins per side, then expectation.
            b = box_t.reshape(-1, 4 * REG_MAX)[valid]
            ltrb = np.sum(softmax(b.reshape(-1, 4, REG_MAX), axis=2) * DFL_WEIGHTS, axis=2)
            anchor = post_utils.gen_anchor(grid)[valid]
            boxes = np.hstack([anchor - ltrb[:, 0:2], anchor + ltrb[:, 2:4]]) * stride

            all_boxes.append(boxes)
            all_scores.append(scores)
            all_ids.append(ids)

        if not all_boxes:
            return (np.zeros((0, 4), np.float32),
                    np.zeros((0,), np.int32),
                    np.zeros((0,), np.float32))

        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        ids = np.concatenate(all_ids, axis=0)

        keep = post_utils.NMS(boxes, scores, ids, self.nms_thres)
        boxes = boxes[keep].astype(np.float32)
        scores = scores[keep]
        ids = ids[keep].astype(np.int32)

        boxes = post_utils.scale_coords_back(
            boxes, img_w, img_h, self.input_W, self.input_H, self.resize_type
        )
        return boxes, ids, scores


# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------
class CameraManager:
    def __init__(self):
        self.cam = srcampy.Camera()
        # open_cam(pipe, video_index, fps, chn0_size, chn1_size)
        # Verified against /app/pydev_demo/09_web_display_camera_sample:
        #   open_cam(0, -1, 30, [512, 1920], [512, 1072])
        # chn0 = [512, 1920] (model channel), chn1 = [512, 1072] (JPEG channel,
        # 1072 = 16-aligned height for the JPU encoder).
        ret = self.cam.open_cam(0, -1, CAM_FPS, [512, CAM_W], [512, CAM_H])
        if ret != 0:
            raise RuntimeError(f"open_cam failed with code {ret}")
        self.encoder = srcampy.Encoder()
        self.encoder.encode(0, 3, CAM_W, CAM_H)  # chn 0, type 3 = JPEG

    def get_frame(self):
        img = self.cam.get_img(2, CAM_W, CAM_H)
        if img is None or len(img) != CAM_W * CAM_H * 3 // 2:
            return None
        return img

    def encode_jpeg(self, nv12_bytes):
        self.encoder.encode_file(nv12_bytes)
        return self.encoder.get_img()

    def close(self):
        try:
            self.cam.close_cam()
        except Exception:
            pass


# --------------------------------------------------------------------------
# WebSocket + HTTP server
# --------------------------------------------------------------------------
def pack_frame(meta: dict, jpeg: bytes) -> bytes:
    """Binary frame: 4-byte big-endian header length + JSON header + JPEG bytes."""
    header = json.dumps(meta, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(header)) + header + jpeg


class PumpkinController:
    """USB serial controller with ESP32 state-feedback monitoring."""

    def __init__(self, port="/dev/ttyACM0", baud=115200, audio=None):
        self.port = port
        self.baud = baud
        self.audio = audio
        self.device = None
        self.lock = threading.Lock()
        self.mode = "standby"
        self.running = False
        self.reader_thread = None

        if serial is None:
            print("[pumpkin] pyserial unavailable; controller disabled",
                  flush=True)
            return

        try:
            self.device = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                timeout=0.1,
                write_timeout=1
            )

            # Opening USB serial may reset the ESP32-C3.
            time.sleep(2)
            self.device.reset_input_buffer()

            # Start reading feedback before sending the initial command.
            self.running = True
            self.reader_thread = threading.Thread(
                target=self._read_loop,
                daemon=True
            )
            self.reader_thread.start()

            self.send("STANDBY")
            print(f"[pumpkin] connected on {self.port}", flush=True)

        except Exception as e:
            print(f"[pumpkin] serial connection failed: {e}", flush=True)
            self.running = False
            self.device = None

    def send(self, command):
        if self.device is None or not self.device.is_open:
            return False

        try:
            with self.lock:
                self.device.write((command + "\n").encode("ascii"))
                self.device.flush()

            print(f"[pumpkin] sent {command}", flush=True)
            return True

        except Exception as e:
            print(f"[pumpkin] serial write failed: {e}", flush=True)
            return False

    def _read_loop(self):
        while self.running and self.device is not None:
            try:
                line = self.device.readline().decode(
                    "utf-8", errors="replace"
                ).strip()

                if not line:
                    continue

                print(f"[pumpkin] ESP32: {line}", flush=True)

                if line.startswith("STATE:"):
                    state = line.split(":", 1)[1].strip().upper()

                    if self.audio is not None:
                        self.audio.set_state(state)

                    if state == "HUNT":
                        self.mode = "hunt"
                    elif state == "STANDBY":
                        self.mode = "standby"

            except Exception as e:
                if self.running:
                    print(f"[pumpkin] serial read error: {e}", flush=True)
                time.sleep(0.1)

    def set_mode(self, mode):
        if mode not in ("standby", "hunt"):
            return False

        command = "STANDBY" if mode == "standby" else "HUNT"

        if self.mode == mode:
            return True

        if not self.send(command):
            return False

        self.mode = mode
        return True


    def close(self):
        # Return the pumpkin to standby before disconnecting USB serial.
        if self.device is not None and self.device.is_open:
            print("[pumpkin] returning to standby before exit...", flush=True)
            self.send("STANDBY")

            # Give the ESP32 time to process the command and stop its animation.
            time.sleep(0.5)

        self.running = False

        if self.reader_thread is not None:
            self.reader_thread.join(timeout=1)

        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass




class DetectionServer:
    def __init__(self, host, port, detector, camera,
             pumpkin=None, log_every=1.0):
        self.host = host
        self.port = port
        self.detector = detector
        self.camera = camera
        self.log_every = log_every
        self.clients = set()
        self._inflight = set()
        self.last_log = 0.0
        self.frames = 0
        self.fps = 0.0
        self._fps_t0 = time.time()
        self._fps_n = 0
        self.pumpkin = pumpkin
        # Automatic candy-trigger state
        self.auto_candy_enabled = True
        self.candy_confidence_threshold = 0.65
        self.candy_was_visible = False

    # ---- HTTP: serve the web UI on the same port ----
    # websockets >= 14: process_request(connection, request) -> Response | None
    async def process_request(self, connection, request):
        path = request.path
        if path.startswith("/ws"):
            return None  # let the websocket handler take it

        rel = "index.html" if path in ("/", "") else path.split("?")[0].lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR) or not os.path.isfile(full):
            return Response(
                HTTPStatus.NOT_FOUND.value, "Not Found",
                Headers([("Content-Type", "text/plain")]), b"not found",
            )

        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        return Response(
            HTTPStatus.OK.value, "OK",
            Headers([("Content-Type", ctype), ("Content-Length", str(len(body)))]),
            body,
        )

    async def handler(self, websocket):
        peer = websocket.remote_address
        print(f"[ws] client connected: {peer}", flush=True)
        self.clients.add(websocket)

        try:
            async for message in websocket:
                if not isinstance(message, str):
                    continue

                try:
                    request = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if request.get("type") != "mode":
                    continue

                mode = request.get("mode")

                if mode not in ("standby", "hunt"):
                    response = {
                        "type": "control",
                        "ok": False,
                        "message": "Invalid mode"
                    }
                elif self.pumpkin is None:
                    response = {
                        "type": "control",
                        "ok": False,
                        "message": "Pumpkin controller unavailable"
                    }
                else:
                    ok = self.pumpkin.set_mode(mode)
                    response = {
                        "type": "control",
                        "ok": ok,
                        "mode": mode,
                        "message": (
                            "Mode command sent"
                            if ok else "ESP32-C3 serial command failed"
                        )
                    }

                await websocket.send(json.dumps(response))

        except Exception as e:
            print(f"[ws] client error: {e}", flush=True)

        finally:
            self.clients.discard(websocket)
            print(f"[ws] client disconnected: {peer}", flush=True)

    def _log(self, boxes, ids, scores, infer_ms):
        now = time.time()
        if now - self.last_log < self.log_every:
            return
        self.last_log = now
        if len(ids) == 0:
            print(f"[det] no candy  fps={self.fps:.1f} infer={infer_ms:.1f}ms", flush=True)
            return
        parts = []
        for box, cid, sc in zip(boxes, ids, scores):
            name = self.detector.labels[int(cid)]
            x1, y1, x2, y2 = [int(round(float(v))) for v in box]
            parts.append(f"{name} {sc:.2f} [{x1},{y1},{x2},{y2}]")
        print(
            f"[det] {len(ids)} object(s) fps={self.fps:.1f} infer={infer_ms:.1f}ms | "
            + " ; ".join(parts),
            flush=True,
        )

    async def run(self):
        async with websockets.serve(
            self.handler,
            self.host,
            self.port,
            process_request=self.process_request,
            max_size=None,
        ):
            print(f"[srv] web UI  : http://<board-ip>:{self.port}/", flush=True)
            print(f"[srv] websocket: ws://<board-ip>:{self.port}/ws", flush=True)
            print("[srv] Ctrl+C to stop", flush=True)

            # Limit browser preview to 15 FPS without limiting inference.
            preview_interval = 1.0 / 15.0
            last_preview_time = 0.0

            while True:
                await asyncio.sleep(0)  # yield
                t0 = time.time()

                nv12 = self.camera.get_frame()
                if nv12 is None:
                    await asyncio.sleep(0.01)
                    continue

                try:
                    tensor = self.detector.pre_process(nv12, CAM_W, CAM_H)
                    outputs = self.detector.forward(tensor)
                    boxes, ids, scores = self.detector.post_process(outputs, CAM_W, CAM_H)
                except Exception as e:
                    print(f"[err] inference failed: {e}", flush=True)
                    await asyncio.sleep(0.05)
                    continue

                infer_ms = (time.time() - t0) * 1000.0

                # fps accounting
                self._fps_n += 1
                dt = time.time() - self._fps_t0
                if dt >= 1.0:
                    self.fps = self._fps_n / dt
                    self._fps_n = 0
                    self._fps_t0 = time.time()

                self._log(boxes, ids, scores, infer_ms)

                # Trigger the pumpkin once when any candy first appears.
                # Audio is controlled only by ESP32 state feedback.
                candy_visible = any(
                    float(sc) >= self.candy_confidence_threshold
                    for sc in scores
                )

                if self.auto_candy_enabled and self.pumpkin is not None:
                    if candy_visible and not self.candy_was_visible:
                        if self.pumpkin.send("CANDY"):
                            print(
                                "[pumpkin] candy detected; trigger sent",
                                flush=True
                            )

                    self.candy_was_visible = candy_visible


                if not self.clients:
                    await asyncio.sleep(0.005)
                    continue

                # Throttle preview only; inference above still runs normally.
                now = time.monotonic()
                if now - last_preview_time < preview_interval:
                    await asyncio.sleep(0)
                    continue

                last_preview_time = now

                try:
                    jpeg = self.camera.encode_jpeg(nv12)

                except Exception as e:
                    print(f"[err] jpeg encode failed: {e}", flush=True)
                    await asyncio.sleep(0.05)
                    continue

                dets = []
                for box, cid, sc in zip(boxes, ids, scores):
                    dets.append({
                        "label": self.detector.labels[int(cid)],
                        "class_id": int(cid),
                        "score": round(float(sc), 4),
                        "box": [round(float(v), 1) for v in box],
                    })

                meta = {
                    "ts": int(time.time() * 1000),
                    "width": CAM_W,
                    "height": CAM_H,
                    "fps": round(self.fps, 1),
                    "infer_ms": round(infer_ms, 1),
                    "detections": dets,
                }
                payload = pack_frame(meta, jpeg)

                # Send concurrently and never block the capture loop on a slow
                # client: a stalled tab used to serialize the whole pipeline.
                # If a client still has a frame in flight, skip this frame for it.
                async def _send(ws):
                    if ws in self._inflight:
                        return
                    self._inflight.add(ws)
                    try:
                        await asyncio.wait_for(ws.send(payload), timeout=1.0)
                    except Exception:
                        self.clients.discard(ws)
                    finally:
                        self._inflight.discard(ws)

                await asyncio.gather(*(_send(ws) for ws in list(self.clients)),
                                     return_exceptions=True)

                self.frames += 1
                await asyncio.sleep(0)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Candy detector: MIPI camera -> terminal + WebSocket")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="BPU .bin model")
    ap.add_argument("--labels", default=DEFAULT_LABELS, help="class names file")
    ap.add_argument("--conf", type=float, default=0.25, help="confidence threshold")
    ap.add_argument("--nms", type=float, default=0.45, help="NMS IoU threshold")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--priority", type=int, default=0, help="BPU priority 0-255")
    ap.add_argument("--bpu-cores", nargs="+", type=int, default=[0])
    ap.add_argument("--log-every", type=float, default=1.0,
                    help="seconds between terminal detection logs")
    args = ap.parse_args()

    if not os.path.isfile(args.model):
        sys.exit(f"model not found: {args.model}")
    if not os.path.isfile(args.labels):
        sys.exit(f"labels not found: {args.labels}")

    labels = common.load_class_names(args.labels)
    print(f"[init] classes ({len(labels)}): {labels}", flush=True)

    detector = CandyDetector(args.model, labels, args.conf, args.nms)
    detector.set_scheduling_params(priority=args.priority, bpu_cores=args.bpu_cores)
    print(f"[init] model input: {detector.input_W}x{detector.input_H} "
          f"outputs={len(detector.output_names)}", flush=True)

    camera = CameraManager()
    print(f"[init] MIPI camera opened: {CAM_W}x{CAM_H} @ {CAM_FPS}fps", flush=True)

    audio = PumpkinAudio()
    pumpkin = PumpkinController(audio=audio)

    server = DetectionServer(
        args.host, args.port, detector, camera,
        pumpkin=pumpkin,
        log_every=args.log_every
    )

    def _stop(sig, frame):
        print("\n[exit] stopping...", flush=True)
        pumpkin.close()
        audio.close()
        camera.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        asyncio.run(server.run())
    finally:
        pumpkin.close()
        audio.close()
        camera.close()


if __name__ == "__main__":
    main()
