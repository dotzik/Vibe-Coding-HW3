"""Workflow ``curate audit`` — Supervisor + Parallel.

Najde hygienické problémy v celé knowledge base: rozbité ``[[odkazy]]``,
zastaralá fakta a nesoulad s ``MEMORY.md`` indexem. Skládá dva patterny:

- **Supervisor** — kurátor řídí explicitní control loop. Strukturovaným
  JSON rozhodnutím (``delegate`` / ``finish``) deleguje na tři specialisty
  a sám rozhodne, kdy má dost informací pro závěrečný report.
- **Parallel** — uvnitř delegace na ``link-checker`` běží fan-out:
  každý podstrom KB se skenuje souběžně přes ``anyio.create_task_group()``,
  výsledky se slijí (fan-in) zpět ke specialistovi.

Audit je **read-only** — vrací markdown report, nemění soubory.

Tokenová efektivita: deterministickou detekci (rozbité odkazy, prošlá
data, mezery v indexu) dělá ``kb.py``. Specialisté dostávají jen kompaktní
digest s předzpracovanými nálezy, nikdy raw obsah souborů.
"""

from __future__ import annotations

from pathlib import Path

import anyio

from . import agents, kb
from .common import run_agent

# Strop control loopu supervisora — bohatě stačí na 3 delegace + finish.
SUPERVISOR_MAX_ITERACI = 6

# JSON schéma rozhodnutí supervisora (Supervisor control loop).
SUPERVISOR_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["delegate", "finish"],
            "description": "'delegate' přidělí práci specialistovi, "
            "'finish' vrátí závěrečný report",
        },
        "delegate_to": {
            "type": "string",
            "description": "Jméno specialisty (při action='delegate')",
        },
        "report": {
            "type": "string",
            "description": "Závěrečný markdown report (při action='finish')",
        },
    },
    "required": ["action"],
    "additionalProperties": False,
}

# Tři specialisté auditu — supervisor je oslovuje jménem.
_SPECIALISTE = {
    "link-checker": agents.LINK_CHECKER,
    "staleness-detector": agents.STALENESS_DETECTOR,
    "index-sync": agents.INDEX_SYNC,
}


# --------------------------------------------------------------------------
# Parallel — fan-out sken podstromů KB pro link-checker
# --------------------------------------------------------------------------

async def _sken_podstromu(
    nazev: str, soubory: list[kb.MemoryFile], existujici_slugy: set[str]
) -> tuple[str, list[dict[str, str]]]:
    """Deterministicky proskenuje jeden podstrom na rozbité odkazy.

    Spouští se souběžně pro každý podstrom. Práce je čistě deterministická
    (žádné API) — paralelizace tu demonstruje fan-out vzor a zrychluje
    sken velkých KB.
    """
    print(f"  [parallel] sken podstromu '{nazev}' ({len(soubory)} souborů)…")
    rozbite: list[dict[str, str]] = []
    for f in soubory:
        for cil in f.links:
            if cil not in existujici_slugy:
                rozbite.append({"source": f.rel_path, "target": cil})
    print(f"  [parallel] podstrom '{nazev}' hotov — {len(rozbite)} rozbitých odkazů")
    return nazev, rozbite


async def _parallel_link_scan(knowledge: kb.KnowledgeBase) -> list[dict[str, str]]:
    """Fan-out/fan-in: paralelní sken všech podstromů, pak agregace.

    Vzor převzat z ``3_workflows/2_parallel_workflow.py`` —
    ``anyio.create_task_group()`` rozjede sken každého podstromu souběžně,
    výsledky se slijí do jednoho seznamu.
    """
    print("\n  Fan-out: paralelní sken podstromů KB")
    existujici = knowledge.slugs
    vysledky: list[tuple[str, list[dict[str, str]]]] = []

    async with anyio.create_task_group() as tg:
        async def sken_a_sber(nazev: str, soubory: list[kb.MemoryFile]) -> None:
            vysledky.append(await _sken_podstromu(nazev, soubory, existujici))

        for nazev, soubory in knowledge.subtrees.items():
            tg.start_soon(sken_a_sber, nazev, soubory)

    # Fan-in: slij nálezy ze všech podstromů.
    vsechny: list[dict[str, str]] = []
    for _, rozbite in vysledky:
        vsechny.extend(rozbite)
    print(f"  Fan-in: celkem {len(vsechny)} rozbitých odkazů ze všech podstromů")
    return vsechny


# --------------------------------------------------------------------------
# Delegace na specialisty — každý dostane kompaktní pre-digest
# --------------------------------------------------------------------------

async def _spust_specialistu(
    nazev: str, knowledge: kb.KnowledgeBase
) -> str:
    """Spustí jednoho specialistu s pre-digestem připraveným ``kb.py``.

    ``link-checker`` navíc nejdřív rozjede Parallel fan-out sken. Každý
    specialista dostane jen předzpracované nálezy — ne raw obsah KB.
    """
    print(f"\n{'-' * 50}")
    print(f"Specialista: {nazev}")
    print(f"{'-' * 50}")

    definice = _SPECIALISTE[nazev]

    # Sestav digest podle specializace.
    if nazev == "link-checker":
        rozbite = await _parallel_link_scan(knowledge)
        if rozbite:
            digest = "\n".join(
                f"- '{r['source']}' odkazuje na neexistující slug '[[{r['target']}]]'"
                for r in rozbite
            )
        else:
            digest = "(žádné rozbité odkazy nenalezeny)"
    elif nazev == "staleness-detector":
        stale = kb.find_stale_files(knowledge)
        textovy = kb.digest_for_audit(knowledge.files)
        if stale:
            seznam = "\n".join(
                f"- '{s['source']}': pole '{s['field']}' = {s['date']} (v minulosti)"
                for s in stale
            )
        else:
            seznam = "(deterministicky nenalezeno žádné prošlé datové pole)"
        digest = (
            f"Prošlá datová pole:\n{seznam}\n\n"
            f"Digest souborů (pro posouzení textových podmínek):\n{textovy}"
        )
    else:  # index-sync
        digest = kb.digest_index(knowledge)

    prompt = (
        f"Tvá role: {definice.description}\n\n"
        f"Pre-digest knowledge base (připravený deterministicky):\n{digest}\n\n"
        f"Vrať stručný markdown se zjištěními."
    )

    text, _ = await run_agent(
        name=nazev,
        system_prompt=definice.prompt,
        prompt=prompt,
        model=definice.model,
        tichy=True,
    )
    print(f"  {nazev}: zjištění předáno supervisorovi ({len(text)} znaků)")
    return text


# --------------------------------------------------------------------------
# Supervisor control loop
# --------------------------------------------------------------------------

async def run_audit(root: str | Path) -> str:
    """Spustí celé workflow ``audit`` nad knowledge base.

    Supervisor v control loopu strukturovaným JSON deleguje na tři
    specialisty (uvnitř link-checkeru běží Parallel sken) a akcí
    'finish' vrátí závěrečný markdown report.
    """
    root = Path(root).resolve()
    print("\n" + "#" * 60)
    print("HUB CURATOR — audit (Supervisor + Parallel)")
    print("#" * 60)
    print(f"Kořen KB: {root}")

    # Deterministické načtení celé KB jednou — sdílí ho všichni specialisté.
    knowledge = kb.read_tree(root)
    print(f"Načteno: {len(knowledge.files)} memory souborů, "
          f"{len(knowledge.subtrees)} podstromů")

    # Historie, kterou supervisor akumuluje mezi iteracemi.
    seznam_specialistu = ", ".join(_SPECIALISTE.keys())
    historie = (
        f"Úkol: proveď audit hygieny knowledge base.\n"
        f"Dostupní specialisté: {seznam_specialistu}.\n"
        f"Deleguj na každého právě jednou, pak akcí 'finish' vrať report."
    )

    for iterace in range(1, SUPERVISOR_MAX_ITERACI + 1):
        print("\n" + "=" * 60)
        print(f"Supervisor — iterace {iterace}/{SUPERVISOR_MAX_ITERACI}")
        print("=" * 60)

        _, rozhodnuti = await run_agent(
            name="curator-supervisor",
            system_prompt=agents.CURATOR_SUPERVISOR.prompt,
            prompt=(
                f"Jsi kurátor řídící tým specialistů.\n\n{historie}\n\n"
                f"Rozhodni další krok: 'delegate' na jednoho ze specialistů "
                f"({seznam_specialistu}), nebo 'finish' až máš výsledky všech."
            ),
            model=agents.CURATOR_SUPERVISOR.model,
            output_format=SUPERVISOR_SCHEMA,
            tichy=True,
        )

        if not rozhodnuti:
            print("  Varování: supervisor nevrátil strukturované rozhodnutí.")
            continue

        if rozhodnuti["action"] == "finish":
            report = rozhodnuti.get("report", "(supervisor nevrátil report)")
            print(f"\n>>> Supervisor dokončil audit po {iterace} iteracích.")
            return report

        # Delegace na specialistu.
        komu = rozhodnuti.get("delegate_to", "")
        if komu not in _SPECIALISTE:
            print(f"  Neznámý specialista '{komu}' — přeskakuji.")
            historie += (
                f"\n\nChyba: '{komu}' není známý specialista. "
                f"Dostupní: {seznam_specialistu}."
            )
            continue

        print(f"  Supervisor deleguje na: {komu}")
        zjisteni = await _spust_specialistu(komu, knowledge)
        historie += f"\n\nVýsledek od '{komu}':\n{zjisteni}"

    print(f"\n>>> Dosažen strop {SUPERVISOR_MAX_ITERACI} iterací.")
    # Fallback report — supervisor nedospěl k 'finish'.
    return _fallback_report(knowledge)


def _fallback_report(knowledge: kb.KnowledgeBase) -> str:
    """Sestaví deterministický report, když supervisor nedoběhne.

    Pojistka pro tokenovou efektivitu — i bez supervisora dostane
    uživatel kompletní výsledek z dat, která ``kb.py`` už spočítal.
    """
    rozbite = kb.find_broken_links(knowledge)
    stale = kb.find_stale_files(knowledge)
    gaps = kb.find_index_gaps(knowledge)
    radky = ["# Audit report (deterministický fallback)", ""]
    radky.append(f"## Rozbité odkazy ({len(rozbite)})")
    radky += [f"- `{r['source']}` → `[[{r['target']}]]`" for r in rozbite] or ["- žádné"]
    radky.append("")
    radky.append(f"## Zastaralá fakta ({len(stale)})")
    radky += [f"- `{s['source']}`: {s['field']} = {s['date']}" for s in stale] or ["- žádné"]
    radky.append("")
    radky.append(f"## Nesoulad indexu")
    radky.append(f"- Chybí v MEMORY.md: {gaps['missing'] or 'žádné'}")
    radky.append(f"- Osiřelé řádky: {gaps['orphaned'] or 'žádné'}")
    return "\n".join(radky)
