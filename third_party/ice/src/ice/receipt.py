"""Inference Receipt data model and hashing utilities for ICE V.01."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


def _canonical_json(obj: dict) -> str:
    """Produce a deterministic JSON string for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_receipt(body: dict) -> str:
    """Compute SHA-256 of the canonicalized receipt body (excluding signature)."""
    # Ensure signature is not included in the hash
    body_for_hash = {k: v for k, v in body.items() if k != "signature"}
    canonical = _canonical_json(body_for_hash)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class InferenceReceipt:
    """Minimum viable Inference Receipt (ICE V.01)."""

    version: str = "0.1"
    receipt_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    agent: dict[str, Any] = field(default_factory=dict)
    target: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    result_summary: str = ""
    result_hash: Optional[str] = None
    previous_hash: str = "genesis"
    content_hash: str = ""
    signature: str = ""
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Remove empty optional fields for cleaner output if desired
        if not d.get("result_hash"):
            d.pop("result_hash", None)
        if not d.get("extensions"):
            d.pop("extensions", None)
        return d

    def compute_content_hash(self) -> str:
        body = self.to_dict()
        body.pop("content_hash", None)
        body.pop("signature", None)
        return hash_receipt(body)

    def sign_placeholder(self, signer_id: str = "local-dev") -> None:
        """
        V.01 placeholder signature.
        In production this would be a real cryptographic signature over content_hash.
        For now we simply bind the signer identity and the content hash.
        """
        self.content_hash = self.compute_content_hash()
        payload = f"{signer_id}:{self.content_hash}"
        self.signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_receipt(
    *,
    agent_id: str,
    agent_type: str = "llm-agent",
    target_url: str,
    surface: Optional[str] = None,
    action: str,
    result_summary: str,
    previous_hash: str = "genesis",
    result_payload: Optional[Any] = None,
    signer_id: str = "local-dev",
) -> InferenceReceipt:
    """Factory helper that produces a fully formed, hashed, and placeholder-signed receipt."""
    target: dict[str, Any] = {"url": target_url}
    if surface:
        target["surface"] = surface

    receipt = InferenceReceipt(
        agent={"id": agent_id, "type": agent_type},
        target=target,
        action=action,
        result_summary=result_summary,
        previous_hash=previous_hash,
    )

    if result_payload is not None:
        raw = json.dumps(result_payload, sort_keys=True, separators=(",", ":"))
        receipt.result_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    receipt.sign_placeholder(signer_id=signer_id)
    return receipt
