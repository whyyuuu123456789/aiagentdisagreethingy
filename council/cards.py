"""A2A AgentCards for the council agents, in the same shape as Lungo's farm cards.

Each stakeholder advertises a `deliberate` skill plus a capability tag. A coffee exchange
can discover them through the AGNTCY directory the way Lungo discovers farms, and they
share one SLIM/NATS group topic for the deliberation.

    python -m council.cards           # writes cards/*.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .agents import lungo_council
from .bus import TOPIC

SLIM_SERVER = os.getenv("SLIM_SERVER", "slim:46357")
NATS_SERVER = os.getenv("NATS_SERVER", "nats:4222")

DESCRIPTIONS = {
    "cost": "Argues for the lowest landed price per lb; vetoes plans over its budget ceiling.",
    "quality": "Virtual sommelier; argues for cup score and vetoes plans below the brand's quality floor.",
    "green": "Argues for low carbon and certified sourcing; vetoes plans under the certified-share minimum.",
    "risk": "Argues for on-time delivery and origin diversification; vetoes over-concentrated plans.",
}


def card(agent) -> dict:
    agent_id = f"council_{agent.id}"
    return {
        "name": f"Council {agent.title}",
        "description": DESCRIPTIONS[agent.id],
        "version": "1.0.0",
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json", "text"],
        "capabilities": {"streaming": False},
        "skills": [
            {
                "id": "deliberate",
                "name": "Deliberate on a procurement plan",
                "description": "Propose plans, vote APPROVE/REJECT/VETO, and commit on the shared council topic.",
                "tags": ["coffee", "procurement", "negotiation", f"objective:{agent.role}"],
                "examples": ["Split a 1000 lb green coffee order across available farms."],
            }
        ],
        "supportsAuthenticatedExtendedCard": False,
        "preferred_transport": "slim",
        "url": f"slim://{SLIM_SERVER}/lungo/agents/{agent_id}",
        "additional_interfaces": [
            {"transport": "slim", "url": f"slim://{SLIM_SERVER}/lungo/agents/{agent_id}"},
            {"transport": "nats", "url": f"nats://{NATS_SERVER}/{TOPIC}"},
        ],
    }


def main() -> None:
    out = Path(__file__).resolve().parent.parent / "cards"
    out.mkdir(exist_ok=True)
    for a in lungo_council():
        path = out / f"council_{a.id}.json"
        path.write_text(json.dumps(card(a), indent=2) + "\n")
        print(path)


if __name__ == "__main__":
    main()
