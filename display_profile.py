"""Remove the emulator's Pixel 6 display decoration from the physical 540p profile.

Called after Android finishes booting. This only disables the two installed Pixel
6 overlays; it does not create overlays, change display geometry, or start a
background service. The emulator may enable its profile overlays during boot, so
the adjustment is attempted once per kernel boot ID in this gateway process.
"""

import logging
import os
import re
import subprocess
import threading


class PhysicalDisplayProfileError(RuntimeError):
    """An explicitly requested physical 540p profile is missing or inconsistent."""


_log = logging.getLogger(__name__)
_lock = threading.Lock()
_attempted_boot_id = None
_last_result = None
_PIXEL6_OVERLAYS = (
    "com.android.internal.emulation.pixel_6",
    "com.android.systemui.emulation.pixel_6",
)
_CUTOUT_RESOURCES = (
    "android:string/config_mainBuiltInDisplayCutout",
    "android:string/config_mainBuiltInDisplayCutoutRectApproximation",
)


def _run(adb, *args):
    result = adb(*args)
    if result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=result.stderr
        )
    return result.stdout or ""


def _failure(exc):
    detail = getattr(exc, "stderr", None) or getattr(exc, "output", None)
    return (str(exc) + (": " + str(detail).strip() if detail else ""))[:1000]


def _lookup(adb, resource):
    value = _run(adb, "shell", "cmd", "overlay", "lookup", "--user", "0",
                 "android", resource).rstrip("\r\n")
    # lookup prints both the reference and its resolved value for aliases.
    if " -> " in value:
        value = value.rsplit(" -> ", 1)[1]
    return value.strip()


def _verify_geometry(size, density):
    physical_size = re.search(r"^Physical size:\s*(\d+)x(\d+)\s*$", size, re.M)
    physical_density = re.search(r"^Physical density:\s*(\d+)\s*$", density, re.M)
    if (not physical_size or physical_size.groups() != ("540", "1200")
            or not physical_density or physical_density.group(1) != "210"):
        raise PhysicalDisplayProfileError(
            "DIRECT_PHYSICAL_DISPLAY=540x1200 requires Physical size 540x1200 "
            "and Physical density 210; received size=%r, density=%r. "
            "Restore the AVD physical display profile and cold boot before connecting."
            % (size.strip(), density.strip())
        )
    override_size = re.search(r"^Override size:\s*(\d+)x(\d+)\s*$", size, re.M)
    override_density = re.search(r"^Override density:\s*(\d+)\s*$", density, re.M)
    if ((override_size and override_size.groups() != ("540", "1200"))
            or (override_density and override_density.group(1) != "210")):
        raise PhysicalDisplayProfileError(
            "The physical 540x1200/210 display has a conflicting wm override: "
            "size=%r, density=%r. Reset the conflicting override before connecting."
            % (size.strip(), density.strip())
        )


def apply_540_profile(adb, *, environ=None):
    """Apply the opted-in display adjustment, using an adb(*args) callable.

    adb must return subprocess.CompletedProcess with text output (raising on
    failure is also supported). An invalid physical profile raises
    PhysicalDisplayProfileError before any mutation. Optional decoration and
    diagnostic failures produce a warning result so remote access remains usable.
    Successful and unsuccessful overlay attempts are memoized per boot ID to
    avoid repeating UI changes on each connection. Restarting the gateway allows
    a failed attempt to be retried after its cause has been fixed.
    """
    global _attempted_boot_id, _last_result
    env = os.environ if environ is None else environ
    if env.get("DIRECT_PHYSICAL_DISPLAY") != "540x1200":
        return {"status": "skipped", "reason": "physical 540p profile not requested"}

    with _lock:
        try:
            if _run(adb, "shell", "getprop", "sys.boot_completed").strip() != "1":
                return {"status": "deferred", "reason": "Android boot is incomplete"}
            boot_id = _run(adb, "shell", "cat", "/proc/sys/kernel/random/boot_id").strip()
            if not boot_id:
                raise ValueError("Android kernel boot ID is unavailable")
        except Exception as exc:
            reason = "Cannot check Android boot for the 540p display profile: " + _failure(exc)
            _log.warning(reason)
            return {"status": "deferred", "reason": reason}

        if boot_id == _attempted_boot_id:
            return dict(_last_result, cached=True)

        try:
            size = _run(adb, "shell", "wm", "size")
            density = _run(adb, "shell", "wm", "density")
        except Exception as exc:
            reason = "Cannot verify the physical 540p display; overlays unchanged: " + _failure(exc)
            _log.warning(reason)
            return {"status": "deferred", "boot_id": boot_id, "reason": reason}
        _verify_geometry(size, density)

        errors = []
        for overlay in _PIXEL6_OVERLAYS:
            try:
                _run(adb, "shell", "su", "0", "cmd", "overlay", "disable",
                     "--user", "0", overlay)
            except Exception as exc:
                errors.append("Cannot disable %s: %s" % (overlay, _failure(exc)))

        cutouts = {}
        for resource in _CUTOUT_RESOURCES:
            try:
                value = _lookup(adb, resource)
                cutouts[resource] = value
                if value:
                    errors.append("The 540p display still has a cutout in %s: %s"
                                  % (resource, value[:250]))
            except Exception as exc:
                errors.append("Cannot verify empty cutout %s: %s" % (resource, _failure(exc)))

        heights = {}
        for orientation in ("portrait", "landscape"):
            resource = "android:dimen/status_bar_height_" + orientation
            try:
                heights[orientation] = _lookup(adb, resource)
            except Exception as exc:
                # Height lookup is diagnostic; it does not determine success.
                _log.warning("Cannot read 540p %s status bar height: %s",
                             orientation, _failure(exc))

        result = {"status": "warning" if errors else "applied", "boot_id": boot_id,
                  "cutouts": cutouts, "status_bar_heights": heights}
        if errors:
            result["errors"] = errors
            _log.warning("540p display decoration adjustment incomplete: %s", "; ".join(errors))
        else:
            _log.info("540p display profile applied; status bar heights: %s", heights)
        _attempted_boot_id, _last_result = boot_id, result
        return dict(result)
