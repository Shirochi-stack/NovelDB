"""Google sign-in through pywebview in a child process (moved from gui.py).

This module must not import Qt or tkinter: it is the target of a multiprocessing child, which
imports only what it needs.
"""

import os
import tempfile
import time


def _writable_data_dir(kind):
    try:
        from noveldb.core import paths
        return str(paths.logs_dir() if kind == 'logs' else paths.cache_dir())
    except Exception:
        return None


def _run_webview_login(output_path):
    import traceback

    # Write debug log to a writable logs directory (reuse the same
    # fallback chain that the module-level logging setup uses).
    _log_dir = _writable_data_dir('logs') or tempfile.gettempdir()
    debug_path = os.path.join(_log_dir, "webview_debug.log")
    def _dbg(msg):
        try:
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    _dbg("=== webview login started ===")

    # pythonnet 3.x requires explicit runtime init before clr.AddReference works
    try:
        from pythonnet import load
        load()
        _dbg("pythonnet loaded OK")
    except Exception as e:
        _dbg(f"pythonnet load (non-fatal): {e}")

    try:
        import webview
        _dbg(f"webview imported OK (version: {getattr(webview, '__version__', '?')})")
    except Exception as e:
        _dbg(f"webview import FAILED:\n{traceback.format_exc()}")
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
        return

    def extract_loginkey_js(cookie_str):
        """Extract LOGINKEY from document.cookie string."""
        if not cookie_str:
            return None
        try:
            for p in cookie_str.split(";"):
                s = p.strip()
                if s.startswith("LOGINKEY="):
                    v = s.split("=", 1)[1].strip()
                    if v:
                        return v
        except Exception:
            pass
        return None

    def get_loginkey():
        """Try multiple methods to read LOGINKEY from the webview."""
        # Method 1: window.get_cookies() — reads ALL cookies including HttpOnly
        try:
            cookies = window.get_cookies()
            if cookies:
                for cookie in cookies:
                    name = getattr(cookie, 'name', '')
                    if name == 'LOGINKEY':
                        v = getattr(cookie, 'value', '')
                        if v:
                            return v
        except Exception:
            pass
        # Method 2: document.cookie — only non-HttpOnly cookies
        try:
            js_cookies = window.evaluate_js("document.cookie")
            return extract_loginkey_js(js_cookies)
        except Exception:
            pass
        return None

    key_holder = {"key": None, "initial_key": None}

    def poll_for_key(_window=None):
        _dbg("poll_for_key started, waiting for page load...")
        time.sleep(3)

        initial_key = get_loginkey()
        key_holder["initial_key"] = initial_key
        _dbg(f"initial key: {repr(initial_key[:20] if initial_key else None)}")

        was_on_google = False

        for i in range(900):
            try:
                cur_url = ""
                try:
                    cur_url = window.get_current_url() or ""
                except Exception:
                    pass

                # Track when we navigate to Google OAuth
                if 'accounts.google.com' in cur_url:
                    if not was_on_google:
                        _dbg(f"poll #{i}: navigated to Google OAuth")
                        was_on_google = True

                # After Google OAuth, detect redirect back to \u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com
                # This means login succeeded — the SAME cookie is now authenticated
                if was_on_google and '\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com' in cur_url:
                    _dbg(f"poll #{i}: redirected back to npia: {cur_url[:80]}")
                    # Wait for the redirect chain to finish
                    time.sleep(3)

                    # Read the key — it's the same value but now it's authenticated
                    key = get_loginkey()
                    if not key:
                        try:
                            js_cookies = window.evaluate_js("document.cookie")
                            key = extract_loginkey_js(js_cookies)
                        except Exception:
                            pass

                    if key:
                        _dbg(f"Login successful! Key: {key[:20]}...")
                        key_holder["key"] = key
                        try:
                            with open(output_path, "w", encoding="utf-8") as f:
                                f.write(key)
                        except Exception:
                            pass
                        try:
                            window.destroy()
                        except Exception:
                            pass
                        return
                    else:
                        _dbg("Redirect detected but no cookie found, continuing...")
                        was_on_google = False  # Reset to detect next redirect

                if i < 5 or (i % 30 == 0):
                    _dbg(f"poll #{i}: url={cur_url[:60] if cur_url else None}, google={was_on_google}")

            except Exception as e:
                if i < 5:
                    _dbg(f"poll #{i} error: {e}")
            time.sleep(1)
        _dbg("poll_for_key timed out after 900s")

    # Use screen ratio for window size
    try:
        import ctypes
        user32 = ctypes.windll.user32
        _sw = user32.GetSystemMetrics(0)
        _sh = user32.GetSystemMetrics(1)
        _dbg(f"screen size: {_sw}x{_sh} (ctypes)")
    except Exception:
        _sw, _sh = 1200, 900
        _dbg(f"screen size: fallback {_sw}x{_sh}")

    try:
        window = webview.create_window(
            "Npia Google Login",
            "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/",
            width=int(_sw * 0.6),
            height=int(_sh * 0.7),
        )
        _dbg("window created, calling webview.start()")
        webview.start(poll_for_key, window, debug=False)
        _dbg("webview.start() returned")
    except Exception as e:
        _dbg(f"webview.start FAILED:\n{traceback.format_exc()}")

    # Window was closed — try one final cookie read
    if key_holder["key"] is None:
        _dbg("Attempting final cookie read after window close...")
        try:
            final_key = get_loginkey()
            _dbg(f"final get_loginkey: {repr(final_key[:20] if final_key else None)}")
            if final_key and final_key != key_holder.get("initial_key"):
                key_holder["key"] = final_key
                try:
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(final_key)
                except Exception:
                    pass
                _dbg(f"LOGINKEY captured post-close: {final_key[:20]}...")
        except Exception as e:
            _dbg(f"final cookie read error: {e}")

    try:
        if key_holder["key"] is None:
            _dbg("No key captured, writing empty output")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass
    _dbg("=== webview login ended ===")
