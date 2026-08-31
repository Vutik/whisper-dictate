"""Global hotkey via an X11 passive grab.

GNOME's own custom keybindings proved unreliable on this desktop (gsettings
accepts them, gsd-media-keys never runs the command), and a direct grab has
one less moving part.
"""
from __future__ import annotations

import os
import threading

from ..interfaces import HotkeyBinder
from ..log import log
from ..registry import register

MOD_MAP = {
    "ctrl": "ControlMask", "control": "ControlMask", "primary": "ControlMask",
    "shift": "ShiftMask", "alt": "Mod1Mask", "meta": "Mod1Mask",
    "super": "Mod4Mask", "win": "Mod4Mask", "mod4": "Mod4Mask",
}


@register("hotkey", "x11", priority=100)
class X11Hotkey(HotkeyBinder):
    def __init__(self, cfg, spec, callback):
        super().__init__(cfg, spec, callback)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        if not os.environ.get("DISPLAY"):
            return False
        try:
            import Xlib  # noqa: F401
        except ImportError:
            return False
        return True

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkey")
        self._thread.start()

    def stop(self) -> None:
        """Stop and wait: the X grab is only released once the thread exits,
        and a rebind to the same combination would otherwise hit BadAccess."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _run(self):
        try:
            from Xlib import X, XK, display
            from Xlib import error as Xerror
        except ImportError as exc:
            self.error = f"python-xlib missing: {exc}"
            log(f"hotkey: {self.error}")
            return

        parts = [p.strip().lower() for p in self.spec.split("+") if p.strip()]
        if not parts:
            self.error = "empty hotkey spec"
            return
        keyname, modnames = parts[-1], parts[:-1]

        mods = 0
        for name in modnames:
            attr = MOD_MAP.get(name)
            if attr is None:
                self.error = f"unknown modifier {name!r}"
                log(f"hotkey: {self.error}")
                return
            mods |= getattr(X, attr)

        try:
            d = display.Display()
        except Exception as exc:
            self.error = f"cannot open X display: {exc}"
            log(f"hotkey: {self.error}")
            return

        keysym = XK.string_to_keysym(keyname)
        if keysym == 0:
            keysym = XK.string_to_keysym(keyname.capitalize())
        keycode = d.keysym_to_keycode(keysym)
        if not keycode:
            self.error = f"unknown key {keyname!r}"
            log(f"hotkey: {self.error}")
            return

        root = d.screen().root
        # NumLock / CapsLock / ScrollLock must not defeat the grab.
        lock_combos = [0, X.LockMask, X.Mod2Mask, X.Mod5Mask,
                       X.LockMask | X.Mod2Mask, X.LockMask | X.Mod5Mask,
                       X.Mod2Mask | X.Mod5Mask,
                       X.LockMask | X.Mod2Mask | X.Mod5Mask]
        catch = Xerror.CatchError(Xerror.BadAccess)
        for extra in lock_combos:
            root.grab_key(keycode, mods | extra, True,
                          X.GrabModeAsync, X.GrabModeAsync, onerror=catch)
        d.sync()
        if catch.get_error():
            self.error = (f"{self.spec} is already grabbed by another client "
                          f"(desktop shortcut?) — pick a different hotkey")
            log(f"hotkey: {self.error}")
            return

        log(f"hotkey: grabbed {self.spec} (keycode {keycode})")
        root.change_attributes(event_mask=X.KeyPressMask)

        import select
        fd = d.fileno()
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                for _ in range(d.pending_events()):
                    ev = d.next_event()
                    if ev.type == X.KeyPress and ev.detail == keycode:
                        try:
                            self.callback()
                        except Exception as exc:
                            log(f"hotkey callback failed: {exc}")
        finally:
            try:
                for extra in lock_combos:
                    root.ungrab_key(keycode, mods | extra)
                d.sync()
            except Exception:
                pass
            try:
                d.close()
            except Exception:
                pass
            log(f"hotkey: released {self.spec}")
