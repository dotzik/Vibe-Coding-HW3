"""Deterministická I/O vrstva nad knowledge base.

Tento modul je čistý Python — žádné volání agentů, žádné API. Veškerá
mechanická práce (procházení stromu, parsování frontmatteru, extrakce
``[[odkazů]]``, čtení indexu, zápis souborů) se dělá zde, aby agenti
dostávali jen kompaktní pre-digest a nikdy raw dumpy souborů. To je
klíčové pro tokenovou efektivitu — levný kód místo drahého modelu.

Konvence KB:
- ``one fact = one file`` — každý memory soubor je jeden ``*.md``
- YAML frontmatter ohraničený ``---`` na začátku souboru
- ``[[slug]]`` odkazy v těle míří na jiné memory soubory dle jejich slugu
- ``MEMORY.md`` v kořeni je ručně udržovaný index všech souborů
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

# Regulární výraz pro [[odkazy]] — slug uvnitř dvojitých hranatých závorek.
_LINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")

# Indexový řádek v MEMORY.md ve tvaru: - [titulek](relativní/cesta.md)
_INDEX_LINE_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")

# Absolutní datum ISO ve frontmatteru nebo v textu (YYYY-MM-DD).
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Soubory, které nejsou memory záznamy a do skenu nepatří.
_IGNORED_NAMES = {"MEMORY.md", "README.md"}


@dataclass
class MemoryFile:
    """Jeden memory soubor po deterministickém zpracování.

    Drží jen kompaktní metadata — nikoli plné tělo. Agentům se posílá
    pře-strávená podoba (viz :func:`digest_for_audit`), ne tento objekt.
    """

    path: Path                       # absolutní cesta k souboru
    slug: str                        # název souboru bez přípony .md
    rel_path: str                    # cesta relativní ke kořeni KB (s '/')
    frontmatter: dict[str, str]      # naparsovaný YAML-lite frontmatter
    title: str                       # titulek (z frontmatteru nebo z H1)
    links: list[str] = field(default_factory=list)   # cílové slugy [[odkazů]]
    iso_dates: list[str] = field(default_factory=list)  # nalezená ISO data
    body_preview: str = ""           # prvních pár řádků těla pro kontext


@dataclass
class KnowledgeBase:
    """Celá knowledge base po deterministickém načtení.

    Tohle je jediný objekt, ze kterého workflow čerpá. Agenti dostanou
    jen výřezy (digesty), nikdy celou strukturu.
    """

    root: Path
    files: list[MemoryFile]
    index_entries: list[str]         # rel_path cesty vypsané v MEMORY.md
    subtrees: dict[str, list[MemoryFile]]  # soubory seskupené dle podsložky

    @property
    def slugs(self) -> set[str]:
        """Množina všech existujících slugů — pro kontrolu odkazů."""
        return {f.slug for f in self.files}


# --------------------------------------------------------------------------
# Parsování
# --------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Rozdělí obsah souboru na frontmatter a tělo.

    Frontmatter je blok mezi prvním párem ``---`` na začátku souboru.
    Parser je záměrně minimalistický (YAML-lite): jen ``klíč: hodnota``
    na řádek, což pokrývá konvenci „one fact = one file" a nepřináší
    závislost na plné YAML knihovně.

    Vrací dvojici ``(frontmatter_dict, telo)``. Když frontmatter chybí,
    vrací prázdný slovník a celý text jako tělo.
    """
    if not text.startswith("---"):
        return {}, text

    # Najdi uzavírací '---' (musí být na samostatném řádku).
    lines = text.splitlines()
    konec = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            konec = i
            break
    if konec is None:
        return {}, text

    frontmatter: dict[str, str] = {}
    for radek in lines[1:konec]:
        if not radek.strip() or radek.lstrip().startswith("#"):
            continue
        if ":" not in radek:
            continue
        klic, _, hodnota = radek.partition(":")
        frontmatter[klic.strip()] = hodnota.strip().strip("\"'")

    telo = "\n".join(lines[konec + 1:]).lstrip("\n")
    return frontmatter, telo


def serialize_frontmatter(frontmatter: dict[str, str], telo: str) -> str:
    """Sestaví obsah memory souboru z frontmatter slovníku a těla.

    Inverze k :func:`parse_frontmatter` — z naparsovaného frontmatteru
    a těla deterministicky složí zpět validní memory soubor. Pořadí
    klíčů respektuje pořadí ve vstupním slovníku (Python 3.7+ dict je
    uspořádaný), takže volající řídí pořadí přípravou slovníku.

    Hodnoty se nezapouzdřují do uvozovek — konvence KB drží prosté
    ``klíč: hodnota`` páry, stejně jako je čte ``parse_frontmatter``.
    """
    radky = ["---"]
    for klic, hodnota in frontmatter.items():
        radky.append(f"{klic}: {hodnota}")
    radky.append("---")
    return "\n".join(radky) + "\n\n" + telo.lstrip("\n")


def extract_links(text: str) -> list[str]:
    """Vytáhne všechny cílové slugy ``[[odkazů]]`` z textu.

    Vrací slugy bez duplicit, v pořadí prvního výskytu. Whitespace
    kolem slugu se ořezává.
    """
    videno: list[str] = []
    for shoda in _LINK_RE.findall(text):
        slug = shoda.strip()
        if slug and slug not in videno:
            videno.append(slug)
    return videno


def extract_iso_dates(text: str) -> list[str]:
    """Vrátí všechna absolutní ISO data (YYYY-MM-DD) nalezená v textu."""
    videno: list[str] = []
    for shoda in _ISO_DATE_RE.findall(text):
        if shoda not in videno:
            videno.append(shoda)
    return videno


def _extract_title(frontmatter: dict[str, str], telo: str, slug: str) -> str:
    """Určí titulek souboru: frontmatter ``title`` → první H1 → slug."""
    if frontmatter.get("title"):
        return frontmatter["title"]
    for radek in telo.splitlines():
        if radek.startswith("# "):
            return radek[2:].strip()
    return slug


def _body_preview(telo: str, max_radku: int = 3) -> str:
    """Vrátí prvních pár neprázdných řádků těla — kompaktní náhled."""
    radky = [r.strip() for r in telo.splitlines() if r.strip()]
    radky = [r for r in radky if not r.startswith("#")]
    return " ".join(radky[:max_radku])[:240]


# --------------------------------------------------------------------------
# Čtení KB
# --------------------------------------------------------------------------

def read_memory_file(path: Path, root: Path) -> MemoryFile:
    """Načte a deterministicky zpracuje jeden memory soubor."""
    text = path.read_text(encoding="utf-8")
    frontmatter, telo = parse_frontmatter(text)
    slug = path.stem
    # Odkazy a data se hledají v celém souboru (frontmatter i tělo).
    links = extract_links(text)
    iso_dates = extract_iso_dates(text)
    rel_path = path.relative_to(root).as_posix()
    return MemoryFile(
        path=path,
        slug=slug,
        rel_path=rel_path,
        frontmatter=frontmatter,
        title=_extract_title(frontmatter, telo, slug),
        links=links,
        iso_dates=iso_dates,
        body_preview=_body_preview(telo),
    )


def read_index(root: Path) -> list[str]:
    """Načte cesty vypsané v ``MEMORY.md`` indexu.

    Vrací seznam rel_path cest (POSIX tvar) tak, jak na ně odkazují
    markdown odkazy v indexu. Když ``MEMORY.md`` chybí, vrací prázdný
    seznam.
    """
    index_path = root / "MEMORY.md"
    if not index_path.exists():
        return []
    text = index_path.read_text(encoding="utf-8")
    cesty: list[str] = []
    for shoda in _INDEX_LINE_RE.findall(text):
        # Normalizuj na POSIX a zahoď případné './' prefixy.
        norm = shoda.replace("\\", "/").lstrip("./")
        if norm not in cesty:
            cesty.append(norm)
    return cesty


def read_tree(root: str | Path) -> KnowledgeBase:
    """Načte celou knowledge base z kořenového adresáře.

    Projde rekurzivně všechny ``*.md`` soubory (kromě ``MEMORY.md`` a
    ``README.md``), zpracuje je a seskupí podle podsložky prvního řádu.
    Seskupení podle podstromů slouží jako jednotky pro paralelní sken.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Kořen KB neexistuje nebo není adresář: {root}")

    files: list[MemoryFile] = []
    for path in sorted(root.rglob("*.md")):
        if path.name in _IGNORED_NAMES:
            continue
        files.append(read_memory_file(path, root))

    # Seskupení dle podsložky prvního řádu (soubory v kořeni → '.').
    subtrees: dict[str, list[MemoryFile]] = {}
    for f in files:
        casti = f.rel_path.split("/")
        podstrom = casti[0] if len(casti) > 1 else "."
        subtrees.setdefault(podstrom, []).append(f)

    return KnowledgeBase(
        root=root,
        files=files,
        index_entries=read_index(root),
        subtrees=subtrees,
    )


# --------------------------------------------------------------------------
# Detekce defektů — čistá deterministika (žádný agent)
# --------------------------------------------------------------------------

def find_broken_links(kb: KnowledgeBase) -> list[dict[str, str]]:
    """Najde ``[[odkazy]]``, které nemíří na žádný existující slug.

    Vrací seznam záznamů ``{"source": rel_path, "target": slug}``.
    """
    existujici = kb.slugs
    rozbite: list[dict[str, str]] = []
    for f in kb.files:
        for cil in f.links:
            if cil not in existujici:
                rozbite.append({"source": f.rel_path, "target": cil})
    return rozbite


def find_stale_files(kb: KnowledgeBase, dnes: date | None = None) -> list[dict[str, str]]:
    """Najde soubory s prošlým datem ve frontmatteru.

    Heuristika: pokud frontmatter obsahuje klíč ``expires`` / ``review_by``
    / ``valid_until`` / ``defer_until`` s ISO datem v minulosti, soubor
    je považován za zastaralý. Agent (``staleness_detector``) pak posuzuje
    jemnější textové podmínky — tahle funkce dělá levný first-pass.
    """
    dnes = dnes or date.today()
    klice_data = ("expires", "review_by", "valid_until", "defer_until", "deadline")
    stale: list[dict[str, str]] = []
    for f in kb.files:
        for klic in klice_data:
            hodnota = f.frontmatter.get(klic)
            if not hodnota:
                continue
            shoda = _ISO_DATE_RE.search(hodnota)
            if not shoda:
                continue
            try:
                kdy = datetime.strptime(shoda.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if kdy < dnes:
                stale.append({
                    "source": f.rel_path,
                    "field": klic,
                    "date": shoda.group(1),
                })
    return stale


def find_index_gaps(kb: KnowledgeBase) -> dict[str, list[str]]:
    """Porovná soubory na disku s indexem v ``MEMORY.md``.

    Vrací slovník se dvěma seznamy:
    - ``missing`` — soubory na disku, které v indexu chybí
    - ``orphaned`` — řádky v indexu, ke kterým neexistuje soubor
    """
    na_disku = {f.rel_path for f in kb.files}
    v_indexu = set(kb.index_entries)
    return {
        "missing": sorted(na_disku - v_indexu),
        "orphaned": sorted(v_indexu - na_disku),
    }


# --------------------------------------------------------------------------
# Pre-digesty pro agenty — kompaktní vstupy místo raw dumpů
# --------------------------------------------------------------------------

def digest_for_audit(files: list[MemoryFile]) -> str:
    """Sestaví kompaktní textový digest seznamu souborů pro agenta.

    Jeden řádek na soubor: slug, cesta, odkazy a datová pole. Žádné
    plné tělo — agent dostane jen to, co potřebuje k posouzení.
    """
    radky: list[str] = []
    for f in files:
        casti = [f"- {f.rel_path}"]
        if f.links:
            casti.append(f"odkazy=[{', '.join(f.links)}]")
        if f.iso_dates:
            casti.append(f"data=[{', '.join(f.iso_dates)}]")
        if f.body_preview:
            casti.append(f'náhled="{f.body_preview[:120]}"')
        radky.append(" | ".join(casti))
    return "\n".join(radky) if radky else "(žádné soubory)"


def digest_index(kb: KnowledgeBase) -> str:
    """Kompaktní digest stavu indexu vůči disku pro agenta."""
    gaps = find_index_gaps(kb)
    return (
        f"Souborů na disku: {len(kb.files)}\n"
        f"Řádků v MEMORY.md: {len(kb.index_entries)}\n"
        f"Chybí v indexu: {gaps['missing'] or '(žádné)'}\n"
        f"Osiřelé řádky: {gaps['orphaned'] or '(žádné)'}"
    )


# --------------------------------------------------------------------------
# Zápis
# --------------------------------------------------------------------------

def slugify(text: str, max_delka: int = 48) -> str:
    """Převede text na slug vhodný jako název souboru.

    Diakritika se zjednodušuje, mezery na pomlčky, nepovolené znaky se
    zahodí. Výsledek je lowercase ASCII oddělený pomlčkami.
    """
    prevod = str.maketrans(
        "áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ",
        "acdeeinorstuuyzacdeeinorstuuyz",
    )
    text = text.translate(prevod).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:max_delka].strip("-") or "memory"


def write_memory(root: str | Path, rel_path: str, obsah: str) -> Path:
    """Zapíše nový memory soubor do KB a vrátí jeho absolutní cestu.

    Nadřazené adresáře se vytvoří podle potřeby. Funkce odmítne přepsat
    existující soubor — kurátor nikdy tiše nemaže fakta.
    """
    cesta = Path(root).resolve() / rel_path
    if cesta.exists():
        raise FileExistsError(f"Memory soubor už existuje: {cesta}")
    cesta.parent.mkdir(parents=True, exist_ok=True)
    cesta.write_text(obsah.rstrip() + "\n", encoding="utf-8")
    return cesta


def append_index_line(root: str | Path, radek: str) -> None:
    """Připojí řádek do ``MEMORY.md`` indexu.

    Když ``MEMORY.md`` neexistuje, založí ho s minimální hlavičkou.
    Řádek se přidává na konec souboru.
    """
    index_path = Path(root).resolve() / "MEMORY.md"
    if not index_path.exists():
        index_path.write_text("# MEMORY — index\n\n", encoding="utf-8")
    soucasny = index_path.read_text(encoding="utf-8").rstrip()
    index_path.write_text(soucasny + "\n" + radek.rstrip() + "\n", encoding="utf-8")
