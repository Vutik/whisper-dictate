"""Text injection: paste-vs-type routing and terminal detection.

No X server involved — subprocess is replaced by a recorder.
"""
import pytest

from whisper_dictate.backends import inject_x11
from whisper_dictate.backends.inject_x11 import X11Injector


class Recorder:
    PIPE = -1
    DEVNULL = -3

    def __init__(self):
        self.runs = []
        self.popens = []
        self.popen_kwargs = []

    def run(self, argv, **kw):
        self.runs.append(argv)

        class R:
            returncode = 0
            stdout = ""
        return R()

    def Popen(self, argv, **kw):
        self.popens.append(argv)
        self.popen_kwargs.append(kw)
        rec = self

        class P:
            returncode = 0

            def communicate(self, data=None, timeout=None):
                rec.popens[-1] = (argv, data)
                return b"", b""

            def kill(self):
                pass
        return P()


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(inject_x11, "subprocess", r)
    monkeypatch.setattr(X11Injector, "_clipboard_get", staticmethod(lambda: b"old"))
    return r


@pytest.fixture
def cfg():
    from whisper_dictate import config
    c = dict(config.DEFAULTS)
    c["restore_clipboard"] = False
    return c


def keystrokes(rec):
    return [a for a in rec.runs if a[:2] == ["xdotool", "key"]]


def test_clipboard_mode_never_sends_a_keystroke(rec, cfg, monkeypatch):
    cfg["insert_method"] = "clipboard"
    X11Injector(cfg).insert("привет")
    assert keystrokes(rec) == []
    assert rec.popens and rec.popens[0][1] == "привет".encode()


def test_paste_in_a_normal_window_uses_ctrl_v(rec, cfg, monkeypatch):
    monkeypatch.setattr(X11Injector, "_active_window_class",
                        staticmethod(lambda: "firefox Navigator"))
    X11Injector(cfg).insert("текст")
    assert keystrokes(rec)[-1][-1] == "ctrl+v"


def test_paste_in_a_terminal_uses_ctrl_shift_v(rec, cfg, monkeypatch):
    monkeypatch.setattr(X11Injector, "_active_window_class",
                        staticmethod(lambda: "gnome-terminal-server Gnome-terminal"))
    X11Injector(cfg).insert("текст")
    assert keystrokes(rec)[-1][-1] == "ctrl+shift+v"


def test_terminal_match_is_case_insensitive_and_partial(rec, cfg, monkeypatch):
    monkeypatch.setattr(X11Injector, "_active_window_class",
                        staticmethod(lambda: "ALACRITTY"))
    X11Injector(cfg).insert("текст")
    assert keystrokes(rec)[-1][-1] == "ctrl+shift+v"


def test_unknown_window_class_falls_back_to_ctrl_v(rec, cfg, monkeypatch):
    monkeypatch.setattr(X11Injector, "_active_window_class", staticmethod(lambda: ""))
    X11Injector(cfg).insert("текст")
    assert keystrokes(rec)[-1][-1] == "ctrl+v"


def test_type_mode_bypasses_the_clipboard(rec, cfg):
    cfg["insert_method"] = "type"
    X11Injector(cfg).insert("привет")
    assert rec.popens == []
    assert rec.runs[0][:3] == ["xdotool", "type", "--clearmodifiers"]
    assert rec.runs[0][-1] == "привет"


def test_availability_needs_a_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    assert X11Injector.is_available({}) is False


def test_wayland_injector_needs_wayland(monkeypatch):
    from whisper_dictate.backends.inject_wayland import WaylandInjector
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert WaylandInjector.is_available({}) is False


def test_clipboard_writer_never_waits_on_a_pipe(rec, cfg):
    """`xclip -i` must not be given a pipe for stderr.

    It forks a child that owns the selection until another application
    claims it, and that child inherits the parent's stderr. On a pipe,
    communicate() blocks for an EOF that arrives only when the clipboard
    changes hands, so every single paste costs the full timeout and then
    fails. A file has no such handshake.
    """
    X11Injector(cfg).insert("привет")

    writes = [kw for argv, kw in zip(rec.popens, rec.popen_kwargs)
              if "xclip" in str(argv)]
    assert writes, "expected the text to go through xclip"
    for kw in writes:
        assert kw.get("stderr") != rec.PIPE
        assert kw.get("stdout") != rec.PIPE
