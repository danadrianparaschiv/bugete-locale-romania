"""Populează și publică execuția bugetară dintr-un trimestru nou.

Fluxul, o dată pe trimestru:

  1. cauți rapoartele pe portalul ANAF (căutarea cere CAPTCHA — pas manual)
  2. pui fișierele în structura existentă:
     data/execution/<an>/<județ>/<oraș>/q<N>/forexebug_execution.xlsx
  3. rulezi acest script

Ce face scriptul: verifică fiecare fișier (identitatea entității, data
raportului, sumele de control ale raportului), îl înregistrează cu checksum
în manifest, regenerează instantaneele și agregatul, apoi arată exact ce s-a
schimbat. Cu `--publica`, comite pe main și dă push — publicarea pe site
pornește singură din workflow-ul `pages`.

    python3 scripts/publica_executie.py                 # verifică și raportează
    python3 scripts/publica_executie.py --an 2026 --trimestru 3
    python3 scripts/publica_executie.py --publica       # + commit & push
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, **kw)


def capture(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--an", default="2026", help="Anul corpusului de execuție")
    ap.add_argument("--trimestru", type=int, choices=(1, 2, 3, 4),
                    help="Doar un trimestru (implicit: toate cele găsite pe disc)")
    ap.add_argument("--publica", action="store_true",
                    help="Comite pe main și dă push (publicarea pe site pornește singură)")
    args = ap.parse_args()

    exec_dir = ROOT / "data" / "execution" / args.an
    if not exec_dir.is_dir():
        print(f"nu există {exec_dir}", file=sys.stderr)
        return 1

    cmd = ["uv", "run", "bgconvertor", "execution", "ingest", "--exec-dir", str(exec_dir)]
    if args.trimestru:
        cmd += ["--quarter", str(args.trimestru)]
    if run(cmd).returncode != 0:
        print("\n⚠ verificarea a semnalat probleme — nimic nu se publică până nu sunt "
              "rezolvate (vezi verification.json)", file=sys.stderr)
        return 1

    run(["uv", "run", "bgconvertor", "execution", "status", "--exec-dir", str(exec_dir)])
    run(["uv", "run", "bgconvertor", "corpus", "aggregate"])

    changed = capture(["git", "status", "--porcelain", "data/execution"])
    if not changed:
        print("\nnimic nou de publicat — corpusul e deja la zi")
        return 0
    print("\nfișiere schimbate:")
    print(changed)

    if not args.publica:
        print("\nPentru publicare: rulează din nou cu --publica")
        return 0

    trim = f" T{args.trimestru}" if args.trimestru else ""
    msg = (f"corpus: execuția bugetară {args.an}{trim}\n\n"
           "Rapoarte Forexebug verificate (identitate entitate, data raportului, "
           "sume de control) și instantanee regenerate.")
    run(["git", "add", "data/execution"])
    if run(["git", "commit", "-m", msg]).returncode != 0:
        return 1
    if run(["git", "pull", "--rebase"]).returncode != 0:
        print("rebase eșuat — rezolvă manual, apoi `git push`", file=sys.stderr)
        return 1
    if run(["git", "push"]).returncode != 0:
        return 1
    print("\n✓ publicat — workflow-ul `pages` reconstruiește site-ul în câteva minute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
