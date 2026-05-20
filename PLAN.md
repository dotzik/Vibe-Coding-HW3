# Plán — Hub Curator (HW3)

## Kontext

Domácí úkol: projekt s praktickým použitím SDK pro kódovacího agenta,
demonstrující orchestraci. Řešení = **Hub Curator** — CLI na Claude Agent SDK
(Python), automatizuje práci s markdown auto-memory KB. Dva podpříkazy, jejichž
podúkoly mapují na všech 5 patternů ze zadání; každý je malý → celek je MVP
postavitelný do deadline (zítra 2026-05-21).

Architektura: viz `DESIGN.md`.

Potvrzeno uživatelem: projekt = Hub Curator, rozsah = Focused MVP,
SDK = Python. Realizace: agent **Hallux**.

GitHub remote: `https://github.com/dotzik/Vibe-Coding-HW3.git`

## Pokrytí zadání

| Pattern | Kde |
|---|---|
| Sequential | `add`: classify → draft → QA-loop → write |
| Parallel | `audit`: fan-out paralelní sken stromů KB (`anyio.create_task_group()`) |
| Conditional | `add`: classifier routuje na draft-handler dle typu memory |
| Loop | `add`: draft → QA evaluator → refiner, dokud skóre ≥ 85 / max 3 iter |
| Supervisor | `audit`: kurátor deleguje strukturovaným JSON na 3 specialisty |

## Jazyk

Vše **česky**: komentáře, docstringy, system prompty, prompty, CLI nápověda,
výstupy, README, `sample_kb/`. Anglicky jen identifikátory a JSON klíče.

## Token efektivita (klíčový požadavek)

- **Deterministiku dělá kód, ne agent.** `kb.py` (čistý Python) extrahuje
  `[[odkazy]]`, data, indexové řádky. Agenti dostávají kompaktní pre-digest,
  nikdy raw dumpy souborů.
- **Levné modely.** Default `haiku` (classifier, supervisor, specialisté).
  `sonnet` jen kde je potřeba kvalita: draft-handlery, `refiner`, `qa_evaluator`.
- **Nízké stropy.** QA-loop max 3 iterace, supervisor max 6.
- **Stručné prompty.** System prompty 2–4 věty, žádná omáčka.

## Referenční vzory (kurz, nevymýšlet znovu)

`D:\Work\_MY\Courses\Vibe-Coding-1\5_Claude_Agent_SDK\python\`:
- `2_multi_agent\2_supervisor_pattern.py` — `SupervisorTeam`, `SUPERVISOR_SCHEMA`,
  `output_format` JSON schema, control loop.
- `3_workflows\4_loop_workflow.py` — `run_agent()` helper, evaluator + while-loop.
- `3_workflows\2_parallel_workflow.py` — `anyio.create_task_group()` fan-out.
- `3_workflows\3_conditional_workflow.py` — classifier → routing.

## Struktura (vše nové)

```
Vibe-Coding-HW3/
├── README.md            # start, usage, mapa na zadání, ukázkový výstup
├── DESIGN.md  PLAN.md
├── pyproject.toml       # deps: claude-agent-sdk>=0.1.53, anyio>=4.0.0
├── .env.example  .gitignore
├── src/hub_curator/
│   ├── __main__.py      # CLI argparse → add | audit
│   ├── common.py        # run_agent() helper, model konstanty
│   ├── agents.py        # AgentDefinition specifikace
│   ├── kb.py            # deterministická I/O vrstva nad KB
│   ├── add_memory.py    # Conditional + Sequential + Loop
│   └── audit.py         # Supervisor + Parallel
└── sample_kb/           # demo KB — NDA-safe, s úmyslnými defekty
```

## Kroky

1. **Scaffold** — `pyproject.toml` (src layout, console-script `curate`),
   `.env.example`, `.gitignore`, prázdné moduly.
2. **`kb.py`** — `read_tree`, `parse_frontmatter`, `extract_links` (regex),
   `read_index`/`index_entries`, `write_memory`, `append_index_line`. Bez agentů.
3. **`common.py`** — `run_agent(name, system_prompt, prompt, model, output_format)`
   dle vzorů; `MODEL_LEVNY="haiku"`, `MODEL_KVALITA="sonnet"`.
4. **`agents.py`** — `AgentDefinition`: `classifier`, draft-handlery
   (`feedback`/`project`/`reference`/`decision`), `qa_evaluator`, `refiner`,
   `curator_supervisor`, `link_checker`, `staleness_detector`, `index_sync`.
5. **`add_memory.py`** — `run_add(note, root, dry_run)`: Conditional (classifier
   → typ) → Sequential (draft → QA-loop → zápis) → Loop (draft→QA→refiner,
   skóre ≥ 85 / max 3). `--dry-run` jen tiskne.
6. **`audit.py`** — `run_audit(root)`: Supervisor (`SUPERVISOR_SCHEMA`
   delegate/finish) deleguje na 3 specialisty; uvnitř `link_checker` Parallel
   fan-out přes `anyio`. `kb.py` dodá pre-digest. Read-only, vrací md report.
7. **`__main__.py`** — `curate add "<note>" [--root] [--dry-run]`,
   `curate audit [--root]`. Default `--root` = přibalená `sample_kb/`.
8. **`sample_kb/`** — 5–6 realistických memory souborů vč. 3 úmyslných defektů:
   rozbitý `[[odkaz]]`, prošlé datum, soubor chybějící v indexu. Generický obsah.
9. **`README.md`** — instalace (`uv venv`/`uv sync`), `.env`, usage, mapa na
   zadání, ukázkový výstup.

## Realizace

Implementuje agent **Hallux** ve scope této složky.

## Verifikace

Předpoklad: `ANTHROPIC_API_KEY` v `.env`, `uv sync` proběhlo.

1. `curate audit` proti `sample_kb/` → report nahlásí všechny 3 defekty;
   v logu vidět supervisor delegace + paralelní fan-out.
2. `curate add "..." --dry-run` → classifier zvolí typ, QA-loop doběhne,
   vytiskne validní memory soubor + indexový řádek.
3. `curate add "..."` bez dry-run → soubor reálně vznikne + řádek v `MEMORY.md`.
4. README ukazuje očekávaný výstup; oba příkazy běží bez chyb.

Závěr: `git init`, commit, push na `github.com/dotzik/Vibe-Coding-HW3.git`.
