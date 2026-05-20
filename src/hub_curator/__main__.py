"""CLI vstupní bod Hub Curatoru.

Dva podpříkazy:
- ``curate add "<poznámka>"`` — z poznámky udělá memory soubor (Conditional
  + Sequential + Loop).
- ``curate audit`` — projde KB a najde hygienické problémy (Supervisor +
  Parallel).

Spuštění:
    curate add "..."          (po `uv sync`, console-script)
    python -m hub_curator ... (přímo z repozitáře)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio

# Windows konzole bývá cp1252 — výstupy obsahují českou diakritiku, proto
# přepneme stdout/stderr na UTF-8, aby tisk nehavaroval na UnicodeEncodeError.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

try:
    from dotenv import load_dotenv
    load_dotenv()  # načte ANTHROPIC_API_KEY z .env, pokud existuje
except ImportError:
    # python-dotenv je volitelný — bez něj se spoléhá na env proměnnou.
    pass

# Výchozí KB = přibalená sample_kb/ vedle kořene projektu.
_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "sample_kb"


def _build_parser() -> argparse.ArgumentParser:
    """Sestaví argparse parser se dvěma podpříkazy. Nápověda je česky."""
    parser = argparse.ArgumentParser(
        prog="curate",
        description="Hub Curator — kurátor markdown auto-memory knowledge base.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- add ---
    p_add = subparsers.add_parser(
        "add",
        help="Z raw poznámky vytvoří validní memory soubor a zařadí do indexu.",
        description="Workflow add: klasifikuje typ, vytvoří draft, projde "
        "QA-loopem a zapíše soubor + řádek do MEMORY.md.",
    )
    p_add.add_argument(
        "note",
        help="Raw brainstorm poznámka volným textem (v uvozovkách).",
    )
    p_add.add_argument(
        "--root",
        default=str(_DEFAULT_ROOT),
        help="Kořen knowledge base (výchozí: přibalená sample_kb/).",
    )
    p_add.add_argument(
        "--dry-run",
        action="store_true",
        help="Jen vytiskne výsledný soubor, nic nezapíše.",
    )

    # --- audit ---
    p_audit = subparsers.add_parser(
        "audit",
        help="Projde knowledge base a najde hygienické problémy.",
        description="Workflow audit: supervisor deleguje na specialisty "
        "(odkazy, zastaralost, index) a vrátí markdown report. Read-only.",
    )
    p_audit.add_argument(
        "--root",
        default=str(_DEFAULT_ROOT),
        help="Kořen knowledge base (výchozí: přibalená sample_kb/).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Hlavní vstupní bod CLI. Vrací návratový kód procesu."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Chyba: kořen KB neexistuje: {root}", file=sys.stderr)
        return 2

    # Import workflow modulů až tady — drží `--help` rychlou a nezávislou na SDK.
    if args.command == "add":
        from .add_memory import run_add
        anyio.run(run_add, args.note, root, args.dry_run)
        return 0

    if args.command == "audit":
        from .audit import run_audit
        report = anyio.run(run_audit, root)
        print("\n" + "=" * 60)
        print("AUDIT REPORT")
        print("=" * 60 + "\n")
        print(report)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
