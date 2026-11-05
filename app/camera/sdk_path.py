"""
Makes the vendored Hikrobot MVS SDK importable without requiring the full
MVS_STD installer (with its GUI/services/drivers) on this machine.

Vendored under vendor/ (at the project root, three levels up from this file),
kept separate per platform since the Windows and Linux MvImport wrappers here
come from different MVS SDK releases and must each match their own native
library's version:
  - mvimport/               Windows ctypes wrapper, matches mvs_runtime/Win64_x64/
  - mvimport_linux/          Linux ctypes wrapper, matches mvs_runtime/linux64/
  - mvs_runtime/Win64_x64/   MvCameraControl.dll + dependents (Windows)
  - mvs_runtime/linux64/64/  libMvCameraControl.so + dependents (Linux SDK's
                              lib/64/ directory, copied in as-is)

On Linux, MvCameraControl_class.py loads the .so via
  os.getenv("MVCAM_COMMON_RUNENV") + "/64/libMvCameraControl.so"
so vendor/mvs_runtime/linux64/ here must contain a "64/" subfolder with
libMvCameraControl.so in it, and MVCAM_COMMON_RUNENV must point at
vendor/mvs_runtime/linux64 *before* MvCameraControl_class is imported -- if
that env var is unset, the Linux wrapper does `None + "/64/..."` and raises
TypeError at import time.

libMvCameraControl.so itself links against several sibling .so files in that
same 64/ folder (libMVGigEVisionSDK.so, libGCBase_gcc485_v3_0.so, etc, as
confirmed against the real installer's set_env_path.sh) -- the dynamic linker
needs LD_LIBRARY_PATH to include that folder to resolve them. Critically,
glibc computes its library search path once at process startup from the
environment the process was *launched* with; mutating os.environ from inside
an already-running Python process does not affect it. So if LD_LIBRARY_PATH
isn't already correct, reexec_for_linux_runtime() restarts the process (once)
with it set, before anything imports the SDK.
"""

import os
import sys
import platform
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_VENDOR_DIR = _PROJECT_ROOT / "vendor"
_MVIMPORT_DIR_WINDOWS = _VENDOR_DIR / "mvimport"
_MVIMPORT_DIR_LINUX = _VENDOR_DIR / "mvimport_linux"
_RUNTIME_DIR_WINDOWS = _VENDOR_DIR / "mvs_runtime" / "Win64_x64"
_RUNTIME_DIR_LINUX = _VENDOR_DIR / "mvs_runtime" / "linux64"
_RUNTIME_LIB_DIR_LINUX = _RUNTIME_DIR_LINUX / "64"

_prepared = False


def reexec_for_linux_runtime() -> None:
    """
    No-op on Windows. On Linux, re-execs this same process (once) with
    LD_LIBRARY_PATH set to include the vendored SDK's lib dir, if it isn't
    already -- must be called before anything imports MvCameraControl_class
    (and, ideally, before any other setup work, since a re-exec restarts the
    process from scratch). Call this as the very first line of main().
    """
    if platform.system() == "Windows":
        return

    needed = str(_RUNTIME_LIB_DIR_LINUX)
    current = os.environ.get("LD_LIBRARY_PATH", "")
    if needed in current.split(os.pathsep):
        return

    os.environ["LD_LIBRARY_PATH"] = needed + (os.pathsep + current if current else "")
    os.execv(sys.executable, [sys.executable] + sys.argv)


def ensure_sdk_importable() -> None:
    """Idempotent: safe to call multiple times."""
    global _prepared
    if _prepared:
        return

    is_windows = platform.system() == "Windows"
    mvimport_dir = _MVIMPORT_DIR_WINDOWS if is_windows else _MVIMPORT_DIR_LINUX

    if not mvimport_dir.is_dir():
        raise RuntimeError(
            f"Vendored MvImport wrapper not found at {mvimport_dir}. "
            "Copy the matching platform's Samples/.../Python/MvImport/*.py there."
        )

    if str(mvimport_dir) not in sys.path:
        sys.path.insert(0, str(mvimport_dir))

    if is_windows:
        if not _RUNTIME_DIR_WINDOWS.is_dir():
            raise RuntimeError(
                f"Vendored MVS runtime DLLs not found at {_RUNTIME_DIR_WINDOWS}. "
                "Copy them from 'C:/Program Files (x86)/Common Files/MVS/Runtime/Win64_x64'."
            )
        # os.add_dll_directory (3.8+) makes WinDLL("MvCameraControl.dll") resolvable
        # without needing the SDK installer to have modified the system PATH.
        os.add_dll_directory(str(_RUNTIME_DIR_WINDOWS))
    else:
        if not (_RUNTIME_LIB_DIR_LINUX / "libMvCameraControl.so").is_file():
            raise RuntimeError(
                f"Vendored MVS Linux runtime not found at {_RUNTIME_LIB_DIR_LINUX}/libMvCameraControl.so. "
                "Copy the MVS Linux SDK's lib/64/ directory there as-is (keeping the 64/ subfolder)."
            )
        if str(_RUNTIME_LIB_DIR_LINUX) not in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
            raise RuntimeError(
                "LD_LIBRARY_PATH is missing the vendored SDK's lib dir -- call "
                "sdk_path.reexec_for_linux_runtime() before anything imports the SDK."
            )
        os.environ.setdefault("MVCAM_COMMON_RUNENV", str(_RUNTIME_DIR_LINUX))

    _prepared = True
