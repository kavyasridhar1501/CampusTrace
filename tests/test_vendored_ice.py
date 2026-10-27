"""ICE V.01 is vendored unmodified and importable from the vendored copy."""

import hashlib
from pathlib import Path

import ice


def _vendored_files(root: Path) -> set[str]:
    return {
        str(p.relative_to(root.parent))
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and not any(part.endswith(".egg-info") for part in p.parts)
    }


def test_vendored_files_unmodified(repo: Path) -> None:
    third_party = repo / "third_party"
    manifest = {}
    for line in (third_party / "ice.sha256").read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        manifest[path] = digest

    assert _vendored_files(third_party / "ice") == set(manifest)
    for path, digest in manifest.items():
        actual = hashlib.sha256((third_party / path).read_bytes()).hexdigest()
        assert actual == digest, f"{path} differs from the pinned ICE commit"


def test_license_kept(repo: Path) -> None:
    assert "MIT License" in (repo / "third_party" / "ice" / "LICENSE").read_text()


def test_imports_from_vendored_copy(repo: Path) -> None:
    assert Path(ice.__file__).resolve().is_relative_to(repo / "third_party" / "ice")


def test_v01_chain_round_trip() -> None:
    ledger = ice.Ledger()
    first = ledger.append(
        agent_id="campustrace",
        target_url="urn:campustrace:ticket:1",
        action="triage",
        result_summary="P3 hvac",
    )
    ledger.append(
        agent_id="campustrace",
        target_url="urn:campustrace:ticket:1",
        action="schedule",
        result_summary="tech-01 at 09:00",
    )
    assert first.previous_hash == "genesis"
    assert ledger.verify_chain()
