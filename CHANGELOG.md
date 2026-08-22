# Jurnal de modificări

## v0.1.0 — nepublicat

Prima versiune publică.

- Extragere: cititor de grilă digitală ghidat de antete (mai multe variante
  de furnizor, detecție per pagină a stilului de încadrare a denumirilor);
  pipeline docling OCR + TableFormer cu detecția orientării 0/90/180/270°
  (prior adaptiv de verticalitate); registru de formate (tabele generice cu
  antet, tabele transpuse, matrice de buget centralizat, pagini de
  continuare fără antet, coduri combinate capitol+economic, formate
  numerice românești și americane, normalizarea marcajelor „x" din OCR).
- Validare: registru Ordinul 1954/2005 (actualizare automată de pe
  mfinante.gov.ro), verificări de cod/denumire, sume de control pe linie,
  sume de ierarhie, identități între secțiuni; separarea documentelor pe
  instituții.
- Straturi LLM (opționale, Claude API): transcriere integrală de pagină
  pentru formate nestructurabile, reparare de sume cu probe aritmetice de
  acceptare, recuperare de celule; plafon ferm în dolari, cache de
  răspunsuri cu reluare gratuită, apeluri paralele, mod Batch API, citiri
  pe decupaje.
- Unelte: `triage` — verificare prealabilă cu estimări de cost/timp,
  magazie de rulări reluabilă per pagină, procese OCR paralele, `report`
  de calitate/cost, `eval` pe fixture-uri etalon, `corpus export`/`report`
  pentru seturi de date inter-municipale.
- Corpus: `data/2026/` — bugetele pe 2026 ale celor 41 de municipii
  reședință de județ plus București, codificate SIRUTA, cu manifest de
  surse.
