"""Workflow ``curate add`` — Conditional + Sequential + Loop.

Z raw brainstorm poznámky udělá hotový validní memory soubor a zařadí
ho do indexu. Skládá tři patterny do jednoho běhu:

- **Sequential** — rámec celého příkazu: classify → draft → QA-loop → write.
- **Conditional** — krok ``classify``: classifier vybere typ memory a
  workflow routuje na odpovídající draft-handler (jeden ze čtyř).
- **Loop** — QA fáze: draft → qa_evaluator → refiner, dokud skóre ≥ 85
  nebo max 3 iterace.

Tokenová efektivita: deterministiku (slug, frontmatter, indexový řádek)
řeší ``kb.py``; agenti dostávají jen poznámku a draft, ne stav celé KB.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import agents, kb
from .common import run_agent

# Stropy pro QA-loop — nízké kvůli tokenové efektivitě.
QA_PRAH = 85
QA_MAX_ITERACI = 3

# JSON schéma pro strukturované rozhodnutí klasifikátoru (Conditional).
CLASSIFIER_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(agents.TYPY_MEMORY),
            "description": "Typ memory záznamu",
        },
        "reason": {
            "type": "string",
            "description": "Stručné zdůvodnění volby typu",
        },
    },
    "required": ["type"],
    "additionalProperties": False,
}

# JSON schéma pro výstup hodnotitele kvality (Loop).
QA_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {
            "type": "integer",
            "description": "Skóre kvality 0-100",
        },
        "acceptable": {
            "type": "boolean",
            "description": "True když skóre dosahuje prahu",
        },
        "feedback": {
            "type": "string",
            "description": "Konkrétní zpětná vazba pro opravu",
        },
    },
    "required": ["score", "feedback"],
    "additionalProperties": False,
}

# Mapování typu na draft-handler (decision i learning sdílí jeden handler).
_HANDLER_KLIC = {
    "feedback": "feedback",
    "project": "project",
    "reference": "reference",
    "decision": "decision",
    "learning": "decision",
}

# Mapování typu na podsložku KB.
_PODSLOZKA = {
    "feedback": "feedbacks",
    "project": "projects",
    "reference": "references",
    "decision": "decisions",
    "learning": "learnings",
}

# Frontmatter klíče dle sample_kb/references/konvence-memory-souboru.md.
# `title`, `type`, `created`, `tags` jsou jádro konvence; volitelná datová
# pole drží prošlé/odložené záznamy (využívá je kb.find_stale_files).
_KLICE_JADRO = ("title", "type", "created", "tags")
_KLICE_DATA = ("expires", "review_by", "valid_until", "defer_until", "deadline")
_POVOLENE_KLICE = _KLICE_JADRO + _KLICE_DATA


async def _classify(note: str) -> tuple[str, str]:
    """Conditional — klasifikuje poznámku a vrátí ``(typ, zduvodneni)``."""
    print("\n" + "=" * 60)
    print("KROK 1/4: Klasifikace (Conditional)")
    print("=" * 60)

    _, strukturovany = await run_agent(
        name="classifier",
        system_prompt=agents.CLASSIFIER.prompt,
        prompt=f"Klasifikuj tuto auto-memory poznámku:\n\n{note}",
        model=agents.CLASSIFIER.model,
        output_format=CLASSIFIER_SCHEMA,
        tichy=True,
    )

    if not strukturovany or strukturovany.get("type") not in agents.TYPY_MEMORY:
        print("  Varování: klasifikace selhala, použit fallback 'reference'.")
        return "reference", "fallback po selhání klasifikátoru"

    typ = strukturovany["type"]
    zduvodneni = strukturovany.get("reason", "")
    print(f"  Typ: {typ}  ({zduvodneni})")
    return typ, zduvodneni


async def _draft(note: str, typ: str) -> str:
    """Conditional pokračování — spustí draft-handler vybraný dle typu."""
    print("\n" + "=" * 60)
    print(f"KROK 2/4: Draft přes handler '{typ}' (Conditional → Sequential)")
    print("=" * 60)

    handler = agents.DRAFT_HANDLERS[_HANDLER_KLIC[typ]]
    prompt = (
        f"Dnešní datum: {date.today().isoformat()}.\n"
        f"Typ memory: {typ}.\n\n"
        f"Z této poznámky vytvoř hotový memory soubor:\n\n{note}"
    )
    text, _ = await run_agent(
        name=f"draft:{typ}",
        system_prompt=handler.prompt,
        prompt=prompt,
        model=handler.model,
        tichy=True,
        allowed_tools=[],  # žádné nástroje — agent jen vrací text, zápis dělá kb.py
    )
    print(f"  Draft hotov ({len(text)} znaků).")
    return _ocisti_markdown(text)


async def _qa_loop(draft: str, typ: str) -> tuple[str, int]:
    """Loop — opakuje hodnocení a opravu, dokud draft není dost dobrý.

    Vrací ``(finalni_obsah, skore)``. Loop končí při skóre ≥ QA_PRAH
    nebo po QA_MAX_ITERACI iteracích.
    """
    print("\n" + "=" * 60)
    print(f"KROK 3/4: QA-loop (Loop, práh {QA_PRAH}, max {QA_MAX_ITERACI})")
    print("=" * 60)

    obsah = draft
    skore = 0
    for iterace in range(1, QA_MAX_ITERACI + 1):
        # Hodnocení kvality — strukturovaný výstup.
        _, hodnoceni = await run_agent(
            name=f"qa_evaluator (iter {iterace})",
            system_prompt=agents.QA_EVALUATOR.prompt,
            prompt=(
                f"Typ memory: {typ}. Ohodnoť tento draft memory souboru:\n\n"
                f"{obsah}"
            ),
            model=agents.QA_EVALUATOR.model,
            output_format=QA_SCHEMA,
            tichy=True,
        )

        if not hodnoceni:
            print(f"  Iterace {iterace}: hodnocení selhalo, končím loop.")
            break

        skore = int(hodnoceni.get("score", 0))
        zpetna_vazba = hodnoceni.get("feedback", "")
        print(f"  Iterace {iterace}: skóre {skore}/100")

        if skore >= QA_PRAH:
            print(f"  Práh {QA_PRAH} dosažen — loop končí.")
            break
        if iterace == QA_MAX_ITERACI:
            print("  Dosažen strop iterací — beru nejlepší dostupný draft.")
            break

        # Oprava draftu dle zpětné vazby.
        print(f"    → refiner opravuje: {zpetna_vazba[:80]}")
        opraveno, _ = await run_agent(
            name=f"refiner (iter {iterace})",
            system_prompt=agents.REFINER.prompt,
            prompt=(
                f"Zpětná vazba:\n{zpetna_vazba}\n\n"
                f"Oprav tento draft:\n\n{obsah}"
            ),
            model=agents.REFINER.model,
            tichy=True,
            allowed_tools=[],  # žádné nástroje — agent jen vrací text
        )
        obsah = _ocisti_markdown(opraveno)

    return obsah, skore


def _write(root: Path, obsah: str, typ: str) -> tuple[str, str]:
    """Sequential závěr — deterministicky zapíše soubor a indexový řádek.

    Vrací ``(rel_path, indexovy_radek)``. Zápis dělá ``kb.py``, ne agent.
    """
    print("\n" + "=" * 60)
    print("KROK 4/4: Zápis souboru a indexu (Sequential)")
    print("=" * 60)

    frontmatter, _ = kb.parse_frontmatter(obsah)
    titulek = frontmatter.get("title") or _prvni_h1(obsah) or "memory"
    slug = kb.slugify(titulek)
    podslozka = _PODSLOZKA[typ]
    rel_path = f"{podslozka}/{slug}.md"

    # Ošetři kolizi slugu — připoj datum.
    if (root / rel_path).exists():
        rel_path = f"{podslozka}/{slug}-{date.today().isoformat()}.md"

    indexovy_radek = f"- [{titulek}]({rel_path})"

    kb.write_memory(root, rel_path, obsah)
    kb.append_index_line(root, indexovy_radek)
    print(f"  Zapsáno: {rel_path}")
    print(f"  Index:   {indexovy_radek}")
    return rel_path, indexovy_radek


async def run_add(note: str, root: str | Path, dry_run: bool = False) -> None:
    """Spustí celé workflow ``add`` nad jednou poznámkou.

    Sequential rámec: classify (Conditional) → draft (Conditional) →
    QA-loop (Loop) → write. Při ``dry_run`` se soubor nezapíše, jen
    vytiskne výsledný obsah a navržený indexový řádek.
    """
    root = Path(root).resolve()
    print("\n" + "#" * 60)
    print("HUB CURATOR — add (Sequential ⊃ Conditional + Loop)")
    print("#" * 60)
    print(f"Poznámka: {note}")
    print(f"Kořen KB: {root}")
    print(f"Režim:    {'dry-run (bez zápisu)' if dry_run else 'zápis'}")

    typ, _ = await _classify(note)
    draft = await _draft(note, typ)
    obsah, skore = await _qa_loop(draft, typ)

    # Frontmatter klíče, které kód zná autoritativně (type, konvence klíčů),
    # se srovnají s rozhodnutím systému — ne s tím, co napsal agent.
    obsah = _normalizuj_frontmatter(obsah, typ)

    if dry_run:
        print("\n" + "=" * 60)
        print("KROK 4/4: dry-run — soubor se NEzapisuje")
        print("=" * 60)
        frontmatter, _ = kb.parse_frontmatter(obsah)
        titulek = frontmatter.get("title") or _prvni_h1(obsah) or "memory"
        slug = kb.slugify(titulek)
        rel_path = f"{_PODSLOZKA[typ]}/{slug}.md"
        print(f"\nNavržená cesta:  {rel_path}")
        print(f"Navržený index:  - [{titulek}]({rel_path})")
        print(f"Finální skóre:   {skore}/100")
        print("\n--- OBSAH SOUBORU ---")
        print(obsah)
        print("--- KONEC ---")
        return

    rel_path, _ = _write(root, obsah, typ)
    print("\n" + "#" * 60)
    print(f"HOTOVO — memory soubor '{rel_path}' vytvořen (skóre {skore}/100).")
    print("#" * 60)


# --------------------------------------------------------------------------
# Pomocné deterministické funkce
# --------------------------------------------------------------------------

def _ocisti_markdown(text: str) -> str:
    """Defenzivní sanitizace odpovědi draft-handleru / refineru.

    Agenti mají v promptu zakázáno přidávat omáčku i code-fence a běží
    bez nástrojů, ale tato funkce je pojistka: i kdyby agent znovu
    ukecal výstup, vytáhne z něj čisté tělo memory souboru.

    Postup:
    1. Když je kdekoli v textu code-fence blok, vezmi jeho obsah
       (i obalený konverzačním textem před/za fencem).
    2. Z výsledku zahoď cokoli před prvním řádkem '---' frontmatteru.
    """
    text = text.strip()

    # 1) Code-fence kdekoli v textu — ber obsah prvního bloku.
    fence = re.search(r"```[a-zA-Z0-9_-]*\r?\n(.*?)\r?\n```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 2) Ořízni úvodní konverzační omáčku — vše před prvním '---' řádkem.
    radky = text.splitlines()
    for i, radek in enumerate(radky):
        if radek.strip() == "---":
            text = "\n".join(radky[i:]).strip()
            break

    return text


def _normalizuj_frontmatter(obsah: str, typ: str) -> str:
    """Sjednotí frontmatter s deterministickým rozhodnutím systému.

    Frontmatter klíče, které kód zná autoritativně, nesmí určovat agent:

    1. ``type`` se VŽDY přepíše na hodnotu z classifieru — i kdyby
       draft-handler napsal jiný typ, autoritativní je classifier.
    2. Klíče mimo konvenci (např. stray ``slug``) se zahodí — povolené
       jsou jen ``_POVOLENE_KLICE`` dle konvence-memory-souboru.md.
    3. Pořadí klíčů se ustálí: jádro konvence, pak volitelná datová pole.

    Když ``type`` ve frontmatteru chybí, doplní se. Soubory bez
    frontmatteru se vrací beze změny (ošetří je až QA-loop).
    """
    frontmatter, telo = kb.parse_frontmatter(obsah)
    if not frontmatter:
        return obsah

    # Autoritativní type z classifieru — přebíjí cokoli od draft-handleru.
    frontmatter["type"] = typ

    # Přeskládej do konvenčního pořadí, zahoď stray klíče.
    cisty: dict[str, str] = {}
    for klic in _POVOLENE_KLICE:
        if klic in frontmatter:
            cisty[klic] = frontmatter[klic]

    return kb.serialize_frontmatter(cisty, telo)


def _prvni_h1(text: str) -> str | None:
    """Vrátí text prvního H1 nadpisu, nebo None."""
    for radek in text.splitlines():
        if radek.startswith("# "):
            return radek[2:].strip()
    return None
