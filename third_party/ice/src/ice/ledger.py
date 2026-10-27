"""Simple in-memory / list-based hash-chained ledger for ICE V.01."""

from __future__ import annotations

from typing import List, Optional

from .receipt import InferenceReceipt, create_receipt


class Ledger:
    """
    Ordered, hash-chained collection of Inference Receipts.

    V.01 keeps everything in memory. Persistence and external anchoring
    are intentionally deferred to later versions.
    """

    def __init__(self) -> None:
        self._receipts: List[InferenceReceipt] = []

    def __len__(self) -> int:
        return len(self._receipts)

    @property
    def tip_hash(self) -> str:
        if not self._receipts:
            return "genesis"
        return self._receipts[-1].content_hash

    def append(
        self,
        *,
        agent_id: str,
        agent_type: str = "llm-agent",
        target_url: str,
        surface: Optional[str] = None,
        action: str,
        result_summary: str,
        result_payload: Optional[object] = None,
        signer_id: str = "local-dev",
    ) -> InferenceReceipt:
        """Create a new receipt chained to the current tip and append it."""
        receipt = create_receipt(
            agent_id=agent_id,
            agent_type=agent_type,
            target_url=target_url,
            surface=surface,
            action=action,
            result_summary=result_summary,
            previous_hash=self.tip_hash,
            result_payload=result_payload,
            signer_id=signer_id,
        )
        self._receipts.append(receipt)
        return receipt

    def verify_chain(self) -> bool:
        """Return True if every receipt correctly chains to its predecessor."""
        if not self._receipts:
            return True

        expected_prev = "genesis"
        for r in self._receipts:
            if r.previous_hash != expected_prev:
                return False
            # Recompute content hash and ensure it matches the stored one
            recomputed = r.compute_content_hash()
            if recomputed != r.content_hash:
                return False
            expected_prev = r.content_hash
        return True

    def to_list(self) -> list[dict]:
        return [r.to_dict() for r in self._receipts]
