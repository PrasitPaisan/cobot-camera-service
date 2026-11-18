"""
Owns the Hikrobot GigE camera: connects by fixed IP and sits idle (no
free-run, no continuous stream) until a PLC trigger edge comes in
(snap_event, set by app/plc/trigger.py). On a trigger, fires the camera's
own software trigger (TriggerMode=ON, TriggerSource=Software) so the camera
only ever produces a frame when one is actually needed, converts it to BGR8
full resolution, and hands it to on_trigger_frame() -- main.py wires that to
detection + saving. Nothing here pushes a live stream anywhere; the camera
is otherwise silent on the network between triggers.
"""

import logging
import threading
import time
from ctypes import c_ubyte, memset, byref, sizeof
from typing import Callable, Optional

import numpy as np

from app.config import settings

logger = logging.getLogger("camera_service.capture")

_SDK_NAMES = (
    "MvCamera",
    "MV_CC_DEVICE_INFO",
    "MV_GIGE_DEVICE_INFO",
    "MV_GIGE_DEVICE",
    "MV_ACCESS_Exclusive",
    "MV_TRIGGER_MODE_OFF",
    "MV_TRIGGER_MODE_ON",
    "MV_FRAME_OUT",
    "MV_CC_PIXEL_CONVERT_PARAM_EX",
    "MVCC_FLOATVALUE",
    "PixelType_Gvsp_BGR8_Packed",
)
_sdk_loaded = False


def _load_sdk() -> None:
    global _sdk_loaded
    if _sdk_loaded:
        return

    from app.camera.sdk_path import ensure_sdk_importable

    ensure_sdk_importable()

    import MvCameraControl_class as _mv

    for name in _SDK_NAMES:
        globals()[name] = getattr(_mv, name)
    _sdk_loaded = True


def _ip_to_int(ip: str) -> int:
    a, b, c, d = (int(part) for part in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def _frame_to_bgr(cam, frame) -> Optional[np.ndarray]:
    """Full-resolution BGR8 conversion of the current frame buffer, no resizing."""
    width = frame.stFrameInfo.nWidth
    height = frame.stFrameInfo.nHeight

    dst_size = width * height * 3
    convert_param = MV_CC_PIXEL_CONVERT_PARAM_EX()
    memset(byref(convert_param), 0, sizeof(convert_param))
    convert_param.nWidth = width
    convert_param.nHeight = height
    convert_param.pSrcData = frame.pBufAddr
    convert_param.nSrcDataLen = frame.stFrameInfo.nFrameLen
    convert_param.enSrcPixelType = frame.stFrameInfo.enPixelType
    convert_param.enDstPixelType = PixelType_Gvsp_BGR8_Packed
    convert_param.pDstBuffer = (c_ubyte * dst_size)()
    convert_param.nDstBufferSize = dst_size

    ret = cam.MV_CC_ConvertPixelTypeEx(convert_param)
    if ret != 0:
        logger.warning("Pixel convert failed, ret[0x%x]", ret)
        return None

    buf = np.ctypeslib.as_array(convert_param.pDstBuffer, shape=(convert_param.nDstLen,))
    # Copy out of the SDK-owned buffer -- it's reused/freed as soon as
    # MV_CC_FreeImageBuffer runs, right after this function returns.
    return buf.reshape((height, width, 3)).copy()


def _teardown_camera(cam) -> None:
    if cam is None:
        return
    for method in ("MV_CC_StopGrabbing", "MV_CC_CloseDevice", "MV_CC_DestroyHandle"):
        try:
            getattr(cam, method)()
        except Exception:
            pass


def _mean_brightness(bgr: np.ndarray) -> float:
    return float(bgr.mean())


def _free_run_and_enter_trigger_mode(
    cam, stop_event: threading.Event, warmup_seconds: float, reason: str
) -> None:
    """Free-runs (TriggerMode=OFF) for warmup_seconds so ExposureAuto/
    GainAuto=Continuous has real frames to converge brightness against, then
    switches into software-trigger mode. Used both on initial connect (can
    afford a long warmup -- nothing's waiting on it yet) and to recalibrate
    later if triggered frames come out dark, e.g. after a lighting glitch
    mid-run (kept short there -- it blocks the next PLC trigger)."""
    # Tolerate not-currently-grabbing (e.g. the very first call, before
    # anything has started) -- this is just making sure we're not already
    # mid-grab before changing TriggerMode.
    cam.MV_CC_StopGrabbing()

    ret = cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_OFF)
    if ret != 0:
        raise RuntimeError(f"set trigger mode off fail! ret[0x{ret:x}]")

    ret = cam.MV_CC_SetEnumValueByString("ExposureAuto", settings.CAMERA_EXPOSURE_AUTO)
    if ret != 0:
        logger.warning(
            "Set ExposureAuto=%s failed, ret[0x%x]", settings.CAMERA_EXPOSURE_AUTO, ret
        )

    ret = cam.MV_CC_SetEnumValueByString("GainAuto", settings.CAMERA_GAIN_AUTO)
    if ret != 0:
        logger.warning("Set GainAuto=%s failed, ret[0x%x]", settings.CAMERA_GAIN_AUTO, ret)

    ret = cam.MV_CC_StartGrabbing()
    if ret != 0:
        raise RuntimeError(f"start grabbing fail! ret[0x{ret:x}]")

    logger.info(
        "Camera at %s free-running %.1fs to let brightness settle (%s)",
        settings.CAMERA_IP,
        warmup_seconds,
        reason,
    )
    warmup_frame = MV_FRAME_OUT()
    warmup_deadline = time.monotonic() + warmup_seconds
    while time.monotonic() < warmup_deadline and not stop_event.is_set():
        memset(byref(warmup_frame), 0, sizeof(warmup_frame))
        ret = cam.MV_CC_GetImageBuffer(warmup_frame, settings.CAMERA_GET_FRAME_TIMEOUT_MS)
        if ret == 0 and warmup_frame.pBufAddr is not None:
            cam.MV_CC_FreeImageBuffer(warmup_frame)

    ret = cam.MV_CC_StopGrabbing()
    if ret != 0:
        raise RuntimeError(f"stop grabbing (post-warmup) fail! ret[0x{ret:x}]")

    exposure_value = MVCC_FLOATVALUE()
    gain_value = MVCC_FLOATVALUE()
    cam.MV_CC_GetFloatValue("ExposureTime", exposure_value)
    cam.MV_CC_GetFloatValue("Gain", gain_value)
    logger.info(
        "Post-warmup ExposureTime=%.1f Gain=%.1f",
        exposure_value.fCurValue,
        gain_value.fCurValue,
    )

    # Now switch to software trigger: the camera only produces a frame
    # when we tell it to (MV_CC_SetCommandValue("TriggerSoftware") below)
    # -- no free-run, no continuous GVSP traffic between triggers.
    ret = cam.MV_CC_SetEnumValue("TriggerMode", MV_TRIGGER_MODE_ON)
    if ret != 0:
        raise RuntimeError(f"set trigger mode on fail! ret[0x{ret:x}]")

    ret = cam.MV_CC_SetEnumValueByString("TriggerSource", "Software")
    if ret != 0:
        raise RuntimeError(f"set trigger source fail! ret[0x{ret:x}]")

    ret = cam.MV_CC_StartGrabbing()
    if ret != 0:
        raise RuntimeError(f"start grabbing (triggered) fail! ret[0x{ret:x}]")

    logger.info(
        "Camera at %s now idle -- waiting for PLC triggers (software trigger mode)",
        settings.CAMERA_IP,
    )


def _connect_and_wait_for_triggers(
    on_trigger_frame: Callable[[np.ndarray], None],
    snap_event: threading.Event,
    stop_event: threading.Event,
) -> None:
    cam = MvCamera()
    try:
        dev_info = MV_CC_DEVICE_INFO()
        gige_info = MV_GIGE_DEVICE_INFO()
        gige_info.nCurrentIp = _ip_to_int(settings.CAMERA_IP)
        gige_info.nNetExport = _ip_to_int(settings.CAMERA_NET_IP)
        dev_info.nTLayerType = MV_GIGE_DEVICE
        dev_info.SpecialInfo.stGigEInfo = gige_info

        ret = cam.MV_CC_CreateHandle(dev_info)
        if ret != 0:
            raise RuntimeError(f"create handle fail! ret[0x{ret:x}]")

        ret = cam.MV_CC_OpenDevice(MV_ACCESS_Exclusive, 0)
        if ret != 0:
            raise RuntimeError(f"open device fail! ret[0x{ret:x}]")

        n_packet_size = cam.MV_CC_GetOptimalPacketSize()
        if int(n_packet_size) > 0:
            ret = cam.MV_CC_SetIntValue("GevSCPSPacketSize", n_packet_size)
            if ret != 0:
                logger.warning("Set packet size failed, ret[0x%x]", ret)
        else:
            logger.warning("Get optimal packet size failed, ret[0x%x]", n_packet_size)

        ret = cam.MV_CC_SetBayerCvtQuality(1)
        if ret != 0:
            logger.warning("Set Bayer convert quality failed, ret[0x%x]", ret)

        _free_run_and_enter_trigger_mode(
            cam,
            stop_event,
            warmup_seconds=settings.CAMERA_BRIGHTNESS_WARMUP_SECONDS,
            reason="initial connect",
        )

        consecutive_errors = 0
        consecutive_bad_brightness = 0
        frame = MV_FRAME_OUT()
        while not stop_event.is_set():
            if not snap_event.wait(timeout=0.05):
                continue

            ret = cam.MV_CC_SetCommandValue("TriggerSoftware")
            if ret != 0:
                logger.warning("Software trigger command failed, ret[0x%x]", ret)
                snap_event.clear()
                continue

            memset(byref(frame), 0, sizeof(frame))
            ret = cam.MV_CC_GetImageBuffer(frame, settings.CAMERA_GET_FRAME_TIMEOUT_MS)
            if ret != 0 or frame.pBufAddr is None:
                consecutive_errors += 1
                logger.warning("Triggered grab failed, ret[0x%x]", ret)
                snap_event.clear()
                if consecutive_errors >= settings.CAMERA_MAX_CONSECUTIVE_ERRORS:
                    raise RuntimeError(f"too many consecutive grab failures, last ret[0x{ret:x}]")
                continue
            consecutive_errors = 0

            try:
                bgr = _frame_to_bgr(cam, frame)
            finally:
                cam.MV_CC_FreeImageBuffer(frame)
                snap_event.clear()

            if bgr is None:
                continue

            on_trigger_frame(bgr)

            # A lighting glitch (e.g. power dip, or the light coming back ON
            # after one) mid-run can leave the camera locked onto a stale
            # exposure/gain that's now way off for the current light level
            # -- too dark right after losing light, or blown-out/overexposed
            # once it comes back and the high gain/exposure from the outage
            # is still in effect. Nothing re-adjusts this on its own once
            # idle in trigger mode, so catch both directions here instead of
            # needing someone to notice and restart the service.
            brightness = _mean_brightness(bgr)
            too_dark = brightness < settings.CAMERA_DARK_FRAME_THRESHOLD
            too_bright = brightness > settings.CAMERA_BRIGHT_FRAME_THRESHOLD
            if too_dark or too_bright:
                consecutive_bad_brightness += 1
                if consecutive_bad_brightness >= settings.CAMERA_DARK_FRAME_MAX_CONSECUTIVE:
                    # ERROR, not warning -- this frame's OK/NG judgment was
                    # made on a too-dark/too-bright image (a real ampoule,
                    # possibly mis-judged), not just an internal hiccup.
                    # Needs an operator to notice: check the lighting, and
                    # review training snapshots saved around this timestamp.
                    logger.error(
                        "%d consecutive %s triggered frame(s) (mean=%.0f, range=%.0f-%.0f) -- last "
                        "result may be wrong, recalibrating brightness now",
                        consecutive_bad_brightness,
                        "dark" if too_dark else "bright",
                        brightness,
                        settings.CAMERA_DARK_FRAME_THRESHOLD,
                        settings.CAMERA_BRIGHT_FRAME_THRESHOLD,
                    )
                    _free_run_and_enter_trigger_mode(
                        cam,
                        stop_event,
                        warmup_seconds=settings.CAMERA_RECALIBRATE_WARMUP_SECONDS,
                        reason="brightness recalibration",
                    )
                    consecutive_bad_brightness = 0
            else:
                consecutive_bad_brightness = 0
    finally:
        _teardown_camera(cam)


def run(
    on_trigger_frame: Callable[[np.ndarray], None],
    snap_event: threading.Event,
    stop_event: threading.Event,
) -> None:
    """Blocks until stop_event is set, reconnecting on any SDK/camera failure."""
    _load_sdk()
    MvCamera.MV_CC_Initialize()
    try:
        while not stop_event.is_set():
            try:
                _connect_and_wait_for_triggers(on_trigger_frame, snap_event, stop_event)
            except Exception:
                logger.exception("Camera capture loop crashed, will retry")
            stop_event.wait(settings.CAMERA_RECONNECT_SECONDS)
    finally:
        MvCamera.MV_CC_Finalize()
