"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  NEGOTIATOR  (advocate/agent/negotiator.py)                                  ║
║  Deterministic decision engine — the agent's TRUST BOUNDARY.                 ║
║                                                                              ║
║  Pure function: no I/O, no LLM, no randomness.                              ║
║  Same inputs → same Decision, every time.                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from ..models import (
    ActionType,
    Classification,
    Decision,
    MessageType,
    OutcomeKind,
    ResolutionPolicy,
)


def _is_cancellation_goal(policy: ResolutionPolicy) -> bool:
    """A cancellation goal wants confirmation, not money.
    target_amount == 0 and min_acceptable_amount == 0 signals this."""
    return policy.target_amount == 0 and policy.min_acceptable_amount == 0


def _has_escalation_budget(policy: ResolutionPolicy, escalation_count: int) -> bool:
    return escalation_count < policy.escalation_budget


def _handle_offer(classification: Classification, policy: ResolutionPolicy,
                  escalation_count: int) -> Decision:
    """Decide what to do with a concrete offer. Order matters — check
    forbidden outcomes first, then amount thresholds, then fallbacks."""

    kind = classification.offer_kind
    amount = classification.offer_amount

    # ── Cancellation goal: money/discount offers are RETENTION BAIT ──
    if _is_cancellation_goal(policy):
        # The only acceptable outcome is written confirmation (FINAL_RESOLUTION).
        # Any money or non-trivial offer is bait to keep the customer.
        if amount > 0 or kind not in (OutcomeKind.NONE.value, OutcomeKind.APOLOGY.value):
            if _has_escalation_budget(policy, escalation_count):
                return Decision(
                    action=ActionType.ESCALATE.value,
                    reason="Monetary/discount offer is retention bait for a cancellation goal; escalating.",
                )
            return Decision(
                action=ActionType.PAUSE_FOR_APPROVAL.value,
                reason="Retention bait persists and escalation budget exhausted; asking user.",
                requires_human=True,
            )

    # ── Forbidden outcome kind → never accept, always counter ──
    if kind in policy.forbidden_outcomes:
        return Decision(
            action=ActionType.COUNTER_OFFER.value,
            reason="Offer kind '%s' is forbidden by policy; countering toward %s %s."
                   % (kind, policy.target_amount, policy.currency),
            target_amount=policy.target_amount,
        )

    # ── Offer meets or exceeds the minimum → accept ──
    if amount >= policy.min_acceptable_amount and policy.min_acceptable_amount > 0:
        return Decision(
            action=ActionType.ACCEPT.value,
            reason="Offer of %s %s meets minimum acceptable amount of %s."
                   % (amount, policy.currency, policy.min_acceptable_amount),
        )

    # ── Below threshold ──
    if policy.ask_below_threshold:
        return Decision(
            action=ActionType.PAUSE_FOR_APPROVAL.value,
            reason="Offer of %s %s is below minimum %s; asking user to decide."
                   % (amount, policy.currency, policy.min_acceptable_amount),
            requires_human=True,
        )

    # Auto-counter toward target
    return Decision(
        action=ActionType.COUNTER_OFFER.value,
        reason="Offer of %s %s is below minimum %s; auto-countering toward %s."
               % (amount, policy.currency, policy.min_acceptable_amount, policy.target_amount),
        target_amount=policy.target_amount,
    )


def decide(
    classification: Classification,
    policy: ResolutionPolicy,
    escalation_count: int,
) -> Decision:
    """Choose the next action under the policy. Pure function — no I/O, no LLM.

    Priority order:
      1. Terminal / simple message types (final_resolution, info_request, unknown)
      2. Denial → escalate or mark denied
      3. Deflection / dark-pattern → escalate or pause
      4. Offer → forbidden / acceptable / below-threshold / cancellation-bait
    """
    msg_type = classification.msg_type

    # ── 1. FINAL_RESOLUTION — the counterparty confirmed it's done ──
    if msg_type == MessageType.FINAL_RESOLUTION.value:
        return Decision(
            action=ActionType.MARK_RESOLVED.value,
            reason="Counterparty confirmed final resolution.",
        )

    # ── 2. INFO_REQUEST — they need more details from us ──
    if msg_type == MessageType.INFO_REQUEST.value:
        return Decision(
            action=ActionType.PROVIDE_INFO.value,
            reason="Counterparty requested additional information or evidence.",
        )

    # ── 3. UNKNOWN — can't parse, ask to clarify ──
    if msg_type == MessageType.UNKNOWN.value:
        return Decision(
            action=ActionType.PROVIDE_INFO.value,
            reason="Reply unclear; requesting clarification.",
        )

    # ── 4. DENIAL — they refuse ──
    if msg_type == MessageType.DENIAL.value:
        if _has_escalation_budget(policy, escalation_count):
            return Decision(
                action=ActionType.ESCALATE.value,
                reason="Denial received; escalating (%d of %d used)."
                       % (escalation_count, policy.escalation_budget),
            )
        return Decision(
            action=ActionType.MARK_DENIED.value,
            reason="Denial received and escalation budget exhausted (%d/%d)."
                   % (escalation_count, policy.escalation_budget),
        )

    # ── 5. DEFLECTION or dark-pattern flag ──
    if msg_type == MessageType.DEFLECTION.value or classification.is_dark_pattern:
        if _has_escalation_budget(policy, escalation_count):
            return Decision(
                action=ActionType.ESCALATE.value,
                reason="Deflection/dark-pattern detected; escalating (%d of %d used)."
                       % (escalation_count, policy.escalation_budget),
            )
        return Decision(
            action=ActionType.PAUSE_FOR_APPROVAL.value,
            reason="Deflection persists and escalation budget exhausted; asking user.",
            requires_human=True,
        )

    # ── 6. OFFER — delegate to the offer handler ──
    if msg_type == MessageType.OFFER.value:
        return _handle_offer(classification, policy, escalation_count)

    # ── Fallback (should never reach here, but safety net) ──
    return Decision(
        action=ActionType.PROVIDE_INFO.value,
        reason="Unhandled message type '%s'; requesting clarification." % msg_type,
    )
