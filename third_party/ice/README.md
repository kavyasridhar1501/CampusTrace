# ICE V.01 — Immutable Chain Evidence

**Version 0.1 | First formal artifact**

ICE (Immutable Chain Evidence) provides a minimal, cryptographically grounded mechanism for recording what an autonomous agent actually observed and acted upon when interacting with an Agent Face.

This repository is the initial implementation and specification of the Inference Receipt (IR) format and the local hash-chained ledger that precedes any external anchoring.

## Core Pipeline

```
Human Face
    ↓
Agent Face
    ↓
Inference Receipt (IR)
    ↓
ICE Ledger (hash-chained, signed)
    ↓  (later)
External Anchor (blockchain / evidence network)
```

Humans consume narrative interfaces.  
Agents consume structured surfaces.  
ICE records the observation itself as permanent evidence.

## What V.01 Delivers

1. **Canonical Inference Receipt schema** (JSON Schema)
2. **Minimal Python reference implementation** for:
   - Creating a receipt
   - Hashing and chaining receipts
   - Verifying a chain
3. Clear separation between the receipt (evidence of one interaction) and the ledger (the ordered chain of receipts)

V.01 intentionally contains no blockchain integration, no network protocol, and no persistence beyond in-memory or simple file storage. Those layers belong to subsequent versions.

## Directory Layout

```
ice-v01/
├── README.md                 # This document
├── LICENSE                   # MIT
├── schemas/
│   └── inference-receipt.schema.json
├── src/
│   └── ice/
│       ├── __init__.py
│       ├── receipt.py         # Core data model + hashing
│       └── ledger.py          # Simple chain management
├── examples/
│   └── basic_chain.py         # Runnable demonstration
└── docs/
    └── design-notes.md         # Design rationale
```

## Quick Start (after cloning)

```bash
python -m venv .venv
source .venv/bin/activate   # or Windows equivalent
pip install -e .
python examples/basic_chain.py
```

## Relationship to Agent Faces

This project is a direct continuation of the Agent Faces field guide:

- Agent Faces defines the *surfaces* agents read.
- ICE defines the *evidence* of what was read and done.

See the companion repository: [agent-faces](https://github.com/Dhammerr6304/agent-faces)

## Status

**V.01 (2026-07-31)**  
Schema locked for the minimum viable receipt. Reference implementation complete and self-contained. Ready for local experimentation and feedback.

## License

MIT
