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

    # Same FPS as the camera's free-run rate -- only the resolution is reduced before
    # JPEG encoding, to keep the push to cloud smooth without eating a lot of bandwidth.
    STREAM_MAX_WIDTH = _env_int("STREAM_MAX_WIDTH", 960)
    JPEG_QUALITY = _env_int("JPEG_QUALITY", 80)

    # GenICam auto-exposure/auto-gain mode, applied once right after the camera
    # opens -- same underlying feature as MVS's one-click brightness adjustment.
    # One of "Off" / "Once" / "Continuous". Continuous keeps self-correcting
    # brightness as the scene's lighting changes, fixing "camera opens dark".
    CAMERA_EXPOSURE_AUTO = os.getenv("CAMERA_EXPOSURE_AUTO", "Continuous")
    CAMERA_GAIN_AUTO = os.getenv("CAMERA_GAIN_AUTO", "Continuous")

    CLOUD_INGEST_URL = os.getenv("CLOUD_INGEST_URL", "http://localhost:8000/camera/ingest")
    CLOUD_INGEST_TOKEN = os.getenv("CLOUD_INGEST_TOKEN", "")
    CLOUD_PUSH_TIMEOUT_SECONDS = _env_float("CLOUD_PUSH_TIMEOUT_SECONDS", 3)

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
