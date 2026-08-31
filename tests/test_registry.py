"""Backend registration, availability filtering and "auto" resolution."""
import pytest

from whisper_dictate import registry
from whisper_dictate.interfaces import TextInjector


@pytest.fixture
def clean_kind(monkeypatch):
    """An isolated registry slot, so tests never disturb the real one."""
    monkeypatch.setitem(registry._REGISTRY, "inject", {})
    return "inject"


def make(name, priority, available=True):
    @registry.register("inject", name, priority=priority)
    class Impl(TextInjector):
        @classmethod
        def is_available(cls, cfg):
            return available

        def insert(self, text):
            pass
    return Impl


def test_auto_picks_highest_priority(clean_kind):
    make("low", 1)
    high = make("high", 99)
    assert registry.resolve("inject", "auto", {}) is high


def test_auto_skips_unavailable(clean_kind):
    make("unusable", 99, available=False)
    usable = make("usable", 1)
    assert registry.resolve("inject", "auto", {}) is usable


def test_explicit_name_wins_over_priority(clean_kind):
    low = make("low", 1)
    make("high", 99)
    assert registry.resolve("inject", "low", {}) is low


def test_explicit_name_is_used_even_if_unavailable(clean_kind):
    """Pinning a backend is a deliberate override, not a suggestion."""
    forced = make("forced", 1, available=False)
    assert registry.resolve("inject", "forced", {}) is forced


def test_unknown_name_raises_and_lists_options(clean_kind):
    make("real", 1)
    with pytest.raises(registry.BackendError, match="real"):
        registry.resolve("inject", "imaginary", {})


def test_no_usable_backend_raises(clean_kind):
    make("a", 1, available=False)
    with pytest.raises(registry.BackendError, match="no inject backend"):
        registry.resolve("inject", "auto", {})


def test_empty_kind_raises(clean_kind):
    with pytest.raises(registry.BackendError, match="no inject backends"):
        registry.resolve("inject", "auto", {})


def test_broken_availability_check_does_not_crash(clean_kind):
    @registry.register("inject", "broken", priority=99)
    class Broken(TextInjector):
        @classmethod
        def is_available(cls, cfg):
            raise RuntimeError("probe exploded")

        def insert(self, text):
            pass

    good = make("good", 1)
    assert registry.resolve("inject", "auto", {}) is good


def test_create_uses_the_configured_name(clean_kind):
    make("low", 1)
    high = make("high", 99)
    obj = registry.create("inject", {"backends": {"inject": "low"}})
    assert obj.backend_name == "low"
    assert registry.create("inject", {}).__class__ is high


def test_registering_an_unknown_kind_raises():
    with pytest.raises(registry.BackendError, match="unknown backend kind"):
        registry.register("telepathy", "brain")


def test_report_marks_availability(clean_kind):
    make("yes", 1)
    make("no", 1, available=False)
    line = registry.report({})
    assert "+yes" in line and "-no" in line


def test_real_backends_all_register():
    import whisper_dictate.backends  # noqa: F401
    for kind in registry.KINDS:
        assert registry.names(kind), f"no backends registered for {kind}"
