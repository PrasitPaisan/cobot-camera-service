import os

from dotenv import load_dotenv

load_dotenv()


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


class Settings:
    # DEBUG shows every PLC poll read/write and every detection box; INFO
    # (default) shows only state transitions and results.
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    CAMERA_IP = os.getenv("CAMERA_IP", "192.168.3.100")
    CAMERA_NET_IP = os.getenv("CAMERA_NET_IP", "192.168.3.1")  # IP of the NIC the camera is wired to
    CAMERA_GET_FRAME_TIMEOUT_MS = _env_int("CAMERA_GET_FRAME_TIMEOUT_MS", 1000)
    CAMERA_RECONNECT_SECONDS = _env_float("CAMERA_RECONNECT_SECONDS", 3)
    CAMERA_MAX_CONSECUTIVE_ERRORS = _env_int("CAMERA_MAX_CONSECUTIVE_ERRORS", 10)

    # GenICam auto-exposure/auto-gain mode, applied once right after the camera
    # opens -- same underlying feature as MVS's one-click brightness adjustment.
    # One of "Off" / "Once" / "Continuous". Continuous keeps self-correcting
    # brightness as the scene's lighting changes, fixing "camera opens dark".
    CAMERA_EXPOSURE_AUTO = os.getenv("CAMERA_EXPOSURE_AUTO", "Continuous")
    CAMERA_GAIN_AUTO = os.getenv("CAMERA_GAIN_AUTO", "Continuous")

    # Continuous exposure/gain needs real frames flowing to measure brightness
    # against -- which trigger mode never provides between triggers. So on
    # connect we free-run for this long first (letting ExposureAuto/GainAuto
    # converge against live frames) before switching into TriggerMode=ON for
    # the rest of the run. Without this, the camera opens dark and never
    # self-corrects once idle in trigger mode.
    CAMERA_BRIGHTNESS_WARMUP_SECONDS = _env_float("CAMERA_BRIGHTNESS_WARMUP_SECONDS", 8.0)

    # If a triggered frame's mean pixel value falls below this, it counts as
    # "dark" (0-255 scale). After CAMERA_DARK_FRAME_MAX_CONSECUTIVE of those
    # in a row, the camera re-runs the brightness warmup automatically --
    # recovers on its own from a lighting glitch mid-run (e.g. a power dip)
    # instead of staying locked onto a stale dark exposure until someone
    # notices and restarts the service. Kept at 1 -- each dark frame here is
    # a real ampoule judged on a bad image (a real reject, not just a log
    # line), so react on the very first one rather than let more than one
    # go by while waiting to "confirm" the problem.
    CAMERA_DARK_FRAME_THRESHOLD = _env_float("CAMERA_DARK_FRAME_THRESHOLD", 40.0)
    CAMERA_DARK_FRAME_MAX_CONSECUTIVE = _env_int("CAMERA_DARK_FRAME_MAX_CONSECUTIVE", 1)

    # Symmetric upper bound: if the light comes back ON after an outage, the
    # high exposure/gain the camera climbed to while compensating for the
    # dark is now way too much -- frames come out blown-out/overexposed
    # instead, which is just as bad for detection as too-dark. Same
    # recalibration path handles this; it just also needs a reason to fire.
    CAMERA_BRIGHT_FRAME_THRESHOLD = _env_float("CAMERA_BRIGHT_FRAME_THRESHOLD", 180.0)

    # Mid-run recalibration (triggered by the dark-frame check above) blocks
    # whatever PLC trigger comes in next, so every second here is downtime
    # on a line that's actively running. Set long enough to converge in one
    # pass (testing showed ~10s is enough even from a cold/dark start) --
    # since CAMERA_DARK_FRAME_MAX_CONSECUTIVE=1 means we only get one shot
    # before the next real ampoule is judged on whatever this produces.
    CAMERA_RECALIBRATE_WARMUP_SECONDS = _env_float("CAMERA_RECALIBRATE_WARMUP_SECONDS", 10.0)

    # Same PLC that backend_fast talks to, but on its own dedicated port --
    # camera_service never shares a connection slot with backend_fast's
    # PLC_READ_PORT/PLC_WRITE_PORT (5000/4999), avoiding the PLC's Ethernet
    # module's low limit on simultaneous connections. Used for reading
    # CAM_TRIG_DEVICE.
    PLC_HOST = os.getenv("PLC_HOST", "192.168.3.250")
    PLC_PORT = _env_int("PLC_PORT", 5001)
    CAM_TRIG_DEVICE = os.getenv("CAM_TRIG_DEVICE", "M6000")
    PLC_TRIGGER_POLL_SECONDS = _env_float("PLC_TRIGGER_POLL_SECONDS", 0.5)
    PLC_RECONNECT_SECONDS = _env_float("PLC_RECONNECT_SECONDS", 3)

    # Same cobot idle/busy bits backend_fast already polls (app/api/routes/
    # prescription.py). Used here only to gate how fast we poll the trigger
    # bit: slow while idle, fast only while the cobot is actually running.
    RUNNING_DEVICE = os.getenv("RUNNING_DEVICE", "M286")
    READY_DEVICE = os.getenv("READY_DEVICE", "M287")
    PLC_IDLE_POLL_SECONDS = _env_float("PLC_IDLE_POLL_SECONDS", 5)

    # Delay between the trigger edge and actually taking the snapshot -- lets
    # motion/vibration settle first so the saved image isn't blurry.
    CAMERA_TRIGGER_SETTLE_SECONDS = _env_float("CAMERA_TRIGGER_SETTLE_SECONDS", 0.5)

    # Full-resolution frame saved on every PLC trigger, sorted into OK/NG
    # subfolders by the judgment made -- a growing labeled dataset for
    # retraining the model later. OK saves the annotated (boxed) copy, NG
    # saves the plain frame (nothing to box).
    TRAINING_DATA_DIR = os.getenv("TRAINING_DATA_DIR", "adddata")

    # YOLO model (best.pt) used to judge each trigger snapshot: ampoule
    # detected at/above this confidence -> OK, otherwise -> NG.
    VISION_MODEL_PATH = os.getenv("VISION_MODEL_PATH", "best.pt")
    VISION_CONF_THRESHOLD = _env_float("VISION_CONF_THRESHOLD", 0.25)

    # Result bits pulsed back to the PLC (1 -> hold -> 0) after each trigger
    # snapshot is judged. Must match the devices actually wired on the PLC side
    # -- confirm with the PLC program before relying on these defaults.
    CAM_RESULT_OK_DEVICE = os.getenv("CAM_RESULT_OK_DEVICE", "M5000")
    CAM_RESULT_NG_DEVICE = os.getenv("CAM_RESULT_NG_DEVICE", "M5001")
    CAM_RESULT_HOLD_SECONDS = _env_float("CAM_RESULT_HOLD_SECONDS", 0.5)


settings = Settings()
