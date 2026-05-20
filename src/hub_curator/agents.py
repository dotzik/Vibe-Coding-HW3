"""Specifikace všech agentů Hub Curatoru.

Každý agent je ``AgentDefinition`` s rolí, system promptem (2-4 věty
dle požadavku na stručnost) a modelem. Workflow moduly (``add_memory``,
``audit``) si odsud agenty berou a spouští přes :func:`common.run_agent`.

Rozdělení modelů:
- ``haiku`` (levný): classifier, supervisor, audit specialisté
- ``sonnet`` (kvalita): draft-handlery, qa_evaluator, refiner
"""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

from .common import MODEL_KVALITA, MODEL_LEVNY

# Pět typů memory záznamů — určuje je classifier a routuje na draft-handler.
TYPY_MEMORY = ("feedback", "project", "reference", "decision", "learning")


# --------------------------------------------------------------------------
# Agenti workflow `add` — Conditional + Sequential + Loop
# --------------------------------------------------------------------------

CLASSIFIER = AgentDefinition(
    description="Klasifikátor typu memory záznamu",
    prompt=(
        "Jsi klasifikátor auto-memory poznámek. Rozhodni, do které z kategorií "
        "feedback, project, reference, decision, learning poznámka patří. "
        "Feedback = pravidlo chování, reference = trvalý fakt/odkaz, decision = "
        "učiněné rozhodnutí, learning = poznatek z praxe, project = stav projektu. "
        "Vrať jen strukturovaný JSON."
    ),
    model=MODEL_LEVNY,
)

# Draft-handlery — jeden na typ, každý zná svůj template frontmatteru.
# Conditional krok vybere právě jeden podle výstupu klasifikátoru.
#
# DŮLEŽITÉ: tyto agenty běží s ``tools=[]`` (žádné nástroje). Soubor
# NIKDY nezapisují — zápis dělá výhradně ``kb.py``. Jejich jediný
# výstup je syrové tělo souboru, viz ``_VRAT_SYROVE``.
_VRAT_SYROVE = (
    "Vrať POUZE syrový obsah memory souboru — frontmatter a tělo. "
    "ŽÁDNÝ úvodní text, ŽÁDNÉ markdown code-fence bloky (```), "
    "ŽÁDNÉ komentáře, ŽÁDNÉ otázky, ŽÁDNÁ nabídka soubor uložit. "
    "Soubor nikam neukládáš — to udělá volající. "
    "První znak tvého výstupu MUSÍ být '-' z '---' frontmatteru."
)

_DRAFT_SPOLECNE = (
    "Vytvoř hotový markdown memory soubor: YAML frontmatter mezi '---' "
    "(klíče title, created, tags), pod ním H1 titulek a stručné tělo. "
    "Klíč 'type' ani cestu souboru NEvyplňuj — type a umístění doplní "
    "systém deterministicky podle klasifikace; jiné klíče (slug apod.) "
    "do frontmatteru nepřidávej. "
    "Drž 'one fact = one file' — jeden záznam, žádná omáčka. Česky. "
    f"{_VRAT_SYROVE}"
)

DRAFT_HANDLERS: dict[str, AgentDefinition] = {
    "feedback": AgentDefinition(
        description="Draft-handler pro feedback (pravidlo chování)",
        prompt=(
            f"Jsi editor feedback memory. {_DRAFT_SPOLECNE} "
            "Tělo musí mít sekce '**Proč:**' a '**Jak aplikovat:**'."
        ),
        model=MODEL_KVALITA,
    ),
    "project": AgentDefinition(
        description="Draft-handler pro project (stav projektu)",
        prompt=(
            f"Jsi editor project memory. {_DRAFT_SPOLECNE} "
            "Tělo shrne aktuální stav a další krok projektu."
        ),
        model=MODEL_KVALITA,
    ),
    "reference": AgentDefinition(
        description="Draft-handler pro reference (trvalý fakt)",
        prompt=(
            f"Jsi editor reference memory. {_DRAFT_SPOLECNE} "
            "Tělo je věcný, nadčasový fakt — žádné názory ani úkoly."
        ),
        model=MODEL_KVALITA,
    ),
    "decision": AgentDefinition(
        description="Draft-handler pro decision a learning",
        prompt=(
            f"Jsi editor decision/learning memory. {_DRAFT_SPOLECNE} "
            "Tělo musí mít sekce '**Kontext:**' a '**Rozhodnutí:**' "
            "(u learning '**Poznatek:**')."
        ),
        model=MODEL_KVALITA,
    ),
}

QA_EVALUATOR = AgentDefinition(
    description="Hodnotitel kvality draftu memory souboru",
    prompt=(
        "Jsi hodnotitel memory souborů. Zkontroluj validní frontmatter, "
        "rozlišitelný titulek, dodržení template typu a žádné odkazy na "
        "neexistující slugy. Vrať strukturovaný JSON se skóre 0-100 a "
        "konkrétní zpětnou vazbou."
    ),
    model=MODEL_KVALITA,
)

REFINER = AgentDefinition(
    description="Opravář draftu memory souboru dle zpětné vazby",
    prompt=(
        "Jsi opravář memory souborů. Uprav draft přesně podle zpětné vazby "
        "hodnotitele, zachovej jádro sdělení a formát. "
        f"{_VRAT_SYROVE}"
    ),
    model=MODEL_KVALITA,
)


# --------------------------------------------------------------------------
# Agenti workflow `audit` — Supervisor + Parallel
# --------------------------------------------------------------------------

CURATOR_SUPERVISOR = AgentDefinition(
    description="Kurátor řídící audit hygieny knowledge base",
    prompt=(
        "Jsi kurátor knowledge base. Deleguj kontrolu hygieny na specialisty "
        "link-checker, staleness-detector a index-sync, každého použij právě "
        "jednou. Až máš výsledky všech tří, akcí 'finish' vrať souhrnný "
        "markdown report. Vrať jen strukturovaný JSON."
    ),
    model=MODEL_LEVNY,
)

LINK_CHECKER = AgentDefinition(
    description="Specialista na rozbité [[odkazy]] mezi memory soubory",
    prompt=(
        "Jsi kontrolor odkazů. Z dodaného digestu posuď nalezené rozbité "
        "[[odkazy]] — odkazy mířící na neexistující slug. Vrať stručný "
        "markdown seznam: zdrojový soubor a chybějící cíl."
    ),
    model=MODEL_LEVNY,
)

STALENESS_DETECTOR = AgentDefinition(
    description="Specialista na zastaralá fakta a prošlé podmínky",
    prompt=(
        "Jsi detektor zastaralosti. Z digestu posuď fakta s prošlým datem "
        "nebo splněnou podmínkou (např. 'odložit do…', absolutní data v "
        "minulosti). Vrať stručný markdown seznam zastaralých záznamů."
    ),
    model=MODEL_LEVNY,
)

INDEX_SYNC = AgentDefinition(
    description="Specialista na synchronizaci MEMORY.md indexu",
    prompt=(
        "Jsi kontrolor indexu. Z digestu posuď memory soubory chybějící v "
        "MEMORY.md a osiřelé řádky indexu bez souboru. Vrať stručný "
        "markdown seznam obou kategorií."
    ),
    model=MODEL_LEVNY,
)
