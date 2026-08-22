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
