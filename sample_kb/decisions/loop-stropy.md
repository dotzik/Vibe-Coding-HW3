---
title: Stropy iterací u QA-loopu
type: decision
created: 2026-02-15
defer_until: 2026-04-30
tags: agents, quality, loop
---

# Stropy iterací u QA-loopu

**Kontext:** QA-loop opakuje hodnocení a opravu draftu. Bez stropu hrozí,
že se u těžko hodnotitelného obsahu zacyklí a spálí tokeny.

**Rozhodnutí:** QA-loop má práh kvality 85 a maximálně 3 iterace.
Dočasně bylo nasazeno volnější nastavení (5 iterací) na ověření do konce
dubna — po `defer_until` se má vrátit na 3 iterace a tento záznam
aktualizovat.
