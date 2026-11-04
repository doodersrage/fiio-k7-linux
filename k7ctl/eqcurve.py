"""Frequency response of an EQ profile, for drawing its curve.

Uses the Audio EQ Cookbook (RBJ) biquads, the same formulas PipeWire's
param_eq builtin uses; tests/test_eqcurve.py checks the shapes, and the curve
was checked against tones measured through PipeWire.
"""

from __future__ import annotations

import cmath
import math

from .eq import Filter, Profile

RATE = 48000.0


def coefficients(f: Filter, rate: float = RATE) -> tuple[float, ...]:
    """(b0, b1, b2, a0, a1, a2) for one filter."""
    w0 = 2 * math.pi * min(f.freq, rate / 2 * 0.999) / rate
    cw, sw = math.cos(w0), math.sin(w0)
    q = f.q if f.q > 0 else 0.707
    alpha = sw / (2 * q)
    A = 10 ** (f.gain / 40)
    t = f.type
    if t == "bq_peaking":
        return (1 + alpha * A, -2 * cw, 1 - alpha * A, 1 + alpha / A, -2 * cw, 1 - alpha / A)
    if t == "bq_lowshelf":
        s = 2 * math.sqrt(A) * alpha
        return (A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw),
                A * ((A + 1) - (A - 1) * cw - s),
                (A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw),
                (A + 1) + (A - 1) * cw - s)
    if t == "bq_highshelf":
        s = 2 * math.sqrt(A) * alpha
        return (A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw),
                A * ((A + 1) + (A - 1) * cw - s),
                (A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw),
                (A + 1) - (A - 1) * cw - s)
    if t == "bq_lowpass":
        return ((1 - cw) / 2, 1 - cw, (1 - cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)
    if t == "bq_highpass":
        return ((1 + cw) / 2, -(1 + cw), (1 + cw) / 2, 1 + alpha, -2 * cw, 1 - alpha)
    if t == "bq_bandpass":
        return (alpha, 0.0, -alpha, 1 + alpha, -2 * cw, 1 - alpha)
    if t == "bq_notch":
        return (1.0, -2 * cw, 1.0, 1 + alpha, -2 * cw, 1 - alpha)
    if t == "bq_allpass":
        return (1 - alpha, -2 * cw, 1 + alpha, 1 + alpha, -2 * cw, 1 - alpha)
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def gain_db(coeffs: tuple[float, ...], freq: float, rate: float = RATE) -> float:
    b0, b1, b2, a0, a1, a2 = coeffs
    z = cmath.exp(-1j * 2 * math.pi * freq / rate)
    h = (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
    return 20 * math.log10(max(abs(h), 1e-12))


def frequencies(n: int = 200, lo: float = 20.0, hi: float = 20000.0) -> list[float]:
    return [lo * (hi / lo) ** (i / (n - 1)) for i in range(n)]


def response(profile: Profile, freqs: list[float] | None = None,
             include_preamp: bool = True) -> list[tuple[float, float]]:
    """[(Hz, dB), ...] for the whole profile."""
    freqs = freqs or frequencies()
    coeffs = [coefficients(f) for f in profile.filters]
    pre = profile.safe_preamp if include_preamp else 0.0
    return [(fr, pre + sum(gain_db(c, fr) for c in coeffs)) for fr in freqs]
