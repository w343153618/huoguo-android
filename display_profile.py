"""Remove Pixel 6 display decoration from opted-in physical display profiles.

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
    """An explicitly requested physical display profile is inconsistent."""


_log = logging.getLogger(__name__)
_lock = threading.Lock()
_attempted_boot_id = None
_attempted_profile = None
_last_result = None
_PROFILES = {
    "540x1200": (("540", "1200"), "210", "540p"),
    "720x1280": (("720", "1280"), "320", "720p"),
    "1080x1920": (("1080", "1920"), "480", "1080p"),
}
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


def _verify_geometry(size, density, requested="540x1200"):
    expected_size, expected_density, _ = _PROFILES[requested]
    physical_size = re.search(r"^Physical size:\s*(\d+)x(\d+)\s*$", size, re.M)
    physical_density = re.search(r"^Physical density:\s*(\d+)\s*$", density, re.M)
    if (not physical_size or physical_size.groups() != expected_size
            or not physical_density or physical_density.group(1) != expected_density):
        raise PhysicalDisplayProfileError(
            "DIRECT_PHYSICAL_DISPLAY=%s requires Physical size %s "
            "and Physical density %s; received size=%r, density=%r. "
            "Restore the AVD physical display profile and cold boot before connecting."
            % (requested, requested, expected_density, size.strip(), density.strip())
        )
    override_size = re.search(r"^Override size:\s*(\d+)x(\d+)\s*$", size, re.M)
    override_density = re.search(r"^Override density:\s*(\d+)\s*$", density, re.M)
    if ((override_size and override_size.groups() != expected_size)
            or (override_density and override_density.group(1) != expected_density)):
        raise PhysicalDisplayProfileError(
            "The physical %s/%s display has a conflicting wm override: "
            "size=%r, density=%r. Reset the conflicting override before connecting."
            % (requested, expected_density, size.strip(), density.strip())
        )


def apply_540_profile(adb, *, environ=None):
    """Apply an opted-in display adjustment through the legacy public name.

    adb must return subprocess.CompletedProcess with text output (raising on
    failure is also supported). An invalid physical profile raises
    PhysicalDisplayProfileError before any mutation. Optional decoration and
    diagnostic failures produce a warning result so remote access remains usable.
    Successful and unsuccessful overlay attempts are memoized per boot/profile to
    avoid repeating UI changes on each connection. Restarting the gateway allows
    a failed attempt to be retried after its cause has been fixed.
    """
    global _attempted_boot_id, _attempted_profile, _last_result
    env = os.environ if environ is None else environ
    requested = env.get("DIRECT_PHYSICAL_DISPLAY")
    if requested not in _PROFILES:
        return {"status": "skipped", "reason": "supported physical profile not requested"}
    _, expected_density, label = _PROFILES[requested]

    with _lock:
        try:
            if _run(adb, "shell", "getprop", "sys.boot_completed").strip() != "1":
                return {"status": "deferred", "reason": "Android boot is incomplete"}
            boot_id = _run(adb, "shell", "cat", "/proc/sys/kernel/random/boot_id").strip()
            if not boot_id:
                raise ValueError("Android kernel boot ID is unavailable")
        except Exception as exc:
            reason = "Cannot check Android boot for the %s display profile: " % label + _failure(exc)
            _log.warning(reason)
            return {"status": "deferred", "reason": reason}

        if boot_id == _attempted_boot_id and requested == _attempted_profile:
            return dict(_last_result, cached=True)

        try:
            size = _run(adb, "shell", "wm", "size")
            density = _run(adb, "shell", "wm", "density")
        except Exception as exc:
            reason = "Cannot verify the physical %s display; overlays unchanged: " % label + _failure(exc)
            _log.warning(reason)
            return {"status": "deferred", "boot_id": boot_id, "reason": reason}
        _verify_geometry(size, density, requested)

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
                    errors.append("The %s display still has a cutout in %s: %s"
                                  % (label, resource, value[:250]))
            except Exception as exc:
                errors.append("Cannot verify empty cutout %s: %s" % (resource, _failure(exc)))

        heights = {}
        for orientation in ("portrait", "landscape"):
            resource = "android:dimen/status_bar_height_" + orientation
            try:
                heights[orientation] = _lookup(adb, resource)
            except Exception as exc:
                # Height lookup is diagnostic; it does not determine success.
                _log.warning("Cannot read %s %s status bar height: %s",
                             label, orientation, _failure(exc))

        result = {"status": "warning" if errors else "applied", "boot_id": boot_id,
                  "profile": requested, "physical_density": int(expected_density),
                  "cutouts": cutouts, "status_bar_heights": heights}
        if errors:
            result["errors"] = errors
            _log.warning("%s display decoration adjustment incomplete: %s", label, "; ".join(errors))
        else:
            _log.info("%s display profile applied; status bar heights: %s", label, heights)
        _attempted_boot_id, _attempted_profile, _last_result = boot_id, requested, result
        return dict(result)
