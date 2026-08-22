"""Validator-driven cell repair.

For a line the validator flagged (broken sum, unparseable cell, missing
value under a stamp), send a crop of the page around that row to Claude
with the row context and the arithmetic constraint, and get a structured
correction back. A repair is only ACCEPTED if it makes the constraint hold;
after `MAX_ATTEMPTS` the cell is marked UNRESOLVED — never guessed.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from .client import LLMClient

log = logging.getLogger("bgc.llm.repair")

MAX_ATTEMPTS = 2
CROP_PAD_ROWS = 3  # rows of visual context above/below the target row

REPAIR_PROMPT_V0 = """\
Imaginea este un fragment dintr-un tabel bugetar românesc scanat (buget local, \
clasificația Ordinul MFP 1954/2005). Valorile sunt în mii lei, format românesc \
(punct = separator de mii, virgulă = zecimale).

Rândul țintă: cod indicator {code!r}, denumire care conține {name_hint!r}.
Coloana țintă: {column} ({column_hint}).

Context OCR pentru acest rând (posibil greșit — de aceea întreb): {ocr_row}
{constraint}

Citește DOAR din imagine valoarea numerică exactă din rândul și coloana țintă. \
Dacă celula este acoperită de ștampilă sau ilizibilă, spune asta explicit — nu \
ghici. Dacă celula conține "X" sau este goală, raportează asta.
"""


class CellReading(BaseModel):
    value: str | None = Field(
        description="Valoarea exact cum apare în celulă, format românesc "
        "(ex. '18.677,50'), sau 'X', sau null dacă e goală"
    )
    legible: bool = Field(description="False dacă celula e acoperită/ilizibilă")
    confidence: str = Field(description="'high' | 'medium' | 'low'")
    note: str = Field(default="", description="Observații scurte (ștampilă, tăiat, etc.)")


def repair_cell(
    client: LLMClient,
    crop_image,
    *,
    code: str | None,
    name_hint: str,
    column: str,
    column_hint: str,
    ocr_row: str,
    constraint: str = "",
    page: int | None = None,
) -> CellReading:
    prompt = REPAIR_PROMPT_V0.format(
        code=code or "?",
        name_hint=name_hint[:60],
        column=column,
        column_hint=column_hint,
        ocr_row=ocr_row[:300],
        constraint=f"Constrângere aritmetică: {constraint}" if constraint else "",
    )
    return client.structured(
        "repair", prompt, CellReading,
        model=client.config.llm.repair_model,
        image=crop_image, page=page,
    )
