---
title: Paralelní sken podstromů zrychluje audit
type: learning
created: 2026-03-11
tags: performance, audit, parallel
---

# Paralelní sken podstromů zrychluje audit

**Poznatek:** Při auditu velké knowledge base se vyplatí skenovat
jednotlivé podstromy souběžně místo sekvenčně. Deterministická kontrola
odkazů nemá sdílený stav, takže fan-out přes task group škáluje lineárně
s počtem podstromů a znatelně zkracuje dobu běhu.
