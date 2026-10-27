"""ICE V.01 — Immutable Chain Evidence

Minimal reference implementation for Inference Receipts and local hash-chained ledgers.
"""

from .receipt import InferenceReceipt, create_receipt, hash_receipt
from .ledger import Ledger

__version__ = "0.1.0"
__all__ = ["InferenceReceipt", "create_receipt", "hash_receipt", "Ledger"]
