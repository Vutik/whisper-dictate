"""Global hotkey read straight from the kernel's input devices.

Wayland has no equivalent of XGrabKey: a client cannot grab a key. The
portable answer is to read /dev/input directly, which works identically on
X11, Wayland and a bare console.

Two consequences worth knowing. Reading input devices needs membership in the
`input` group. And unlike an X11 grab this does not *consume* the key — the
focused application still receives it — so pick a combination applications
ignore.

Only the configured combination is inspected; nothing else is read, stored or
logged.
"""
from __future__ import annotations

import os
import selectors
import threading

from ..interfaces import HotkeyBinder
from ..log import log
from ..registry import register

MODIFIER_KEYS = {
    "ctrl": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "control": ("KEY_LEFTCTRL", "KEY_RIGHTCTRL"),
    "shift": ("KEY_LEFTSHIFT", "KEY_RIGHTSHIFT"),
    "alt": ("KEY_LEFTALT", "KEY_RIGHTALT"),
    "super": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
    "win": ("KEY_LEFTMETA", "KEY_RIGHTMETA"),
}


@register("hotkey", "evdev", priority=50)
class EvdevHotkey(HotkeyBinder):
    def __init__(self, cfg, spec, callback):
        super().__init__(cfg, spec, callback)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def is_available(cls, cfg: dict) -> bool:
        try:
            import evdev
        except ImportError:
            return False
        try:
            return any(os.access(p, os.R_OK) for p in evdev.list_devices())
        except Exception:
            return False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkey-evdev")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    # ----------------------------------------------------------------------

    @staticmethod
    def _keyboards(evdev):
        from evdev import ecodes
        found = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except Exception:
                continue
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps and ecodes.KEY_A in caps[ecodes.EV_KEY]:
                found.append(dev)
            else:
                dev.close()
        return found

    def _parse(self, ecodes):
        parts = [p.strip().lower() for p in self.spec.split("+") if p.strip()]
        if not parts:
            self.error = "empty hotkey spec"
            return None, None
        keyname, modnames = parts[-1], parts[:-1]

        code = getattr(ecodes, f"KEY_{keyname.upper()}", None)
        if code is None and keyname == "space":
            code = ecodes.KEY_SPACE
        if code is None:
            self.error = f"unknown key {keyname!r}"
            return None, None

        wanted = []
        for name in modnames:
            names = MODIFIER_KEYS.get(name)
            if names is None:
                self.error = f"unknown modifier {name!r}"
                return None, None
            wanted.append({getattr(ecodes, n) for n in names})
        return code, wanted

    def _run(self):
        try:
            import evdev
            from evdev import ecodes
        except ImportError as exc:
            self.error = f"python-evdev missing: {exc}"
            log(f"hotkey: {self.error}")
            return

        target, wanted_mods = self._parse(ecodes)
        if target is None:
            log(f"hotkey: {self.error}")
            return

        devices = self._keyboards(evdev)
        if not devices:
            self.error = ("no readable keyboard in /dev/input — "
                          "add your user to the 'input' group and re-login")
            log(f"hotkey: {self.error}")
            return

        log(f"hotkey: watching {len(devices)} keyboard(s) for {self.spec}")
        held: set[int] = set()
        sel = selectors.DefaultSelector()
        for dev in devices:
            sel.register(dev, selectors.EVENT_READ)

        try:
            while not self._stop.is_set():
                for key, _ in sel.select(timeout=0.3):
                    for event in key.fileobj.read():
                        if event.type != ecodes.EV_KEY:
                            continue
                        # value: 0 = up, 1 = down, 2 = autorepeat
                        if event.value == 1:
                            held.add(event.code)
                        elif event.value == 0:
                            held.discard(event.code)
                        if event.code == target and event.value == 1:
                            if all(group & held for group in wanted_mods):
                                try:
                                    self.callback()
                                except Exception as exc:
                                    log(f"hotkey callback failed: {exc}")
        finally:
            sel.close()
            for dev in devices:
                try:
                    dev.close()
                except Exception:
                    pass
            log(f"hotkey: stopped watching for {self.spec}")
