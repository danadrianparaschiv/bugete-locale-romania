# Politica de securitate

## Raportarea unei vulnerabilități

Folosiți [GitHub Security Advisories](https://github.com/danadrianparaschiv/bugete-locale-romania/security/advisories/new)
("Report a vulnerability") pentru raportare privată. Nu deschideți
issue-uri publice pentru vulnerabilități înainte de remediere.

Răspundem în mod normal în cel mult 7 zile.

## Domeniu

`bgconvertor` este un instrument local de linie de comandă — nu expune
servicii de rețea. Zonele relevante pentru securitate:

- **Prelucrarea PDF-urilor**: fișierele PDF sunt date de intrare
  nesigure; vulnerabilități de tip parser (prin `pypdf`, `docling`,
  biblioteci de imagine) sunt în domeniu.
- **Cheile API**: cheile LLM stau exclusiv în `.env` (negit-uit); orice
  cale prin care o cheie ar putea ajunge în fișiere comise, loguri
  comise sau în corpus este o vulnerabilitate.
- **Lanțul de aprovizionare**: dependențele sunt fixate în `uv.lock`.

## Date

Depozitul conține doar acte administrative publice. Dacă găsiți în corpus
date personale care nu ar trebui să fie publice (dincolo de conținutul
documentelor oficiale publicate de autorități), semnalați-le pe același
canal privat.

## Versiuni acoperite

Doar ultima versiune de pe ramura `main` primește remedieri.
