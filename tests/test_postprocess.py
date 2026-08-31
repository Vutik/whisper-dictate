"""Transcript clean-up: term restoration and hallucination filtering."""
import pytest

from whisper_dictate.backends.postprocess_rules import RuleProcessor, normalise
from whisper_dictate.interfaces import Transcript


def run(cfg, text):
    return RuleProcessor(cfg).process(Transcript(text=text, language="ru"))


@pytest.fixture
def cfg():
    from whisper_dictate import config
    return dict(config.DEFAULTS)


@pytest.mark.parametrize("said, expected", [
    ("под мохоз",                    "под macOS"),
    ("Открой сеттингс",              "Открой settings"),
    ("нажми аксепт",                 "нажми accept"),
    ("потом реджект",                "потом reject"),
    ("надо апрув и ассайн",          "надо approve и assign"),
    ("работает под линукс",          "работает под Linux"),
    ("пул реквест в гитхаб",         "pull request в GitHub"),
    ("там json и api",               "там JSON и API"),
])
def test_terms_are_restored(cfg, said, expected):
    assert run(cfg, said) == expected


def test_replacement_is_case_insensitive(cfg):
    assert run(cfg, "Линукс и ЛИНУКС") == "Linux и Linux"


def test_only_whole_words_are_replaced(cfg):
    cfg["replacements"] = {"api": "API"}
    assert run(cfg, "рапира и apiary") == "рапира и apiary"
    assert run(cfg, "вызов api сюда") == "вызов API сюда"


def test_longer_phrases_win(cfg):
    cfg["replacements"] = {"пул реквест": "pull request", "реквест": "request"}
    assert run(cfg, "открой пул реквест") == "открой pull request"


def test_plain_speech_is_untouched(cfg):
    text = "Обычная русская фраза без единого термина."
    assert run(cfg, text) == text


def test_hallucination_is_dropped(cfg):
    assert run(cfg, "Продолжение следует...") == ""
    assert run(cfg, "Спасибо за просмотр!") == ""


def test_hallucination_inside_a_sentence_is_kept(cfg):
    """Only a transcript that is *entirely* the phrase gets dropped."""
    text = "Он сказал спасибо за просмотр и ушёл"
    assert run(cfg, text) == text


def test_empty_and_whitespace(cfg):
    assert run(cfg, "") == ""
    assert run(cfg, "    ") == ""


def test_repeated_whitespace_collapses(cfg):
    assert run(cfg, "два    пробела") == "два пробела"


def test_bad_rule_does_not_break_the_processor(cfg):
    cfg["replacements"] = {"": "x", "линукс": "Linux"}
    assert run(cfg, "линукс") == "Linux"


def test_normalise_strips_punctuation_and_case():
    assert normalise("Продолжение следует...") == "продолжение следует"


def test_passthrough_backend_keeps_everything(cfg):
    from whisper_dictate.backends.postprocess_rules import PassthroughProcessor
    p = PassthroughProcessor(cfg)
    assert p.process(Transcript(text="  мохоз  ")) == "мохоз"
