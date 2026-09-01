"""Orientation detection for scanned pages.

Docling does not correct content rotated inside the raster (a landscape
table scanned into a portrait page), so we detect the rotation up front:
OCR a downscaled render at 0/90/270 degrees and keep the rotation that
yields the most confident text. Cached per page in the 'orient' stage.

The winning sample text is kept in the payload — it doubles as the cheap
input for page classification (prose vs table vs investment list).
"""

from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger("bgc.extract.orient")

ROTATIONS = (0, 90, 270, 180)
DETECT_SCALE = 1.0  # render scale for detection (small = fast)


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.log_level": "warning"})


def _score(result) -> tuple[float, str]:
    """Total confidence-weighted character count, and the joined text."""
    if result is None or result.txts is None:
        return 0.0, ""
    total = 0.0
    for text, conf in zip(result.txts, result.scores, strict=False):
        total += float(conf) * len(text)
    return total, " ".join(result.txts)


class AdaptiveOrient:
    """Per-document orientation with a learned upright prior.

    After STREAK consecutive upright pages, only the 0-degree pass runs; a
    full 4-rotation check still fires every FULL_EVERY pages, and whenever
    the quick score falls below half this file's typical upright score
    (which is how a suddenly-rotated page announces itself).
    """

    STREAK = 8
    FULL_EVERY = 15

    def __init__(self) -> None:
        self.streak = 0
        self.count = 0
        self.zero_scores: list[float] = []
        self.last_nonzero_rotation: int | None = None
        self.upright_since_rotation = 0

    def _remember(self, result: dict) -> dict:
        """Prefer the established page-block orientation only on close scores."""
        result = dict(result)
        rotation = int(result["rotation"])
        scores = result.get("scores") or {}
        if not rotation and self.last_nonzero_rotation is not None:
            upright = float(scores.get("0", 0.0))
            prior = float(scores.get(str(self.last_nonzero_rotation), 0.0))
            if upright and prior >= upright * 1.15:
                rotation = self.last_nonzero_rotation
                result["rotation"] = rotation
                result["continuity_override"] = True
        elif rotation and self.last_nonzero_rotation not in (None, rotation):
            winner = float(scores.get(str(rotation), 0.0))
            prior = float(scores.get(str(self.last_nonzero_rotation), 0.0))
            if winner and prior >= winner * 0.85:
                rotation = self.last_nonzero_rotation
                result["rotation"] = rotation
                result["continuity_override"] = True
        if rotation:
            self.last_nonzero_rotation = rotation
            self.upright_since_rotation = 0
        else:
            self.upright_since_rotation += 1
            if self.upright_since_rotation >= 3:
                self.last_nonzero_rotation = None
        return result

    def detect(self, image) -> dict:
        import numpy as np

        self.count += 1
        if self.streak >= self.STREAK and self.count % self.FULL_EVERY != 0:
            result = _engine()(np.array(image.convert("RGB")))
            score, text = _score(result)
            typical = sorted(self.zero_scores)[len(self.zero_scores) // 2]
            if score >= 0.5 * typical:
                self.zero_scores.append(score)
                return self._remember({
                    "rotation": 0,
                    "sample_text": text[:3000],
                    "scores": {"0": round(score, 1)},
                    "quick": True,
                })
            log.info("quick orient score dropped (%.0f < %.0f/2) — full check", score, typical)

        r = detect(image)
        if r["rotation"] == 0:
            self.streak += 1
            self.zero_scores.append(float(r["scores"]["0"]))
        else:
            self.streak = 0
        return self._remember(r)


def detect(image) -> dict:
    """PIL image -> {'rotation': deg, 'sample_text': str, 'scores': {...}}.

    rotation is the counter-clockwise correction to apply (PIL convention):
    rotate the page by this many degrees to make the text upright.
    """
    import numpy as np

    scores: dict[int, float] = {}
    samples: dict[int, str] = {}
    for deg in ROTATIONS:
        img = image if deg == 0 else image.rotate(deg, expand=True)
        result = _engine()(np.array(img.convert("RGB")))
        scores[deg], samples[deg] = _score(result)
        # Early exit: a decisively strong upright score means no rotation —
        # skips 2/3 of the OCR cost on the (majority) upright pages.
        # Densest rotated page observed scores ~1000 at 0deg.
        if deg == 0 and scores[0] >= 1400:
            return {
                "rotation": 0,
                "sample_text": samples[0][:3000],
                "scores": {"0": round(scores[0], 1)},
            }

    best = max(scores, key=scores.get)  # type: ignore[arg-type]
    # Prefer 0 unless a rotation is clearly better (upside-down text still
    # OCRs a little; require a 25% margin to rotate).
    if best != 0 and scores[best] < scores[0] * 1.25:
        best = 0
    return {
        "rotation": best,
        "sample_text": samples[best][:3000],
        "scores": {str(k): round(v, 1) for k, v in scores.items()},
    }
