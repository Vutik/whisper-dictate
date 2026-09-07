"""Classifying a captured buffer, which is how empty results get a cause."""
import numpy as np
import pytest

from whisper_dictate import diagnose


def buffer(value, seconds=1.0, samplerate=16000):
    return np.full(int(seconds * samplerate), value, dtype=np.float32)


def test_measure_reports_peak_rms_and_length():
    level = diagnose.measure(buffer(0.5, seconds=2.0))
    assert level.samples == 32000
    assert level.peak == pytest.approx(0.5)
    assert round(level.rms, 4) == 0.5


def test_measure_survives_an_empty_buffer():
    level = diagnose.measure(np.zeros(0, dtype=np.float32))
    assert (level.samples, level.peak, level.rms) == (0, 0.0, 0.0)


def test_measure_ignores_nan_and_inf():
    audio = np.array([0.1, np.nan, np.inf, -np.inf], dtype=np.float32)
    assert diagnose.measure(audio).peak == pytest.approx(0.1)


def test_no_samples_blames_the_stream():
    headline, detail = diagnose.explain_silence(diagnose.measure(buffer(0, 0)))
    assert "no audio" in headline.lower()
    assert "zero samples" in detail


def test_all_zeros_points_at_the_dead_device():
    headline, detail = diagnose.explain_silence(diagnose.measure(buffer(0.0)))
    assert headline == "Microphone is silent"
    # The detail has to carry the command, or it is not a diagnosis.
    assert "auto_null.monitor" in detail
    assert "wireplumber" in detail


def test_faint_signal_points_at_the_mute():
    headline, detail = diagnose.explain_silence(diagnose.measure(buffer(1e-4)))
    assert headline == "Microphone almost silent"
    assert "muted" in detail


def test_audible_signal_is_not_a_silence_fault():
    assert diagnose.explain_silence(diagnose.measure(buffer(0.3))) is None


def test_thresholds_do_not_overlap():
    assert diagnose.SILENT < diagnose.FAINT
    assert diagnose.explain_silence(diagnose.measure(buffer(diagnose.FAINT)))\
        is None
