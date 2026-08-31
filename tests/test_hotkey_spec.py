"""Hotkey specification parsing, without touching any input device."""
import types

import pytest

from whisper_dictate.backends.hotkey_evdev import EvdevHotkey


class StubCodes:
    """Just enough of evdev.ecodes for parsing."""
    KEY_SPACE = 57
    KEY_D = 32
    KEY_LEFTCTRL, KEY_RIGHTCTRL = 29, 97
    KEY_LEFTALT, KEY_RIGHTALT = 56, 100
    KEY_LEFTSHIFT, KEY_RIGHTSHIFT = 42, 54
    KEY_LEFTMETA, KEY_RIGHTMETA = 125, 126


def parse(spec):
    h = EvdevHotkey({}, spec, lambda: None)
    return h, h._parse(StubCodes)


def test_plain_combination():
    h, (code, mods) = parse("ctrl+alt+space")
    assert h.error is None
    assert code == StubCodes.KEY_SPACE
    assert mods == [{29, 97}, {56, 100}]


def test_modifier_aliases_are_equivalent():
    _, (_, a) = parse("control+super+d")
    _, (_, b) = parse("ctrl+win+d")
    assert a == b


def test_left_and_right_modifiers_both_count():
    _, (_, mods) = parse("shift+d")
    assert mods == [{42, 54}]


def test_case_is_ignored():
    _, (code, _) = parse("CTRL+ALT+SPACE")
    assert code == StubCodes.KEY_SPACE


def test_bare_key_without_modifiers():
    h, (code, mods) = parse("space")
    assert h.error is None and code == StubCodes.KEY_SPACE and mods == []


def test_unknown_key_is_reported():
    h, (code, _) = parse("ctrl+nosuchkey")
    assert code is None
    assert "unknown key" in h.error


def test_unknown_modifier_is_reported():
    h, (code, _) = parse("hyper+d")
    assert code is None
    assert "unknown modifier" in h.error


def test_empty_spec_is_reported():
    h, (code, _) = parse("  ")
    assert code is None
    assert "empty" in h.error


def test_availability_without_readable_devices(monkeypatch):
    import whisper_dictate.backends.hotkey_evdev as mod
    fake = types.SimpleNamespace(list_devices=lambda: [])
    monkeypatch.setitem(__import__("sys").modules, "evdev", fake)
    assert EvdevHotkey.is_available({}) is False
