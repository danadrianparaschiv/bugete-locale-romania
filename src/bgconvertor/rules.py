"""Aggregation rules and rollup pseudo-codes for local budgets.

The 00.xx rollup codes, 49.90, 98.02/99.02 etc. are NOT in Anexa 2 — they
exist only in report forms — so they are seeded here as data, together with
the arithmetic identities the validator enforces.

`verified` marks identities confirmed against primary sources; the ones set
False must be cross-checked in Phase 1 against the formulas the budget PDFs
themselves print in row names, e.g.
"TOTAL VENITURI (00.02+00.15+00.16+00.17+45.02+46.02+48.02)".

Codes are written with the local-budget suffix .02; `resuffix()` maps an
identity onto another budget (e.g. .10 own-revenue institutions) where the
same chapter structure applies.
"""

from __future__ import annotations

from pydantic import BaseModel

SUFFIX_LOCAL = "02"  # bugetul local
SUFFIX_OWN_REVENUE = "10"  # instituții finanțate din venituri proprii


class Rollup(BaseModel):
    code: str
    name: str


class Identity(BaseModel):
    """target = sum(plus) - sum(minus), all values for the same column."""

    target: str
    plus: list[str]
    minus: list[str] = []
    scope: str  # "revenue" | "expense_functional" | "expense_economic" | "section"
    verified: bool = True
    note: str = ""

    def resuffix(self, suffix: str) -> "Identity":
        def swap(code: str) -> str:
            parts = code.split(".")
            if len(parts) >= 2 and parts[1] == SUFFIX_LOCAL:
                parts[1] = suffix
            return ".".join(parts)

        return self.model_copy(
            update={
                "target": swap(self.target),
                "plus": [swap(c) for c in self.plus],
                "minus": [swap(c) for c in self.minus],
            }
        )


ROLLUP_CODES: list[Rollup] = [
    Rollup(code="00.01", name="TOTAL VENITURI"),
    Rollup(code="00.02", name="I. VENITURI CURENTE"),
    Rollup(code="00.03", name="A. VENITURI FISCALE"),
    Rollup(code="00.04", name="A1. IMPOZIT PE VENIT, PROFIT SI CASTIGURI DIN CAPITAL"),
    Rollup(code="00.05", name="A11. IMPOZIT PE VENIT, PROFIT SI CASTIGURI DE LA PERSOANE JURIDICE"),
    Rollup(code="00.06", name="A12. IMPOZIT PE VENIT, PROFIT SI CASTIGURI DIN CAPITAL DE LA PERSOANE FIZICE"),
    Rollup(code="00.07", name="A13. ALTE IMPOZITE PE VENIT, PROFIT SI CASTIGURI DIN CAPITAL"),
    Rollup(code="00.09", name="A3. IMPOZITE SI TAXE PE PROPRIETATE"),
    Rollup(code="00.10", name="A4. IMPOZITE SI TAXE PE BUNURI SI SERVICII"),
    Rollup(code="00.11", name="A6. ALTE IMPOZITE SI TAXE FISCALE"),
    Rollup(code="00.12", name="C. VENITURI NEFISCALE"),
    Rollup(code="00.13", name="C1. VENITURI DIN PROPRIETATE"),
    Rollup(code="00.14", name="C2. VANZARI DE BUNURI SI SERVICII"),
    Rollup(code="00.15", name="II. VENITURI DIN CAPITAL"),
    Rollup(code="00.16", name="III. OPERATIUNI FINANCIARE"),
    Rollup(code="00.17", name="IV. SUBVENTII"),
    Rollup(code="00.18", name="SUBVENTII DE LA ALTE NIVELE ALE ADMINISTRATIEI PUBLICE"),
    Rollup(code="00.19", name="SUBVENTII DE CAPITAL"),
    Rollup(code="00.20", name="SUBVENTII CURENTE"),
    Rollup(code="49.90", name="VENITURI PROPRII"),
    Rollup(code="50.02", name="Partea I-a SERVICII PUBLICE GENERALE"),
    Rollup(code="59.02", name="Partea a II-a APARARE, ORDINE PUBLICA SI SIGURANTA NATIONALA"),
    Rollup(code="63.02", name="Partea a III-a CHELTUIELI SOCIAL-CULTURALE"),
    Rollup(code="69.02", name="Partea a IV-a SERVICII SI DEZVOLTARE PUBLICA, LOCUINTE, MEDIU SI APE"),
    Rollup(code="79.02", name="Partea a V-a ACTIUNI ECONOMICE"),
    Rollup(code="96.02", name="Partea a VII-a REZERVE, EXCEDENT/DEFICIT"),
    Rollup(code="97.02", name="REZERVE"),
    Rollup(code="98.02", name="EXCEDENT"),
    Rollup(code="99.02", name="DEFICIT"),
    Rollup(code="5002", name="TOTAL CHELTUIELI"),
    Rollup(code="49.02", name="TOTAL CHELTUIELI (varianta 49.02)"),
    Rollup(code="49.10", name="TOTAL CHELTUIELI (varianta 49.10)"),
    # Economic classification grupe — heading rows in Anexa I ec, not coded entries.
    Rollup(code="01", name="CHELTUIELI CURENTE"),
    Rollup(code="70", name="CHELTUIELI DE CAPITAL"),
    Rollup(code="79", name="OPERATIUNI FINANCIARE"),
    Rollup(code="84", name="PLATI EFECTUATE IN ANII PRECEDENTI SI RECUPERATE IN ANUL CURENT"),
    Rollup(code="90", name="REZERVE, EXCEDENT/DEFICIT"),
]

REVENUE_IDENTITIES: list[Identity] = [
    Identity(
        target="00.01",
        plus=["00.02", "00.15", "00.16", "00.17", "45.02", "46.02", "48.02"],
        scope="revenue",
    ),
    Identity(target="00.02", plus=["00.03", "00.12"], scope="revenue"),
    Identity(target="00.03", plus=["00.04", "00.09", "00.10", "00.11"], scope="revenue"),
    Identity(target="00.04", plus=["00.05", "00.06", "00.07"], scope="revenue"),
    Identity(target="00.05", plus=["01.02"], scope="revenue"),
    Identity(target="00.06", plus=["03.02", "04.02"], scope="revenue"),
    Identity(target="00.07", plus=["05.02"], scope="revenue"),
    Identity(target="00.09", plus=["07.02"], scope="revenue"),
    Identity(
        target="00.10",
        plus=["10.02", "11.02", "12.02", "15.02", "16.02"],
        scope="revenue",
        verified=False,
        note="A4 composition to be confirmed against printed formulas in Phase 1",
    ),
    Identity(target="00.11", plus=["18.02"], scope="revenue"),
    Identity(target="00.12", plus=["00.13", "00.14"], scope="revenue"),
    Identity(target="00.13", plus=["30.02", "31.02"], scope="revenue"),
    Identity(
        target="00.14",
        plus=["33.02", "34.02", "35.02", "36.02", "37.02"],
        scope="revenue",
    ),
    Identity(target="00.15", plus=["39.02"], scope="revenue"),
    Identity(target="00.16", plus=["40.02", "41.02"], scope="revenue"),
    Identity(target="00.17", plus=["42.02", "43.02"], scope="revenue"),
    Identity(
        target="49.90",
        plus=["00.02", "00.15"],
        minus=["11.02", "37.02"],
        scope="revenue",
        note="VENITURI PROPRII = 00.02 - 11.02 - 37.02 + 00.15",
    ),
]

EXPENSE_PART_IDENTITIES: list[Identity] = [
    Identity(target="50.02", plus=["51.02", "54.02", "55.02", "56.02"], scope="expense_functional"),
    Identity(target="59.02", plus=["60.02", "61.02"], scope="expense_functional"),
    Identity(
        target="63.02",
        plus=["65.02", "66.02", "67.02", "68.02"],
        scope="expense_functional",
    ),
    Identity(target="69.02", plus=["70.02", "74.02"], scope="expense_functional"),
    Identity(
        target="79.02",
        plus=["80.02", "81.02", "83.02", "84.02", "87.02"],
        scope="expense_functional",
    ),
]

ECONOMIC_IDENTITIES: list[Identity] = [
    Identity(
        target="01",
        plus=["10", "20", "30", "40", "50", "51", "55", "56", "57", "58", "59", "60", "61", "65"],
        scope="expense_economic",
        note="grupa 01 CHELTUIELI CURENTE; formula printed in the 2026 annex itself",
    ),
    Identity(
        target="70",
        plus=["71", "72", "75"],
        scope="expense_economic",
        note="CHELTUIELI DE CAPITAL (cod 71+72+75); printed in the budget PDFs",
    ),
    Identity(
        target="79",
        plus=["80", "81"],
        scope="expense_economic",
        verified=False,
        note="OPERATIUNI FINANCIARE composition to be confirmed in Phase 1",
    ),
]

SECTION_IDENTITIES: list[Identity] = [
    Identity(
        target="37.02.03",
        plus=[],
        minus=["37.02.04"],
        scope="section",
        note="varsaminte functionare->dezvoltare: 37.02.03 = -37.02.04, nets to zero",
    ),
]

ALL_IDENTITIES = (
    REVENUE_IDENTITIES + EXPENSE_PART_IDENTITIES + ECONOMIC_IDENTITIES + SECTION_IDENTITIES
)

# Titles allowed in the economic classification whose values are negative.
NEGATIVE_CODES = {"37.02.03", "85", "85.01", "85.01.01"}
