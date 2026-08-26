"""Scan preprocessing before OCR.

Stamp removal: official stamps are saturated blue/purple ink stamped over
black text — dropping high-saturation pixels to white erases the stamp
while leaving the (unsaturated) print intact. Cheap, and attacks the
single biggest scanned-error source in the corpus.
"""

from __future__ import annotations

import numpy as np


def remove_stamps(image, sat_threshold: int = 70, min_value: int = 60):
    """PIL image -> PIL image with saturated (colored) pixels whitened.

    sat_threshold: HSV saturation (0-255) above which a pixel counts as ink
    from a colored stamp. min_value: very dark pixels are kept regardless
    (black text can pick up slight color casts from scanning).
    """
    from PIL import Image

    hsv = np.array(image.convert("HSV"))
    rgb = np.array(image.convert("RGB"))
    stamp = (hsv[:, :, 1] > sat_threshold) & (hsv[:, :, 2] > min_value)
    rgb[stamp] = (255, 255, 255)
    return Image.fromarray(rgb)


def colored_ink_fraction(image, sat_threshold: int = 70, min_value: int = 60) -> float:
    """Fraction of pixels likely belonging to a blue/purple official stamp."""
    hsv = np.array(image.convert("HSV"))
    stamp = (hsv[:, :, 1] > sat_threshold) & (hsv[:, :, 2] > min_value)
    return float(stamp.mean())


def _projection_score(image) -> float:
    """Alignment score: ruled/text rows peak when the page is deskewed."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    ink = gray < 190
    if not ink.any():
        return 0.0
    horizontal = ink.sum(axis=1, dtype=np.float64)
    vertical = ink.sum(axis=0, dtype=np.float64)
    return float(np.var(np.diff(horizontal)) + 0.25 * np.var(np.diff(vertical)))


def estimate_deskew_angle(
    image, max_degrees: float = 2.0, step: float = 0.5, min_gain: float = 0.025
) -> float:
    """Return the small corrective PIL rotation, or zero without a clear gain."""
    from PIL import Image

    sample = image.convert("L")
    if max(sample.size) > 1000:
        scale = 1000 / max(sample.size)
        sample = sample.resize(
            (max(1, round(sample.width * scale)), max(1, round(sample.height * scale)))
        )
    angles = [round(-max_degrees + index * step, 3) for index in range(round(2 * max_degrees / step) + 1)]
    scores = {}
    for angle in angles:
        candidate = sample if angle == 0 else sample.rotate(
            angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
        )
        scores[angle] = _projection_score(candidate)
    baseline = scores.get(0.0, _projection_score(sample))
    best = max(scores, key=scores.get)
    if best == 0 or baseline <= 0 or scores[best] < baseline * (1 + min_gain):
        return 0.0
    return float(best)


def adaptive_preprocess(image, max_deskew_degrees: float = 2.0):
    """Build one recovery candidate and record exactly what changed."""
    from PIL import Image

    stamp_fraction = colored_ink_fraction(image)
    stamp_removed = stamp_fraction >= 0.0002
    candidate = remove_stamps(image) if stamp_removed else image
    angle = estimate_deskew_angle(candidate, max_degrees=max_deskew_degrees)
    if angle:
        candidate = candidate.rotate(
            angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white"
        )
    return candidate, {
        "stamp_removed": stamp_removed,
        "colored_ink_fraction": round(stamp_fraction, 6),
        "deskew_angle": angle,
    }
