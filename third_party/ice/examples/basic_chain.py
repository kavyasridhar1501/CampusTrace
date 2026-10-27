#!/usr/bin/env python3
"""Minimal demonstration of ICE V.01 receipt creation and chain verification."""

from ice import Ledger
import json


def main() -> None:
    ledger = Ledger()

    # First observation
    r1 = ledger.append(
        agent_id="did:example:agent-alpha",
        agent_type="research-agent",
        target_url="https://example.com/llms.txt",
        surface="llms.txt",
        action="GET",
        result_summary="Retrieved curated site map containing 12 primary links.",
    )
    print("Receipt 1 created:")
    print(json.dumps(r1.to_dict(), indent=2))
    print()

    # Second observation (chained)
    r2 = ledger.append(
        agent_id="did:example:agent-alpha",
        agent_type="research-agent",
        target_url="https://example.com/.well-known/agent.json",
        surface="agent.json",
        action="GET",
        result_summary="Discovered MCP endpoint and two declared tools.",
    )
    print("Receipt 2 created (chained to previous):")
    print(json.dumps(r2.to_dict(), indent=2))
    print()

    # Verify integrity
    print(f"Chain length : {len(ledger)}")
    print(f"Tip hash     : {ledger.tip_hash}")
    print(f"Chain valid  : {ledger.verify_chain()}")


if __name__ == "__main__":
    main()
