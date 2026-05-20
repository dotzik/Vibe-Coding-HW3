"""Společný běhový helper pro agenty a konstanty modelů.

Sjednocuje volání Claude Agent SDK do jediné funkce :func:`run_agent`,
aby workflow moduly neopakovaly boilerplate okolo ``ClaudeSDKClient``.
Vzor převzat z kurzových příkladů (``3_workflows/*``).
"""

from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)

# Modely podle požadavku na tokenovou efektivitu:
# levný haiku jako default, sonnet jen kde je potřeba kvalita generování.
MODEL_LEVNY = "haiku"      # classifier, supervisor, audit specialisté
MODEL_KVALITA = "sonnet"   # draft-handlery, refiner, qa_evaluator


async def run_agent(
    name: str,
    system_prompt: str,
    prompt: str,
    model: str = MODEL_LEVNY,
    output_format: dict | None = None,
    tichy: bool = False,
    allowed_tools: list[str] | None = None,
) -> tuple[str, dict | None]:
    """Spustí jednoho agenta ve vlastní izolované session.

    Každé volání má čerstvý kontext — čisté hranice mezi rolemi dle
    vzoru kurzu. Volitelný ``output_format`` zapne strukturovaný JSON
    výstup (používá classifier a supervisor).

    Args:
        name: Zobrazované jméno agenta (do logu).
        system_prompt: System prompt definující roli agenta (2-4 věty).
        prompt: Vlastní zadání úkolu.
        model: ``MODEL_LEVNY`` nebo ``MODEL_KVALITA``.
        output_format: Volitelné JSON schéma pro strukturovaný výstup.
        tichy: Když True, neproudí text agenta na stdout.
        allowed_tools: Seznam povolených nástrojů. ``[]`` agenta plně
            odřízne od nástrojů (čistě textový výstup) — draft-handlery
            a refiner běží takto, zápis dělá výhradně ``kb.py``.

    Returns:
        Dvojici ``(text_odpovedi, strukturovany_vystup_nebo_None)``.
    """
    options_kwargs: dict = {
        "model": model,
        "system_prompt": system_prompt,
    }
    if allowed_tools is not None:
        options_kwargs["allowed_tools"] = allowed_tools
    if output_format is not None:
        options_kwargs["output_format"] = {
            "type": "json_schema",
            "schema": output_format,
        }
    options = ClaudeAgentOptions(**options_kwargs)

    text = ""
    strukturovany: dict | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                        if not tichy:
                            print(block.text, end="", flush=True)
            elif isinstance(msg, ResultMessage):
                if getattr(msg, "structured_output", None):
                    strukturovany = msg.structured_output
                if msg.total_cost_usd and msg.total_cost_usd > 0:
                    print(f"\n  [náklady {name}: ${msg.total_cost_usd:.4f}]")

    return text, strukturovany
