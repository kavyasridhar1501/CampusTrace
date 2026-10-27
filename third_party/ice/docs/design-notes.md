# Design Notes — ICE V.01

## Intentional Scope Limitations

V.01 deliberately excludes:

- Real cryptographic signatures (uses a deterministic placeholder)
- Persistence beyond process memory
- Network protocols or remote ledger services
- Blockchain or external anchoring
- Multi-party consensus or validation

These omissions keep the first artifact small, understandable, and focused on the core evidence model.

## Why a Receipt is Separate from a Log Entry

A conventional log is operator-controlled and mutable. An Inference Receipt is intended to be:

1. Self-describing
2. Hash-chained to prior observations
3. Signed by the observing agent (or its key)
4. Independently verifiable later

Even in V.01 the structure forces the discipline of producing evidence rather than merely logging.

## Future Evolution Path

- V.0.2: Replace placeholder signature with Ed25519 (or equivalent) and key management
- V.0.3: Simple file-based or SQLite persistence
- V.0.4: Export of chain roots / Merkle proofs for external anchoring
- Later: Integration with Agent Faces discovery surfaces and live MCP tooling

## Relationship to Agent Faces

The Agent Faces taxonomy defines *what* can be observed.  
ICE defines *how* the observation itself becomes durable evidence.

Both projects share the same architectural thesis: the web is bifurcating into human-facing and agent-facing surfaces, and the agent side requires its own evidentiary substrate.
