# dpi_setup.py — Cross-platform DPI-scaling bootstrap for PySide6
#
# Call  dpi_setup.configure()  ONCE, **before** importing any PySide6 modules.
# Safe to call multiple times (idempotent). Works on Windows, macOS, and Linux.
#
# What this does:
#   1. Sets QT_ENABLE_HIGHDPI_SCALING=1  → enables Qt6 native high-DPI rendering
#      (text is rasterised at the correct resolution → sharp glyphs)
#   2. Detects the OS-level DPI scale (e.g. 1.5× on a 150 % Windows display)
#   3. Sets QT_SCALE_FACTOR = desired / system_scale so the user's configured
#      factor represents the *total* scale, not an additional multiplier.
#
# The scale factor is read from config.json ("gui_scale_factor" key).
# If the file is missing or unreadable a resolution-based default is used.

import json
import os
import sys

# Force UTF-8 console output to prevent UnicodeEncodeError on Windows cp1252
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

_configured = False

# Default scale factor when config.json has no value
DEFAULT_SCALE_FACTOR = 1.0

# Populated by configure(): the scale factor that was ultimately applied.
# Consumers (e.g. tkinter apps via apply_to_tk) read these to line up with
# what Qt would have used.
_TOTAL_SCALE = 1.0    # user's desired total UI scale
_QT_SCALE = 1.0       # what we wrote into QT_SCALE_FACTOR
_SYSTEM_SCALE = 1.0   # OS-level DPI scale (1.0, 1.25, 1.5, ...)


def _ensure_dpi_aware():
    """Make the process DPI-aware on Windows (idempotent, no-op on other platforms)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass


def _mac_get_backing_scale_factor():
    """Return the macOS backing scale factor using CoreGraphics (ctypes).

    Uses CGMainDisplayID + backingScaleFactor via Cocoa/ObjC runtime.
    Falls back to pixel-density heuristic, then system_profiler as last resort.
    This avoids shelling out to system_profiler which can hang or crash on
    Hackintosh / non-Apple hardware (AMD CPUs, non-standard GPU kexts).

    Returns 2.0 for Retina, 1.0 for standard displays.
    """
    # ── Method 1: Cocoa NSScreen.mainScreen.backingScaleFactor via ObjC runtime ──
    try:
        import ctypes
        import ctypes.util
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library('objc'))

        # objc_getClass / sel_registerName / objc_msgSend
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

        NSScreen = objc.objc_getClass(b'NSScreen')
        if NSScreen:
            sel_main = objc.sel_registerName(b'mainScreen')
            main_screen = objc.objc_msgSend(NSScreen, sel_main)
            if main_screen:
                sel_scale = objc.sel_registerName(b'backingScaleFactor')
                # backingScaleFactor returns CGFloat (double on 64-bit)
                objc.objc_msgSend.restype = ctypes.c_double
                scale = objc.objc_msgSend(main_screen, sel_scale)
                # Reset restype for safety
                objc.objc_msgSend.restype = ctypes.c_void_p
                if scale >= 1.0:
                    return float(scale)
    except Exception:
        pass

    # ── Method 2: CoreGraphics pixel density heuristic ──────────────────────
    try:
        import ctypes
        import ctypes.util
        cg_path = ctypes.util.find_library('CoreGraphics')
        if cg_path:
            cg = ctypes.cdll.LoadLibrary(cg_path)
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            display_id = cg.CGMainDisplayID()
            cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
            cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            pixel_w = cg.CGDisplayPixelsWide(display_id)
            # CGDisplayScreenSize returns CGSize (two doubles: width, height in mm)
            class CGSize(ctypes.Structure):
                _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]
            cg.CGDisplayScreenSize.restype = CGSize
            cg.CGDisplayScreenSize.argtypes = [ctypes.c_uint32]
            phys = cg.CGDisplayScreenSize(display_id)
            if phys.width > 0:
                dpi = (pixel_w / phys.width) * 25.4  # mm → inches
                if dpi > 170:  # Retina threshold (~220 DPI typical)
                    return 2.0
    except Exception:
        pass

    # ── Method 3: system_profiler as last resort (shorter timeout) ──────────
    try:
        import subprocess, re as _re
        out = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"],
            timeout=3, stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
        if _re.search(r"Resolution:.*Retina", out, _re.IGNORECASE):
            return 2.0
    except Exception:
        pass

    return 1.0


def _mac_get_screen_resolution():
    """Return (width, height) of the main macOS display using CoreGraphics.

    Uses CGMainDisplayID + CGDisplayPixelsWide/High which are reliable on all
    macOS-compatible hardware including Hackintosh / AMD CPU systems.
    Falls back to system_profiler as last resort.
    """
    # ── Method 1: CoreGraphics (fast, no subprocess, Hackintosh-safe) ──────
    try:
        import ctypes
        import ctypes.util
        cg_path = ctypes.util.find_library('CoreGraphics')
        if cg_path:
            cg = ctypes.cdll.LoadLibrary(cg_path)
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            display_id = cg.CGMainDisplayID()
            cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
            cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
            cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            width = cg.CGDisplayPixelsWide(display_id)
            height = cg.CGDisplayPixelsHigh(display_id)
            if width > 0 and height > 0:
                return (int(width), int(height))
    except Exception:
        pass

    # ── Method 2: system_profiler as last resort (shorter timeout) ──────────
    try:
        import subprocess, re as _re
        out = subprocess.check_output(
            ["system_profiler", "SPDisplaysDataType"],
            timeout=3, stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
        m = _re.search(r"Resolution:\s+(\d+)\s*x\s*(\d+)", out)
        if m:
            return (int(m.group(1)), int(m.group(2)))
    except Exception:
        pass

    return (0, 0)


def _get_system_dpi_scale():
    """Return the OS-level DPI scale factor (e.g. 1.0, 1.25, 1.5, 2.0).

    Windows  → GetDpiForSystem / GetDeviceCaps
    macOS    → CoreGraphics backing scale (Hackintosh-safe, no system_profiler)
    Linux    → Xrdb / GDK_SCALE / xrandr DPI
    Fallback → 1.0
    """
    # ── Windows ────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        _ensure_dpi_aware()
        try:
            import ctypes
            # GetDpiForSystem (Windows 10 1607+)
            try:
                dpi = ctypes.windll.user32.GetDpiForSystem()
                if dpi > 0:
                    return dpi / 96.0
            except Exception:
                pass
            # Fallback: GetDeviceCaps(LOGPIXELSX)
            hdc = ctypes.windll.user32.GetDC(0)
            if hdc:
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                if dpi > 0:
                    return dpi / 96.0
        except Exception:
            pass

    # ── macOS ──────────────────────────────────────────────────────────────
    elif sys.platform == "darwin":
        return _mac_get_backing_scale_factor()

    # ── Linux / X11 / Wayland ──────────────────────────────────────────────
    else:
        # 1. GDK_SCALE (integer scale set by GNOME/GTK)
        try:
            gdk = os.environ.get("GDK_SCALE", "")
            if gdk:
                val = int(gdk)
                if val >= 1:
                    return float(val)
        except (ValueError, TypeError):
            pass

        # 2. Xft.dpi from xrdb (set by most desktop environments)
        try:
            import subprocess, re as _re
            out = subprocess.check_output(
                ["xrdb", "-query"],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
            m = _re.search(r"Xft\.dpi:\s*(\d+)", out)
            if m:
                dpi = int(m.group(1))
                if dpi > 0:
                    return dpi / 96.0
        except Exception:
            pass

    return 1.0


def _get_screen_resolution():
    """Return (width, height) of the primary monitor using stdlib only.

    Windows  → ctypes + GetSystemMetrics
    macOS    → CoreGraphics (Hackintosh-safe, no system_profiler)
    Linux    → subprocess + xrandr
    Returns (0, 0) on failure.
    """
    width, height = 0, 0

    # ── Windows ────────────────────────────────────────────────────────────
    if sys.platform == "win32":
        _ensure_dpi_aware()
        try:
            import ctypes
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
        except Exception:
            pass

    # ── macOS ──────────────────────────────────────────────────────────────
    elif sys.platform == "darwin":
        width, height = _mac_get_screen_resolution()
        # CGDisplayPixelsWide/High returns logical points on Retina;
        # multiply by backing scale factor to get physical pixel count
        # so the auto-scaler correctly recognises high-DPI displays.
        backing = _mac_get_backing_scale_factor()
        width = int(width * backing)
        height = int(height * backing)

    # ── Linux / X11 ────────────────────────────────────────────────────────
    else:
        try:
            import subprocess, re as _re
            out = subprocess.check_output(
                ["xrandr", "--current"],
                timeout=5, stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="replace")
            # Match the *connected primary* line first, fall back to any connected
            m = _re.search(r"connected primary\s+(\d+)x(\d+)", out)
            if not m:
                m = _re.search(r"connected\s+(\d+)x(\d+)", out)
            if m:
                width, height = int(m.group(1)), int(m.group(2))
        except Exception:
            pass

    return width, height


def _get_default_scale_for_resolution():
    """Return a sensible default scale factor based on the primary monitor's resolution.

    Values are chosen to be conservative on DPI-aware processes (tkinter
    on Windows, Cocoa Retina on macOS) where the OS has already scaled
    the UI. The multiplier is only meant to tweak the OS-applied size,
    not replace it — so all values stay near 1.0.

    Qt code paths (see configure()) divide this out by the system DPI
    scale, so the same values also work there.

    Works on Windows, macOS, and Linux (no PySide6 needed).
    Falls back to DEFAULT_SCALE_FACTOR if detection fails.
    """
    try:
        width, height = _get_screen_resolution()
        if width <= 0 or height <= 0:
            return DEFAULT_SCALE_FACTOR

        # Horizontal-resolution buckets. These are *total* target scales;
        # the OS contribution is handled separately in configure() / apply_to_tk().
        if width >= 3840:       # 4K (3840x2160) and up
            return 1.0
        elif width >= 2560:     # 1440p / QHD (2560x1440)
            return 1.0
        elif width >= 1920:     # 1080p (1920x1080)
            return 1.0
        elif width >= 1366:     # 768p / common laptop panels
            return 0.9
        else:                   # 720p or lower / very small panels
            return 0.8
    except Exception:
        return DEFAULT_SCALE_FACTOR


def _read_scale_factor():
    """Read gui_scale_factor from config.json (stdlib only, no PySide6)."""
    try:
        # In frozen (PyInstaller) builds, __file__ points to _MEIPASS temp dir.
        # config.json lives next to the executable, not inside the bundle.
        if getattr(sys, 'frozen', False) and hasattr(sys, 'executable'):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(base_dir, "config.json")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # If auto DPI scaling is enabled, use resolution-based default
            if cfg.get("auto_dpi_scale", True):
                return _get_default_scale_for_resolution()
            val = cfg.get("gui_scale_factor", None)
            if val is None:
                return _get_default_scale_for_resolution()
            factor = float(val)
            # Clamp to sane range
            if factor < 0.5:
                factor = 0.5
            elif factor > 3.0:
                factor = 3.0
            return factor
    except Exception:
        pass
    return _get_default_scale_for_resolution()


def configure():
    """Enable Qt6 native high-DPI rendering and apply the user's scale factor.

    Must be called *before* importing any PySide6/Qt modules.
    Idempotent — subsequent calls are harmless no-ops.

    For tkinter apps, this is still safe to call early: it only sets env
    vars (ignored by tkinter) and makes the Windows process DPI-aware.
    Use apply_to_tk(root) after creating the Tk root to apply the same
    scale factor to tkinter widgets.
    """
    global _configured, _TOTAL_SCALE, _QT_SCALE, _SYSTEM_SCALE
    if _configured:
        return
    _configured = True

    # Ensure the process is DPI-aware before we measure anything, so that
    # resolution and system-DPI reads are correct on Windows.
    _ensure_dpi_aware()

    # ── Enable Qt6 native high-DPI scaling (sharp text rendering) ─────────
    # Use PassThrough to prevent blurry text on fractional scales (e.g. 1.15x)
    os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    # Do NOT set QT_FONT_DPI — let Qt derive font DPI from the device pixel
    # ratio so glyphs are rasterised at the correct resolution (not 96 DPI
    # stretched up, which causes blurriness).
    os.environ.pop("QT_FONT_DPI", None)

    # ── Apply user-configured scale factor ──────────────────────────────────
    # The user's factor is the desired *total* scale.  Qt will auto-detect
    # the system DPI (e.g. 1.5× on a 150 % Windows display), so we divide
    # out the system scale to keep the total correct.
    factor = _read_scale_factor()
    system_scale = _get_system_dpi_scale()
    if sys.platform == "darwin":
        # Qt 6 handles Retina 2.0x scaling natively via
        # QT_ENABLE_HIGHDPI_SCALING; dividing by the backing factor
        # would double-downscale the UI, making it unreadably small.
        qt_scale = factor
    else:
        qt_scale = factor / system_scale if system_scale > 0.5 else factor
    qt_scale = max(0.25, min(4.0, qt_scale))
    os.environ["QT_SCALE_FACTOR"] = str(round(qt_scale, 4))

    _TOTAL_SCALE = float(factor)
    _QT_SCALE = float(qt_scale)
    _SYSTEM_SCALE = float(system_scale)

    try:
        print(
            f"[dpi_setup] scaling configured (target={factor}, "
            f"system={system_scale:.2f}, QT_SCALE_FACTOR={qt_scale:.4f})"
        )
    except Exception:
        pass


def get_scale_factor():
    """Return the user's desired total UI scale factor after configure().

    Falls back to a resolution-based default if configure() hasn't run yet.
    """
    if _configured:
        return _TOTAL_SCALE
    return _read_scale_factor()


def get_system_scale():
    """Return the detected OS-level DPI scale after configure()."""
    if _configured:
        return _SYSTEM_SCALE
    return _get_system_dpi_scale()


def apply_to_tk(root):
    """Apply the configured DPI scaling to a tkinter `Tk` / `Toplevel` root.

    Must be called AFTER the root window exists. Safe to call multiple times
    (the last call wins). Uses Tk's built-in 'tk scaling' so widget geometry,
    default fonts, and ttk themes scale consistently.

    On Windows it also ensures the process is DPI-aware (no-op elsewhere).

    On macOS tkinter is already Retina-aware via the system framework, so we
    only pass through the user's desired scale (Qt divides out the backing
    factor; tkinter does not need that).
    """
    # Make sure configure() has run at least once.
    if not _configured:
        configure()
    _ensure_dpi_aware()

    try:
        # tk's native 'scaling' is in points-per-pixel: 1.0 = 72 DPI, default
        # on Windows/Linux is ~1.33 (96 DPI / 72). To scale UI by `factor`,
        # multiply the current value rather than overriding absolutely,
        # otherwise we'd clobber the system DPI that Tk already detected.
        current = float(root.tk.call('tk', 'scaling'))
    except Exception:
        current = 1.333  # fall back to the 96 DPI default

    # On Windows the process is DPI-aware now, so Tk already reports the
    # correct system scaling in `current`. The user's factor is a multiplier
    # on top of that. On macOS tkinter ignores the backing factor in 'scaling'
    # so we just multiply too.
    target_factor = _TOTAL_SCALE if _configured else _read_scale_factor()
    try:
        new_scaling = current * float(target_factor)
        # Clamp to Tk's sane range
        new_scaling = max(0.5, min(6.0, new_scaling))
        root.tk.call('tk', 'scaling', new_scaling)
    except Exception:
        pass

    # Also scale the default named fonts so text in every widget grows/shrinks
    # proportionally. Without this, widget padding changes but font size doesn't.
    try:
        import tkinter.font as tkfont
        for fname in tkfont.names(root):
            try:
                f = tkfont.nametofont(fname)
                size = int(f.cget('size'))
                if size == 0:
                    continue
                # tk uses negative sizes for pixels, positive for points.
                # Scaling applies to both; round to keep values integer.
                scaled = int(round(size * float(target_factor)))
                if scaled == 0:
                    scaled = size
                f.configure(size=scaled)
            except Exception:
                continue
    except Exception:
        pass
