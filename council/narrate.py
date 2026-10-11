"""Optional LLM voice for agent messages, using litellm and LLM_MODEL like the rest of coffeeAGNTCY.

The LLM only rewrites the human-readable `text` of a message. Plans, votes and bars are
computed by the agents' own logic, so runs stay reproducible and auditable with or without it.
"""

from __future__ import annotations

import json
import os

PERSONAS = {
    "cost": "a blunt procurement lead who watches every cent",
    "quality": "a meticulous coffee sommelier protecting the brand's cup profile",
    "green": "a sustainability officer focused on certification and carbon",
    "risk": "a supply-chain risk manager worried about late or concentrated shipments",
}


def llm_narrator():
    import litellm  # installed with the Lungo/Corto environments

    model = os.environ.get("LLM_MODEL")
    if not model:
        raise SystemExit("Set LLM_MODEL (e.g. anthropic/claude-sonnet-5-5) to use --llm")

    def narrate(sender: str, kind: str, payload: dict, text: str) -> str:
        if sender not in PERSONAS:
            return text
        try:
            resp = litellm.completion(
                model=model,
                max_tokens=120,
                messages=[
                    {"role": "system", "content": f"You are {PERSONAS[sender]} in a coffee buying council. "
                     "Restate the structured message below in one or two sentences, first person, "
                     "keeping every number exactly. Do not change the decision."},
                    {"role": "user", "content": f"{kind}: {text}\nData: {json.dumps(payload, default=str)}"},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:  # narration is cosmetic; never break the deliberation
            return f"{text} (narration unavailable: {type(exc).__name__})"

    return narrate
