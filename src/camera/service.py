"""
High-performance, non-blocking camera video capture service.
Supports:
1. Native Raspberry Pi Picamera2 libcamera/PiSP backend.
2. Native rpicam-vid / libcamera-vid hardware MJPEG stream subprocess.
3. Standard OpenCV V4L2 / DirectShow (USB webcams / Windows).
4. Fallback animated synthetic test pattern.
"""

import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Optional
import numpy as np
from PIL import Image

# Automatically add Debian / Raspberry Pi OS system dist-packages to sys.path
# so Picamera2 / libcamera can be loaded inside virtual environments (.venv).
if platform.system() == "Linux":
    for p in ("/usr/lib/python3/dist-packages", "/usr/local/lib/python3/dist-packages"):
        if p not in sys.path and os.path.exists(p):
            sys.path.append(p)

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None

from src.config import AppConfig

logger = logging.getLogger(__name__)


class CameraService:
    """Manages video capture hardware, background frame polling, and JPEG encoding."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.device_index: int = getattr(config, "camera_device_index", 0)
        self.target_fps: int = getattr(config, "camera_fps", 30)
        self.width: int = getattr(config, "camera_width", 640)
        self.height: int = getattr(config, "camera_height", 480)
        self.jpeg_quality: int = getattr(config, "camera_jpeg_quality", 80)

        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cap: Optional[Any] = None
        self._picam: Optional[Any] = None
        self._rpicam_process: Optional[subprocess.Popen] = None
        self._active_backend: str = "none"
        self._using_test_pattern = False
        self._ready_event = threading.Event()

        # Frame buffers
        self._latest_bgr: Optional[np.ndarray] = None
        self._latest_rgb: Optional[np.ndarray] = None
        self._latest_jpeg: Optional[bytes] = None
        self._latest_timestamp: float = 0.0
        self._frame_count: int = 0

        # Preview listeners (GUI callbacks)
        self._listeners_lock = threading.Lock()
        self._preview_listeners: list[Callable[[Image.Image], None]] = []

        # Generate initial test pattern frame buffer
        initial_bgr = self._generate_test_pattern_bgr(0.0)
        initial_rgb = (
            cv2.cvtColor(initial_bgr, cv2.COLOR_BGR2RGB)
            if cv2 is not None
            else initial_bgr[:, :, ::-1].copy()
        )
        self._initial_bgr = initial_bgr
        self._initial_rgb = initial_rgb

    @property
    def is_running(self) -> bool:
        """Return True if background capture thread is running."""
        return self._is_running

    @property
    def is_using_test_pattern(self) -> bool:
        """Return True if fallback test pattern is active due to no camera hardware."""
        return self._using_test_pattern

    @property
    def active_backend(self) -> str:
        """Return active backend: 'picamera2', 'rpicam_vid', 'opencv', or 'synthetic'."""
        return self._active_backend

    def add_preview_listener(self, callback: Callable[[Image.Image], None]) -> None:
        """Register a callback that receives every newly captured PIL frame."""
        with self._listeners_lock:
            if callback not in self._preview_listeners:
                self._preview_listeners.append(callback)

    def remove_preview_listener(self, callback: Callable[[Image.Image], None]) -> None:
        """Unregister a preview listener callback."""
        with self._listeners_lock:
            if callback in self._preview_listeners:
                self._preview_listeners.remove(callback)

    def start(self, wait_ready: bool = False, timeout: float = 3.0) -> bool:
        """Start background camera capture thread."""
        with self._lock:
            if self._is_running:
                return True
            self._is_running = True
            self._frame_count = 0
            self._ready_event.clear()
            # Prime buffers so frames are available immediately
            self._latest_bgr = self._initial_bgr.copy()
            self._latest_rgb = self._initial_rgb.copy()
            self._latest_timestamp = time.monotonic()

        self._thread = threading.Thread(
            target=self._capture_worker,
            name="CameraCaptureWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info("Camera service started on device index %d.", self.device_index)

        # Dispatch immediate primed frame to listeners if any registered
        with self._listeners_lock:
            listeners = list(self._preview_listeners)
        if listeners:
            try:
                pil_init = Image.fromarray(self._initial_rgb)
                for listener in listeners:
                    try:
                        listener(pil_init)
                    except Exception:
                        pass
            except Exception:
                pass

        if wait_ready:
            self._ready_event.wait(timeout=timeout)

        return True

    def wait_until_ready(self, timeout: float = 2.0) -> bool:
        """Wait until the capture worker has established camera or simulation."""
        return self._ready_event.wait(timeout=timeout)

    def stop(self) -> None:
        """Stop background camera capture thread and release hardware resources."""
        with self._lock:
            if not self._is_running:
                return
            self._is_running = False
            self._ready_event.clear()

        # Terminate rpicam-vid subprocess if running
        if self._rpicam_process is not None:
            try:
                self._rpicam_process.terminate()
                self._rpicam_process.wait(timeout=1.0)
            except Exception:
                try:
                    self._rpicam_process.kill()
                except Exception:
                    pass
            self._rpicam_process = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

        with self._lock:
            if self._picam is not None:
                try:
                    self._picam.stop()
                    self._picam.close()
                except Exception as e:
                    logger.debug("Error releasing Picamera2: %s", e)
                self._picam = None

            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception as e:
                    logger.debug("Error releasing OpenCV camera: %s", e)
                self._cap = None

            self._latest_bgr = None
            self._latest_rgb = None
            self._latest_jpeg = None
            self._active_backend = "none"
            self._using_test_pattern = False

        logger.info("Camera service stopped cleanly.")

    def set_device(self, index: int) -> None:
        """Change camera device index and restart capture if running."""
        if self.device_index == index:
            return
        logger.info("Switching camera device from %d to %d", self.device_index, index)
        self.device_index = index
        if self._is_running:
            self.stop()
            self.start(wait_ready=True)

    def get_latest_frame_pil(
        self, target_size: Optional[tuple[int, int]] = None
    ) -> Optional[Image.Image]:
        """
        Return the latest frame as a PIL Image (RGB format).
        If target_size is provided, performs fast SIMD resizing via OpenCV.
        """
        with self._lock:
            if not self._is_running or self._latest_bgr is None:
                return None
            bgr_copy = self._latest_bgr.copy()
            rgb_copy = self._latest_rgb.copy() if (target_size is None and self._latest_rgb is not None) else None

        if target_size is not None:
            w, h = max(10, int(target_size[0])), max(10, int(target_size[1]))
            if cv2 is not None:
                try:
                    resized_bgr = cv2.resize(bgr_copy, (w, h), interpolation=cv2.INTER_LINEAR)
                    rgb_scaled = cv2.cvtColor(resized_bgr, cv2.COLOR_BGR2RGB)
                    return Image.fromarray(rgb_scaled)
                except Exception as e:
                    logger.debug("OpenCV resize failed: %s", e)
            # Fallback to Pillow scaling
            try:
                rgb = cv2.cvtColor(bgr_copy, cv2.COLOR_BGR2RGB) if cv2 is not None else bgr_copy[:, :, ::-1]
                pil_raw = Image.fromarray(rgb)
                return pil_raw.resize((w, h), Image.Resampling.BILINEAR)
            except Exception as e:
                logger.debug("Pillow resize fallback failed: %s", e)
                return None

        if rgb_copy is None:
            rgb_copy = (
                cv2.cvtColor(bgr_copy, cv2.COLOR_BGR2RGB)
                if cv2 is not None
                else bgr_copy[:, :, ::-1].copy()
            )
        try:
            return Image.fromarray(rgb_copy)
        except Exception as e:
            logger.debug("Failed converting frame to PIL Image: %s", e)
            return None

    def get_latest_frame_jpeg(self, quality: Optional[int] = None) -> Optional[bytes]:
        """
        Return the latest frame compressed as JPEG bytes.
        Suitable for sending to Gemini Multimodal Live API.
        """
        with self._lock:
            if not self._is_running:
                return None
            # Return pre-encoded JPEG if available and default quality
            if quality is None and self._latest_jpeg is not None:
                return self._latest_jpeg
            if self._latest_bgr is None:
                return None
            bgr_copy = self._latest_bgr.copy()

        q = quality if quality is not None else self.jpeg_quality
        if cv2 is not None:
            try:
                encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), q]
                success, buffer = cv2.imencode(".jpg", bgr_copy, encode_params)
                if success:
                    return buffer.tobytes()
            except Exception as e:
                logger.warning("cv2 imencode failed: %s", e)

        # Fallback to Pillow JPEG encoding
        try:
            rgb = cv2.cvtColor(bgr_copy, cv2.COLOR_BGR2RGB) if cv2 else bgr_copy
            pil_img = Image.fromarray(rgb)
            bio = io.BytesIO()
            pil_img.save(bio, format="JPEG", quality=q)
            return bio.getvalue()
        except Exception as e:
            logger.warning("Pillow JPEG encode fallback failed: %s", e)
            return None

    def capture_snapshot(self) -> Optional[tuple[Image.Image, bytes]]:
        """Grab a fresh snapshot returning (PIL.Image, jpeg_bytes)."""
        pil_img = self.get_latest_frame_pil()
        jpeg_bytes = self.get_latest_frame_jpeg()
        if pil_img and jpeg_bytes:
            return pil_img, jpeg_bytes
        return None

    def _open_picamera2(self) -> bool:
        """Attempt to open hardware camera using native Raspberry Pi Picamera2 libcamera pipeline."""
        if Picamera2 is None:
            return False

        try:
            logger.info("Attempting to initialize native Picamera2 libcamera/PiSP backend...")
            picam = Picamera2(self.device_index)
            # Configure hardware preview stream in BGR888 format
            camera_config = picam.create_preview_configuration(
                main={"format": "BGR888", "size": (self.width, self.height)},
                controls={"FrameRate": self.target_fps},
            )
            picam.configure(camera_config)
            picam.start()
            self._picam = picam
            self._active_backend = "picamera2"
            self._using_test_pattern = False
            logger.info(
                "✓ Opened native Picamera2 device %d (%dx%d @ %d FPS) with hardware PiSP acceleration.",
                self.device_index, self.width, self.height, self.target_fps
            )
            return True
        except Exception as e:
            logger.info("Picamera2 backend failed to initialize: %s", e)
            if hasattr(self, "_picam") and self._picam is not None:
                try:
                    self._picam.close()
                except Exception:
                    pass
                self._picam = None

        return False

    def _open_rpicam_vid(self) -> bool:
        """Attempt to stream MJPEG directly from native rpicam-vid / libcamera-vid binary."""
        rpicam_bin = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
        if not rpicam_bin:
            return False

        cmd = [
            rpicam_bin,
            "-t", "0",
            "--camera", str(self.device_index),
            "--width", str(self.width),
            "--height", str(self.height),
            "--framerate", str(self.target_fps),
            "--codec", "mjpeg",
            "--quality", str(self.jpeg_quality),
            "-o", "-",
            "-n",
            "--inline",
        ]

        try:
            logger.info("Launching native Raspberry Pi hardware stream: %s", " ".join(cmd[:6]))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1024 * 64,
            )
            time.sleep(0.3)
            if proc.poll() is None:
                self._rpicam_process = proc
                self._active_backend = "rpicam_vid"
                self._using_test_pattern = False
                logger.info("✓ Opened native rpicam-vid hardware MJPEG stream.")
                return True
            else:
                proc.kill()
        except Exception as e:
            logger.info("rpicam-vid launcher failed: %s", e)

        return False

    def _open_opencv(self) -> bool:
        """Attempt to open camera using standard OpenCV V4L2/DirectShow backend."""
        if cv2 is None:
            return False

        try:
            cap = cv2.VideoCapture(self.device_index)
            if cap.isOpened():
                ret, _ = cap.read()
                if ret:
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    cap.set(cv2.CAP_PROP_FPS, self.target_fps)
                    self._cap = cap
                    self._active_backend = "opencv"
                    self._using_test_pattern = False
                    logger.info(
                        "✓ Opened OpenCV camera index %d (%dx%d @ %d FPS)",
                        self.device_index, self.width, self.height, self.target_fps
                    )
                    return True
                else:
                    cap.release()
        except Exception as e:
            logger.debug("Failed opening OpenCV camera index %d: %s", self.device_index, e)

        return False

    def _open_camera(self) -> bool:
        """Attempt to open hardware camera across all platforms with fallback hierarchy."""
        # 1. On Linux / Pi 5: Try Picamera2
        if platform.system() == "Linux":
            if self._open_picamera2():
                return True
            # 2. Try rpicam-vid / libcamera-vid CLI pipe
            if self._open_rpicam_vid():
                return True

        # 3. Try standard OpenCV (USB webcams, Windows, macOS)
        if self._open_opencv():
            return True

        return False

    def _capture_worker(self) -> None:
        """Background thread worker continuously polling frames."""
        has_camera = self._open_camera()
        if not has_camera:
            self._using_test_pattern = True
            self._active_backend = "synthetic"
            logger.info("Using simulated camera test pattern for video streaming.")

        self._ready_event.set()
        frame_interval = 1.0 / max(1, self.target_fps)
        sim_angle = 0.0
        mjpeg_buffer = bytearray()

        while self._is_running:
            loop_start = time.perf_counter()
            bgr_frame: Optional[np.ndarray] = None
            jpeg_data: Optional[bytes] = None

            # 1. Read frame from rpicam-vid MJPEG pipe if active
            if self._rpicam_process is not None and self._rpicam_process.stdout:
                try:
                    while self._is_running:
                        chunk = self._rpicam_process.stdout.read(4096)
                        if not chunk:
                            break
                        mjpeg_buffer.extend(chunk)
                        start = mjpeg_buffer.find(b"\xff\xd8")
                        end = mjpeg_buffer.find(b"\xff\xd9")
                        if start != -1 and end != -1 and end > start:
                            jpeg_data = bytes(mjpeg_buffer[start : end + 2])
                            mjpeg_buffer = mjpeg_buffer[end + 2 :]
                            if cv2 is not None:
                                bgr_frame = cv2.imdecode(
                                    np.frombuffer(jpeg_data, np.uint8), cv2.IMREAD_COLOR
                                )
                            else:
                                bio = io.BytesIO(jpeg_data)
                                pil_tmp = Image.open(bio)
                                bgr_frame = np.array(pil_tmp)[:, :, ::-1]
                            break
                except Exception as e:
                    logger.warning("Error parsing rpicam-vid MJPEG stream: %s", e)
                    bgr_frame = None

            # 2. Read frame from Picamera2 if active
            elif self._picam is not None:
                try:
                    bgr_frame = self._picam.capture_array("main")
                except Exception as e:
                    logger.warning("Picamera2 frame capture error: %s", e)
                    has_camera = False
                    self._using_test_pattern = True

            # 3. Read frame from OpenCV if active
            elif has_camera and self._cap is not None:
                try:
                    ret, frame = self._cap.read()
                    if ret and frame is not None:
                        bgr_frame = frame
                    else:
                        logger.warning("Camera read returned empty frame; switching to test pattern.")
                        has_camera = False
                        self._using_test_pattern = True
                except Exception as e:
                    logger.warning("Error reading camera frame: %s", e)
                    has_camera = False
                    self._using_test_pattern = True

            # 4. Fallback synthetic pattern if hardware frame is unavailable
            if bgr_frame is None:
                sim_angle = (sim_angle + 0.05) % (2 * 3.14159)
                bgr_frame = self._generate_test_pattern_bgr(sim_angle)

            # Convert to RGB for Pillow / GUI only when listeners exist
            with self._listeners_lock:
                has_listeners = len(self._preview_listeners) > 0
                listeners = list(self._preview_listeners) if has_listeners else []

            rgb_frame = None
            if has_listeners:
                if cv2 is not None:
                    rgb_frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
                else:
                    rgb_frame = bgr_frame[:, :, ::-1].copy()

            # Store in thread-safe buffer
            now = time.monotonic()
            with self._lock:
                self._latest_bgr = bgr_frame
                self._latest_rgb = rgb_frame
                self._latest_jpeg = jpeg_data
                self._latest_timestamp = now
                self._frame_count += 1

            if listeners and rgb_frame is not None:
                try:
                    pil_img = Image.fromarray(rgb_frame)
                    for listener in listeners:
                        try:
                            listener(pil_img)
                        except Exception as cb_err:
                            logger.debug("Preview listener callback error: %s", cb_err)
                except Exception as pil_err:
                    logger.debug("Error creating PIL frame for listeners: %s", pil_err)

            # Sleep to maintain target FPS
            elapsed = time.perf_counter() - loop_start
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

        # Cleanup backends upon worker exit
        if self._rpicam_process is not None:
            try:
                self._rpicam_process.terminate()
            except Exception:
                pass
            self._rpicam_process = None

        if self._picam is not None:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass
            self._picam = None

        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def _generate_test_pattern_bgr(self, sim_angle: float) -> np.ndarray:
        """Generate an animated test pattern frame when physical camera is absent."""
        w, h = self.width, self.height
        # Dark cyber grid background
        frame = np.full((h, w, 3), (24, 20, 20), dtype=np.uint8)

        # Draw grid lines
        grid_spacing = 40
        for x in range(0, w, grid_spacing):
            frame[:, x:x+1] = (38, 32, 32)
        for y in range(0, h, grid_spacing):
            frame[y:y+1, :] = (38, 32, 32)

        # Bouncing animated orbit
        cx = int(w / 2 + (w / 4) * np.cos(sim_angle))
        cy = int(h / 2 + (h / 4) * np.sin(sim_angle * 1.5))
        if cv2 is not None:
            # Draw targeting reticle
            cv2.circle(frame, (cx, cy), 24, (161, 227, 166), 2)  # Catppuccin Green
            cv2.circle(frame, (cx, cy), 6, (250, 179, 137), -1)   # Peach dot
            cv2.putText(
                frame, "CUBEY VISION - SIMULATED FEED",
                (16, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (205, 214, 244), 2
            )
            cv2.putText(
                frame, f"Device Index: {self.device_index} (Virtual)",
                (16, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (186, 194, 222), 1
            )
            timestamp_str = time.strftime("%H:%M:%S")
            cv2.putText(
                frame, f"Time: {timestamp_str} | Target: {self.target_fps} FPS",
                (16, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (166, 173, 200), 1
            )
        return frame
