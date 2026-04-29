import re

import pytest

from agent.routing import build_routing_metadata, route_prompt


def test_route_prompt_returns_advisory_envelope_with_stable_metadata():
    envelope = route_prompt("Summarize this short note.", source_platform="cli")

    assert envelope["schema"] == "hermes.routing.v1"
    assert envelope["schema_version"] == "1.0.0"
    assert envelope["mode"] == "advisory"
    assert envelope["outcome"] is None
    assert envelope["source"] == "hermes-router:1.0.0"
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", envelope["prompt_hash"])
    assert envelope["recommendation"] == "main_context"
    assert envelope["model_tier"] == "fast"
    assert envelope["reasoning_tier"] == "none"
    assert envelope["allow_fallback"] is True


def test_route_prompt_accepts_empty_or_none_prompt():
    empty = route_prompt("", source_platform="cli")
    none_prompt = route_prompt(None, source_platform="cli")

    assert empty["prompt_hash"] == none_prompt["prompt_hash"]
    assert empty["recommendation"] == "main_context"
    assert none_prompt["recommendation"] == "main_context"


def test_delegate_research_prompt_recommends_delegation_without_binding_provider():
    envelope = route_prompt(
        "Research Reddit, GitHub, and docs in parallel, then synthesize the findings.",
        source_platform="cli",
    )

    assert envelope["recommendation"] == "delegate"
    assert envelope["role"] == "researcher"
    assert envelope["model_tier"] in {"balanced", "deep"}
    assert envelope["delegation_score"] > 0
    assert envelope["delegation_forbidden"] == []
    assert "delegation" in envelope["suggested_toolsets"]
    assert envelope["delegate_target_hint"] == "researcher"
    assert "provider" not in envelope
    assert "model" not in envelope


def test_custodial_trading_prompt_stays_direct_and_disables_fallback():
    envelope = route_prompt(
        "Execute the trading rebalance and update the custodial Citadel positions.",
        source_platform="cli",
    )

    assert envelope["recommendation"] == "main_context"
    assert envelope["role"] == "custodial"
    assert envelope["safety"] == "critical"
    assert envelope["stakes"] == "critical"
    assert envelope["model_tier"] == "custodial_direct"
    assert envelope["reasoning_tier"] == "ultrathink"
    assert envelope["allow_fallback"] is False
    assert "custodial-direct-v1" in envelope["policy_ids"]
    assert "financial_or_trading_action" in envelope["requires_approval"]
    assert "custodial_domain" in envelope["delegation_forbidden"]
    assert "financial_or_trading_execution" in envelope["delegation_forbidden"]


def test_standalone_position_does_not_trigger_custodial_routing():
    envelope = route_prompt("Fix the CSS position of the checkout button.", source_platform="cli")

    assert envelope["role"] != "custodial"
    assert envelope["safety"] != "critical"
    assert envelope["model_tier"] != "custodial_direct"
    assert "financial_or_trading_action" not in envelope["requires_approval"]
    assert "custodial_domain" not in envelope["delegation_forbidden"]



def test_email_and_customer_copy_require_approval_without_policy_prose():
    envelope = route_prompt(
        "Draft and send a customer-facing Elliott Bay Brewing email about tonight's special.",
        source_platform="telegram",
    )

    assert envelope["recommendation"] == "main_context"
    assert envelope["safety"] == "sensitive"
    assert envelope["stakes"] == "high"
    assert "email-draft-approval-v1" in envelope["policy_ids"]
    assert "eb-customer-copy-guard-v1" in envelope["policy_ids"]
    assert "customer_email_draft" in envelope["requires_approval"]
    assert "email_send" in envelope["requires_approval"]
    assert "external_send_or_publish" in envelope["delegation_forbidden"]
    assert all("approval before publish" not in hit.get("note", "").lower() for hit in envelope["classifier_hits"])


def test_user_override_can_force_fast_main_context():
    envelope = route_prompt("/fast Quickly explain what this function does.", source_platform="cli")

    assert envelope["user_override"] == {
        "present": True,
        "kind": "use_fast",
        "raw": "/fast",
    }
    assert envelope["recommendation"] == "main_context"
    assert envelope["model_tier"] == "fast"
    assert envelope["reasoning_tier"] in {"none", "think"}


def test_fast_override_does_not_downgrade_custodial_prompt():
    envelope = route_prompt("/fast Check Citadel trading positions.", source_platform="cli")

    assert envelope["user_override"]["kind"] == "use_fast"
    assert envelope["stakes"] == "critical"
    assert envelope["model_tier"] == "custodial_direct"
    assert envelope["reasoning_tier"] == "ultrathink"


def test_deep_override_does_not_downgrade_custodial_prompt():
    envelope = route_prompt("/deep Check Citadel trading positions.", source_platform="cli")

    assert envelope["user_override"]["kind"] == "use_deep"
    assert envelope["stakes"] == "critical"
    assert envelope["model_tier"] == "custodial_direct"
    assert envelope["reasoning_tier"] == "ultrathink"


def test_user_override_can_request_deep_reasoning_without_provider_binding():
    envelope = route_prompt("/deep Design the migration framework.", source_platform="cli")

    assert envelope["user_override"] == {
        "present": True,
        "kind": "use_deep",
        "raw": "/deep",
    }
    assert envelope["reasoning_tier"] == "ultrathink"
    assert envelope["model_tier"] == "deep"
    assert "provider" not in envelope
    assert "model" not in envelope


def test_keyword_matching_does_not_escalate_on_substrings():
    envelope = route_prompt(
        "Explain sender telemetry for preproduction tokenization, a post-mortem, and foo.env; "
        "the item was deleted yesterday.",
        source_platform="cli",
    )

    assert "email_send" not in envelope["requires_approval"]
    assert "production_change" not in envelope["requires_approval"]
    assert "credential_change" not in envelope["requires_approval"]
    assert "file_deletion" not in envelope["requires_approval"]
    assert "customer_copy_publish" not in envelope["requires_approval"]
    assert envelope["delegation_forbidden"] == []


def test_do_not_delegate_override_wins_over_parallel_research_signal():
    envelope = route_prompt(
        "Do not delegate. Research GitHub and docs in parallel, then compare options.",
        source_platform="cli",
    )

    assert envelope["user_override"]["kind"] == "do_not_delegate"
    assert envelope["recommendation"] == "main_context"
    assert envelope["delegate_target_hint"] is None
    assert envelope["delegation_score"] <= 0


def test_fast_override_wins_when_mixed_with_do_not_delegate():
    envelope = route_prompt(
        "/fast Do not delegate. Research GitHub and docs in parallel.",
        source_platform="cli",
    )

    assert envelope["user_override"]["kind"] == "use_fast"
    assert envelope["recommendation"] == "delegate"


def test_delegate_override_promotes_borderline_prompt_to_delegation():
    envelope = route_prompt("Delegate this small cleanup task.", source_platform="cli")

    assert envelope["user_override"]["kind"] == "delegate"
    assert envelope["recommendation"] == "delegate"
    assert envelope["delegation_score"] >= 4


def test_delegation_forbidden_wins_over_high_parallel_score():
    envelope = route_prompt(
        "Compare Citadel trading positions in parallel across multiple independent agents.",
        source_platform="cli",
    )

    assert envelope["recommendation"] == "main_context"
    assert envelope["model_tier"] == "custodial_direct"
    assert "custodial_domain" in envelope["delegation_forbidden"]
    assert envelope["delegate_target_hint"] == "none"


def test_bare_review_does_not_trigger_security_review_policy():
    envelope = route_prompt("Review my pull request and suggest cleanup.", source_platform="cli")

    assert "security-review-ultrathink-v1" not in envelope["policy_ids"]
    assert envelope["role"] != "reviewer"


def test_false_positive_send_and_trade_phrases_do_not_escalate():
    send_envelope = route_prompt("Send a Slack message with the lunch count.", source_platform="cli")
    trade_envelope = route_prompt("Review fair trade secrets in the article.", source_platform="cli")

    assert "email_send" not in send_envelope["requires_approval"]
    assert "email-draft-approval-v1" not in send_envelope["policy_ids"]
    assert trade_envelope["role"] != "custodial"
    assert "financial_or_trading_action" not in trade_envelope["requires_approval"]


def test_classifier_hits_do_not_echo_sensitive_prompt_text():
    envelope = route_prompt("Rotate api key secret credential password token for prod.", source_platform="cli")
    serialized_hits = repr(envelope["classifier_hits"]).lower()

    assert "api key" not in serialized_hits
    assert "password" not in serialized_hits
    assert "token" not in serialized_hits


def test_fast_override_hit_uses_reasoning_axis():
    envelope = route_prompt("/fast summarize this", source_platform="cli")

    assert any(hit["id"] == "user-use_fast" and hit["axis"] == "reasoning" for hit in envelope["classifier_hits"])


def test_build_routing_metadata_disabled_empty_or_none_returns_none():
    assert build_routing_metadata("Summarize this", routing_config=None) is None
    assert build_routing_metadata("Summarize this", routing_config={}) is None
    assert build_routing_metadata("Summarize this", routing_config={"enabled": False}) is None


def test_build_routing_metadata_defaults_enabled_config_to_dry_run():
    metadata = build_routing_metadata("Summarize this", routing_config={"enabled": True})

    assert metadata is not None
    assert metadata["mode"] == "dry_run"


def test_build_routing_metadata_rejects_non_dry_run_modes():
    with pytest.raises(ValueError, match="dry_run"):
        build_routing_metadata("Summarize this", routing_config={"enabled": True, "mode": "enforce"})


def test_tier_bindings_resolve_from_config_without_mutating_envelope():
    from agent.routing import resolve_tier_binding

    envelope = route_prompt("/deep Design the migration framework.", source_platform="cli")
    config = {
        "mode": "dry_run",
        "tiers": {
            "deep": {
                "provider": "openrouter",
                "model": "anthropic/claude-opus-4.6",
                "reasoning_effort": "xhigh",
            }
        },
    }

    binding = resolve_tier_binding(envelope, config)

    assert binding == {
        "model_tier": "deep",
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4.6",
        "reasoning_effort": "xhigh",
    }
    assert "provider" not in envelope
    assert "model" not in envelope


def test_resolve_tier_binding_handles_missing_and_malformed_bindings():
    from agent.routing import resolve_tier_binding

    assert resolve_tier_binding({}, {"tiers": {}}) is None
    assert resolve_tier_binding({"model_tier": "deep"}, {"tiers": {}}) == {"model_tier": "deep"}
    assert resolve_tier_binding({"model_tier": "deep"}, {"tiers": {"deep": "bad"}}) == {"model_tier": "deep"}


def test_classifier_hits_are_capped():
    envelope = route_prompt(
        "/deep Do not delegate. Research Reddit GitHub docs YouTube X/Twitter web sources in parallel. "
        "Implement code, test, debug, fix repo architecture schema framework migration design. "
        "Security vulnerability review audit. Draft and send customer-facing Elliott Bay Brewing email, "
        "publish social post, delete file, deploy production, rotate api key secret credential password token.",
        source_platform="cli",
    )

    assert len(envelope["classifier_hits"]) <= 20
