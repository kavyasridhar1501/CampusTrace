import pytest

from agent.config import HAIKU, SONNET, Settings


def test_model_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "INTAKE_MODEL",
        "ROOTCAUSE_MODEL",
        "TRIAGE_MODEL",
        "DEDUP_MODEL",
        "BRIEFING_MODEL",
        "FOLLOWUP_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert SONNET == "claude-sonnet-5-5"
    assert HAIKU == "claude-haiku-5-5"
    assert (s.intake_model, s.rootcause_model, s.triage_model) == (SONNET, SONNET, SONNET)
    assert (s.dedup_model, s.briefing_model, s.followup_model) == (HAIKU, HAIKU, HAIKU)


def test_models_come_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRIAGE_MODEL", "claude-opus-5-5")
    assert Settings(_env_file=None).triage_model == "claude-opus-5-5"  # type: ignore[call-arg]


def test_secrets_have_no_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "SOCRATA_APP_TOKEN", "ICE_SIGNING_KEY"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.anthropic_api_key is None
    assert s.voyage_api_key is None
    assert s.socrata_app_token is None
    assert s.ice_signing_key is None


def test_env_example_has_placeholders_only(repo) -> None:  # type: ignore[no-untyped-def]
    lines = (repo / ".env.example").read_text().splitlines()
    values = dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))
    for key in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY", "SOCRATA_APP_TOKEN", "ICE_SIGNING_KEY"):
        assert values[key] in ("", "replace-me"), key
