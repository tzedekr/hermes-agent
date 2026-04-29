"""Advisory prompt routing for Hermes.

This module is intentionally provider-agnostic. It classifies a user prompt into
an advisory routing envelope; the runtime remains authoritative and records the
actual outcome elsewhere. The router emits policy identifiers and compact
classifier hits, not long policy prose.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

SCHEMA = "hermes.routing.v1"
SCHEMA_VERSION = "1.0.0"
POLICY_REGISTRY_VERSION = "1.0.0"
SOURCE = f"hermes-router:{SCHEMA_VERSION}"

Recommendation = Literal["main_context", "delegate", "ask_clarification", "block", "defer"]
Role = Literal["executor", "researcher", "reviewer", "planner", "custodial"]
Safety = Literal["normal", "sensitive", "critical"]
Stakes = Literal["low", "medium", "high", "critical"]
ReasoningTier = Literal["none", "think", "megathink", "ultrathink"]
ModelTier = Literal["fast", "balanced", "deep", "local_fast", "local_deep", "custodial_direct"]

_APPROVALS = {
    "email_draft",
    "email_send",
    "customer_copy_publish",
    "customer_email_draft",
    "todoist_task_creation",
    "calendar_event_create_or_modify",
    "file_deletion",
    "production_change",
    "credential_change",
    "financial_or_trading_action",
}

_DELEGATION_FORBIDDEN = {
    "custodial_domain",
    "financial_or_trading_execution",
    "secrets_or_credentials",
    "external_send_or_publish",
    "irreversible_destructive_ops",
    "mcp_write_fragile",
}


def _term_pattern(term: str) -> str:
    term = term.strip().lower()
    escaped = re.escape(term)
    # Treat hyphenated text as a single token so words like "post" do not
    # match inside "post-mortem". This also prevents punctuation-led future
    # triggers such as "/admin" or ".env" from matching mid-token.
    boundary_chars = "a-z0-9_-"
    return f"(?<![{boundary_chars}]){escaped}(?![{boundary_chars}])"


def _contains(prompt: str, *terms: str) -> bool:
    low = prompt.lower()
    return any(re.search(_term_pattern(term), low) for term in terms if term.strip())


def _add_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


@dataclass
class RoutingState:
    prompt: str
    source_platform: str = "cli"
    recommendation: Recommendation = "main_context"
    role: Role = "executor"
    safety: Safety = "normal"
    stakes: Stakes = "low"
    complexity: int = 2
    delegation_score: int = 0
    safety_score: int = 0
    confidence: float = 0.72
    policy_ids: list[str] = field(default_factory=list)
    classifier_hits: list[dict[str, Any]] = field(default_factory=list)
    requires_approval: list[str] = field(default_factory=list)
    delegation_forbidden: list[str] = field(default_factory=list)
    allow_fallback: bool = True
    suggested_toolsets: list[str] = field(default_factory=list)
    delegate_target_hint: str | None = None
    user_override: dict[str, Any] | None = None

    def hit(self, id_: str, axis: str, delta: int, *, matched: str = "", note: str = "") -> None:
        entry: dict[str, Any] = {"id": id_, "axis": axis, "delta": delta}
        # Do not echo user prompt text in routing telemetry. The prompt_hash is
        # the durable correlation mechanism; classifier hits stay content-free.
        if matched and matched == id_:
            entry["matched"] = matched[:80]
        if note:
            entry["note"] = note[:240]
        self.classifier_hits.append(entry)

    def policy(self, policy_id: str) -> None:
        already_present = policy_id in self.policy_ids
        _add_unique(self.policy_ids, policy_id)
        if not already_present:
            self.hit(policy_id.replace("-v1", ""), "policy", 0, matched=policy_id)

    def approval(self, approval: str) -> None:
        if approval in _APPROVALS:
            _add_unique(self.requires_approval, approval)

    def forbid_delegation(self, reason: str) -> None:
        if reason in _DELEGATION_FORBIDDEN:
            _add_unique(self.delegation_forbidden, reason)


def _detect_user_override(state: RoutingState) -> None:
    text = state.prompt.strip()
    lowered = text.lower()
    patterns = [
        (r"^/fast\b", "use_fast", "/fast"),
        (r"\bdo not delegate\b|\bdon't delegate\b", "do_not_delegate", "do not delegate"),
        (r"\bdelegate this\b|\buse subagents\b", "delegate", "delegate"),
        (r"^/deep\b|\bultrathink\b|\bmegathink\b", "use_deep", "/deep"),
    ]
    for pattern, kind, raw in patterns:
        if re.search(pattern, lowered):
            state.user_override = {"present": True, "kind": kind, "raw": raw}
            axis = "reasoning" if kind in {"use_fast", "use_deep"} else "delegation"
            state.hit(f"user-{kind}", axis, 0, matched=f"user-{kind}")
            return
    state.user_override = {"present": False, "kind": "other", "raw": ""}


def _is_custodial_or_trading_prompt(prompt: str) -> bool:
    """Return whether the prompt is clearly about trading/custodial work.

    Avoid treating the standalone word "position" as financial; it is common in
    CSS/layout, hiring, and general prose. Position only escalates when paired
    with explicit trading/custody context.
    """
    if _contains(prompt, "citadel", "custodial", "rebalance"):
        return True
    if _contains(prompt, "position", "positions") and _contains(
        prompt,
        "portfolio",
        "account",
        "brokerage",
        "market",
        "trading",
        "trade",
        "custodial",
        "citadel",
        "rebalance",
    ):
        return True
    return False


def _classify_safety_and_policy(state: RoutingState) -> None:
    prompt = state.prompt

    if _is_custodial_or_trading_prompt(prompt):
        state.role = "custodial"
        state.safety = "critical"
        state.stakes = "critical"
        state.safety_score += 10
        state.complexity = max(state.complexity, 7)
        state.allow_fallback = False
        state.policy("custodial-direct-v1")
        state.approval("financial_or_trading_action")
        state.forbid_delegation("custodial_domain")
        state.forbid_delegation("financial_or_trading_execution")
        state.hit("custodial-domain", "safety", 10, matched="custodial/trading")

    if _contains(prompt, "api key", "secret", "credential", ".env", "password", "token"):
        if state.safety == "normal":
            state.safety = "sensitive"
        state.stakes = "high" if state.stakes in {"low", "medium"} else state.stakes
        state.safety_score += 6
        state.approval("credential_change")
        state.forbid_delegation("secrets_or_credentials")
        state.hit("secrets-or-credentials", "safety", 6, matched="secret/credential")

    email_context = _contains(prompt, "email", "outlook", "reply to")
    email_send_intent = _contains(prompt, "send email", "send an email", "send the email", "reply to") or (
        _contains(prompt, "send") and email_context
    )
    if email_context:
        state.safety = "sensitive" if state.safety == "normal" else state.safety
        state.stakes = "high" if state.stakes in {"low", "medium"} else state.stakes
        state.policy("email-draft-approval-v1")
        state.approval("email_draft")
        if email_send_intent:
            state.approval("email_send")
            state.forbid_delegation("external_send_or_publish")
        state.hit("email-action", "safety", 5, matched="email-action")

    if _contains(prompt, "elliott bay", "eb brewing", "customer-facing", "customer facing", "publish", "social post"):
        state.safety = "sensitive" if state.safety == "normal" else state.safety
        state.stakes = "high" if state.stakes in {"low", "medium"} else state.stakes
        state.policy("eb-customer-copy-guard-v1")
        state.approval("customer_email_draft")
        if _contains(prompt, "publish", "post", "send"):
            state.approval("customer_copy_publish")
            state.forbid_delegation("external_send_or_publish")
        state.hit("customer-copy", "policy", 4, matched="customer-facing")

    if _contains(prompt, "delete", "remove all", "erase", "wipe", "truncate", "rm -rf", "drop table", "destroy"):
        state.safety = "critical"
        state.stakes = "critical"
        state.safety_score += 8
        state.approval("file_deletion")
        state.forbid_delegation("irreversible_destructive_ops")
        state.hit("destructive-operation", "safety", 8, matched="delete/destroy")

    if _contains(prompt, "production", "prod", "deploy", "restart service", "shutdown", "launchctl", "systemctl"):
        state.stakes = "high" if state.stakes in {"low", "medium"} else state.stakes
        state.approval("production_change")
        state.hit("production-change", "stakes", 5, matched="production/deploy")


def _classify_capability(state: RoutingState) -> None:
    prompt = state.prompt

    if _contains(prompt, "research", "sources", "web", "reddit", "github", "docs", "youtube", "x/twitter"):
        state.role = "researcher" if state.role == "executor" else state.role
        state.complexity = max(state.complexity, 5)
        state.delegation_score += 3
        for toolset in ("web", "search"):
            _add_unique(state.suggested_toolsets, toolset)
        state.hit("research-task", "delegation", 3, matched="research/web")

    if _contains(prompt, "parallel", "separately", "multiple", "compare", "independent"):
        state.delegation_score += 3
        state.complexity = max(state.complexity, 6)
        state.hit("parallelizable", "delegation", 3, matched="parallel/multiple")

    if _contains(prompt, "implement", "build", "code", "test", "debug", "fix", "repo"):
        state.complexity = max(state.complexity, 5)
        for toolset in ("terminal", "file"):
            _add_unique(state.suggested_toolsets, toolset)
        state.hit("coding-task", "complexity", 2, matched="code/build/test")

    if _contains(prompt, "architecture", "schema", "framework", "migration", "design"):
        state.role = "planner" if state.role == "executor" else state.role
        state.complexity = max(state.complexity, 7)
        state.policy("architecture-ultrathink-v1")
        state.hit("architecture-task", "reasoning", 4, matched="architecture/schema")

    if _contains(prompt, "security review", "security audit", "vulnerability", "audit"):
        state.role = "reviewer" if state.role == "executor" else state.role
        state.complexity = max(state.complexity, 7)
        state.policy("security-review-ultrathink-v1")
        state.hit("security-review", "reasoning", 4, matched="security/audit")


def _resolve_recommendation(state: RoutingState) -> None:
    override = state.user_override or {}
    if override.get("kind") == "do_not_delegate":
        state.delegation_score = min(state.delegation_score, 0)
    elif override.get("kind") == "delegate":
        state.delegation_score += 4

    if state.delegation_forbidden:
        state.recommendation = "main_context"
        state.delegate_target_hint = "none"
        return

    if state.delegation_score >= 4:
        state.recommendation = "delegate"
        _add_unique(state.suggested_toolsets, "delegation")
        if state.role in {"researcher", "reviewer", "planner"}:
            state.delegate_target_hint = state.role
        else:
            state.delegate_target_hint = "executor"
    else:
        state.recommendation = "main_context"
        state.delegate_target_hint = None


def _reasoning_tier(complexity: int, stakes: Stakes, policy_ids: list[str], override: dict[str, Any] | None) -> ReasoningTier:
    if override and override.get("kind") == "use_deep":
        return "ultrathink"
    if override and override.get("kind") == "use_fast" and stakes in {"low", "medium"} and complexity <= 5:
        return "none"
    if stakes == "critical" or "security-review-ultrathink-v1" in policy_ids or "architecture-ultrathink-v1" in policy_ids:
        return "ultrathink"
    if stakes == "high" or complexity >= 8:
        return "megathink"
    if complexity >= 4:
        return "think"
    return "none"


def _model_tier(state: RoutingState, reasoning_tier: ReasoningTier) -> ModelTier:
    override = state.user_override or {}
    if state.role == "custodial" or (state.delegation_forbidden and state.safety == "critical"):
        return "custodial_direct"
    if override.get("kind") == "use_fast" and state.stakes in {"low", "medium"}:
        return "fast"
    if reasoning_tier == "ultrathink":
        return "deep"
    if state.recommendation == "delegate" or reasoning_tier in {"think", "megathink"}:
        return "balanced"
    return "fast"


def resolve_tier_binding(envelope: dict[str, Any], routing_config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve an abstract model tier to its configured binding.

    Bindings are returned as runtime metadata only. They are deliberately kept
    out of the advisory envelope so provider/model names never leak into prompt
    prose or classifier output.
    """
    if not isinstance(envelope, dict) or not isinstance(routing_config, dict):
        return None
    tier = envelope.get("model_tier")
    if not isinstance(tier, str) or not tier:
        return None
    tiers = routing_config.get("tiers") or {}
    binding = tiers.get(tier)
    if not isinstance(binding, dict):
        return {"model_tier": tier}

    resolved: dict[str, Any] = {"model_tier": tier}
    for key in ("provider", "model", "base_url", "api_mode", "reasoning_effort", "service_tier", "allow_fallback"):
        value = binding.get(key)
        if value not in (None, ""):
            resolved[key] = value
    return resolved


def build_routing_metadata(
    prompt: str,
    *,
    source_platform: str = "cli",
    routing_config: dict[str, Any] | None = None,
    model: str = "",
    provider: str = "",
    reasoning_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build turn-level dry-run routing metadata.

    The metadata is observational by default: it records the advisory envelope,
    any configured tier binding, and the runtime outcome. It does not mutate the
    caller's model/provider/reasoning settings.
    """
    config = routing_config if isinstance(routing_config, dict) else {}
    if not config or config.get("enabled", True) is False:
        return None
    mode = str(config.get("mode") or "dry_run").strip() or "dry_run"
    if mode != "dry_run":
        raise ValueError("routing.mode currently supports only 'dry_run'")
    envelope = route_prompt(prompt, source_platform=source_platform)
    binding = resolve_tier_binding(envelope, config)
    return {
        "mode": mode,
        "recommendation": envelope,
        "resolved_binding": binding,
        "outcome": {
            "applied": False,
            "reason": "dry_run_metadata_only" if mode == "dry_run" else "not_applied_by_runtime",
            "model": model,
            "provider": provider,
            "reasoning_config": reasoning_config,
        },
    }


def route_prompt(prompt: str, *, source_platform: str = "cli") -> dict[str, Any]:
    """Return a compact advisory routing envelope for *prompt*.

    The returned dictionary is intentionally safe to serialize. It contains no
    provider/model names and no policy prose. Hermes runtime may ignore or
    override the recommendation and should record that in ``outcome``.
    """
    state = RoutingState(prompt=prompt or "", source_platform=source_platform or "cli")
    _detect_user_override(state)
    _classify_safety_and_policy(state)
    _classify_capability(state)
    _resolve_recommendation(state)

    reasoning_tier = _reasoning_tier(state.complexity, state.stakes, state.policy_ids, state.user_override)
    model_tier = _model_tier(state, reasoning_tier)

    confidence = max(0.0, min(1.0, 0.55 + min(abs(state.delegation_score) + abs(state.safety_score), 10) / 25))
    prompt_hash = "sha256:" + hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()

    return {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "policy_registry_version": POLICY_REGISTRY_VERSION,
        "correlation_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "prompt_hash": prompt_hash,
        "source": SOURCE,
        "mode": "advisory",
        "recommendation": state.recommendation,
        "role": state.role,
        "safety": state.safety,
        "stakes": state.stakes,
        "complexity": max(1, min(10, state.complexity)),
        "reasoning_tier": reasoning_tier,
        "reasoning_override_reason": None,
        "model_tier": model_tier,
        "delegation_score": max(-10, min(10, state.delegation_score)),
        "safety_score": max(-10, min(10, state.safety_score)),
        "confidence": round(confidence, 2),
        "classifier_hits": state.classifier_hits[:20],
        "policy_ids": state.policy_ids,
        "requires_approval": state.requires_approval,
        "delegation_forbidden": state.delegation_forbidden,
        "allow_fallback": state.allow_fallback,
        "suggested_toolsets": state.suggested_toolsets,
        "delegate_target_hint": state.delegate_target_hint,
        "user_override": state.user_override,
        "outcome": None,
    }
