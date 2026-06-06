"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  ORCHESTRATOR  (advocate/agent/orchestrator.py)                              ║
║  The durable, resumable resolution loop — the heart of the agent.            ║
║                                                                              ║
║  plan → open → { receive → classify → decide → act → persist } → repeat     ║
║  until RESOLVED / DENIED / ABANDONED, or paused for a human.                 ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import time

from ..channels import MockChannel
from ..llm import LLMClient, LLMError
from ..models import (
    ActionType,
    Case,
    CaseStatus,
    MessageRole,
    MessageType,
    OutcomeKind,
)
from ..store import CaseStore
from .classifier import classify
from .negotiator import decide
from .strategist import make_plan


# A safety cap so a buggy loop can never run forever. Tune as needed.
MAX_TURNS = 12

# ─── LLM prompt for composing outbound messages ────────────────────────────
COMPOSER_SYSTEM = """\
You are Advocate, an autonomous consumer-advocacy agent writing the next
chat message to a company's support rep on the customer's behalf.

Rules:
- Be polite, concise (1-3 sentences), firm, and factual.
- Reference evidence when useful.
- Do NOT second-guess the action — just phrase it.
- Output ONLY the message text — no preamble, no quotes, no markdown.
"""


def _compose_message(case: Case, action: str, llm: LLMClient,
                     extra_context: str = "") -> str:
    """Use the LLM to phrase an outbound message. The action is already decided
    by the negotiator — the LLM only writes the words, never overrides the choice."""
    goal = case.goal
    evidence = ", ".join(case.evidence) if case.evidence else "available on request"
    last_counterparty = ""
    for m in reversed(case.transcript):
        if m.role == MessageRole.COUNTERPARTY.value:
            last_counterparty = m.text
            break

    # Use escalation ladder if available and we're escalating
    escalation_hint = ""
    if action == ActionType.ESCALATE.value and case.plan and case.plan.escalation_ladder:
        idx = min(case.escalation_count, len(case.plan.escalation_ladder) - 1)
        escalation_hint = "Escalation strategy: " + case.plan.escalation_ladder[idx]

    prompt = (
        "Action to take: %s\n"
        "Customer goal: %s\n"
        "Evidence: %s\n"
        "Their last message: %s\n"
        "%s\n%s"
        % (action, goal, evidence, last_counterparty, escalation_hint, extra_context)
    )

    messages = [
        {"role": "system", "content": COMPOSER_SYSTEM},
        {"role": "user", "content": prompt.strip()},
    ]

    try:
        return llm.chat(messages, temperature=0.4, max_tokens=250).strip()
    except LLMError:
        # Fallback: a safe generic message so the loop never crashes
        fallbacks = {
            ActionType.PROVIDE_INFO.value: (
                "I've provided the requested information along with supporting evidence. "
                "Please review and let me know how we can proceed."
            ),
            ActionType.COUNTER_OFFER.value: (
                "Thank you for the offer, but it doesn't meet the customer's requirements. "
                "We'd like to work toward a resolution of %s %s."
                % (case.policy.target_amount, case.policy.currency)
            ),
            ActionType.ESCALATE.value: (
                "I'd like to escalate this matter to a supervisor. The current response "
                "doesn't address the customer's legitimate concern."
            ),
            ActionType.ACCEPT.value: (
                "We accept this resolution. Please confirm the details and process it."
            ),
        }
        return fallbacks.get(action, "Please clarify the next steps for resolving this case.")


def _apply_decision(case: Case, decision, channel, llm: LLMClient,
                    store: CaseStore) -> bool:
    """Carry out one Decision: send messages, update status, set outcomes.
    Return True to stop the loop, False to continue."""

    action = decision.action

    # ── PROVIDE_INFO: compose & send an info/evidence message ──
    if action == ActionType.PROVIDE_INFO.value:
        text = _compose_message(case, action, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        store.save(case)
        return False

    # ── COUNTER_OFFER: compose & send a counter toward target_amount ──
    if action == ActionType.COUNTER_OFFER.value:
        extra = "Counter toward: %s %s." % (decision.target_amount, case.policy.currency)
        text = _compose_message(case, action, llm, extra_context=extra)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        store.save(case)
        return False

    # ── ESCALATE: send the next escalation step; increment count ──
    if action == ActionType.ESCALATE.value:
        text = _compose_message(case, action, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.escalation_count += 1
        store.save(case)
        return False

    # ── ACCEPT: status → RESOLVED; set outcome from the classification ──
    if action == ActionType.ACCEPT.value:
        # Find the last counterparty classification to get the offer details
        offer_amount = 0.0
        offer_kind = OutcomeKind.REFUND.value
        for m in reversed(case.transcript):
            if m.role == MessageRole.COUNTERPARTY.value and m.meta:
                offer_amount = m.meta.get("offer_amount", 0.0)
                offer_kind = m.meta.get("offer_kind", OutcomeKind.REFUND.value)
                break

        text = _compose_message(case, action, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.status = CaseStatus.RESOLVED.value
        case.outcome_amount = offer_amount
        case.outcome_kind = offer_kind
        case.add_message(
            MessageRole.SYSTEM.value,
            "✅ Accepted: %s %s (%s)" % (offer_amount, case.policy.currency, offer_kind),
        )
        store.save(case)
        return True

    # ── MARK_RESOLVED: counterparty confirmed resolution ──
    if action == ActionType.MARK_RESOLVED.value:
        # Pull outcome from the last counterparty message metadata
        offer_amount = 0.0
        offer_kind = OutcomeKind.NONE.value
        for m in reversed(case.transcript):
            if m.role == MessageRole.COUNTERPARTY.value and m.meta:
                offer_amount = m.meta.get("offer_amount", 0.0)
                offer_kind = m.meta.get("offer_kind", OutcomeKind.NONE.value)
                break

        case.status = CaseStatus.RESOLVED.value
        case.outcome_amount = offer_amount
        case.outcome_kind = offer_kind
        case.add_message(
            MessageRole.SYSTEM.value,
            "✅ Resolved: counterparty confirmed final resolution.",
        )
        store.save(case)
        return True

    # ── MARK_DENIED: documented final denial ──
    if action == ActionType.MARK_DENIED.value:
        case.status = CaseStatus.DENIED.value
        case.add_message(
            MessageRole.SYSTEM.value,
            "⛔ Denied: %s" % decision.reason,
        )
        store.save(case)
        return True

    # ── ABANDON: escalation budget exhausted, no resolution ──
    if action == ActionType.ABANDON.value:
        case.status = CaseStatus.ABANDONED.value
        case.add_message(
            MessageRole.SYSTEM.value,
            "⚠️ Abandoned: %s" % decision.reason,
        )
        store.save(case)
        return True

    # ── PAUSE_FOR_APPROVAL: human-in-the-loop checkpoint ──
    if action == ActionType.PAUSE_FOR_APPROVAL.value:
        case.status = CaseStatus.NEEDS_APPROVAL.value
        case.add_message(
            MessageRole.SYSTEM.value,
            "⏸️ Paused for human approval: %s" % decision.reason,
        )
        store.save(case)
        return True  # stop the loop; resume when the human approves/rejects

    # Fallback — unknown action, log and continue
    case.add_message(
        MessageRole.SYSTEM.value,
        "⚠️ Unknown action '%s'; skipping." % action,
    )
    store.save(case)
    return False


def resolve_case(case_id: str, db_path: str) -> None:
    """Run (or resume) the full resolution loop for one Case. Persist every step."""

    store = CaseStore(db_path)
    try:
        case = store.get(case_id)
        if not case:
            return
        if case.is_terminal():
            return

        llm = LLMClient()
        cp_mode = case.context.get("_counterparty_mode", "llm")
        channel = MockChannel(mode=cp_mode, llm=llm)

        # ── Handle RESUME from NEEDS_APPROVAL ──────────────────────────
        if case.status == CaseStatus.NEEDS_APPROVAL.value:
            _handle_resume(case, channel, llm, store)
            if case.is_terminal():
                return

        # ── PLAN ───────────────────────────────────────────────────────
        if case.status == CaseStatus.NEW.value:
            try:
                case.plan = make_plan(case, llm)
            except Exception as e:
                case.add_message(MessageRole.SYSTEM.value,
                                 "Planning error: %s (using fallback)" % e)
                from .strategist import _fallback_plan
                case.plan = _fallback_plan(case)

            case.status = CaseStatus.PLANNED.value
            case.add_message(
                MessageRole.SYSTEM.value,
                "📋 Strategy planned. Target: %s" % (
                    case.plan.target_counterparty or "Customer Support"
                ),
            )
            store.save(case)

        # ── OPEN ───────────────────────────────────────────────────────
        if case.status == CaseStatus.PLANNED.value:
            opening = case.plan.opening_statement if case.plan else case.goal
            try:
                channel.open_case(case, opening)
            except Exception as e:
                case.add_message(MessageRole.SYSTEM.value,
                                 "Channel error on open: %s" % e)
                store.save(case)
                return

            case.add_message(MessageRole.AGENT.value, opening)
            case.status = CaseStatus.OPEN.value
            store.save(case)

        # ── MAIN NEGOTIATION LOOP ──────────────────────────────────────
        for turn in range(MAX_TURNS):
            if case.is_terminal() or case.status == CaseStatus.NEEDS_APPROVAL.value:
                break

            # Small delay so the dashboard animates each step visually
            time.sleep(0.8)

            # Receive the counterparty's reply
            try:
                reply = channel.receive(case)
            except Exception as e:
                case.add_message(MessageRole.SYSTEM.value,
                                 "Channel receive error: %s" % e)
                case.status = CaseStatus.WAITING.value
                store.save(case)
                break

            if reply is None:
                case.status = CaseStatus.WAITING.value
                case.add_message(MessageRole.SYSTEM.value,
                                 "No reply received; parking case.")
                store.save(case)
                break

            # Classify the reply
            try:
                classification = classify(case, reply, llm)
            except Exception as e:
                # If classification fails, record the raw reply and ask to clarify
                case.add_message(MessageRole.COUNTERPARTY.value, reply,
                                 msg_type=MessageType.UNKNOWN.value)
                case.add_message(MessageRole.SYSTEM.value,
                                 "Classification error: %s" % e)
                store.save(case)
                continue

            # Record the counterparty message with classification metadata
            case.add_message(
                MessageRole.COUNTERPARTY.value,
                reply,
                msg_type=classification.msg_type,
                meta={
                    "offer_amount": classification.offer_amount,
                    "offer_kind": classification.offer_kind,
                    "is_dark_pattern": classification.is_dark_pattern,
                    "summary": classification.summary,
                    "conditions": classification.conditions,
                },
            )
            store.save(case)

            # Decide the next action (deterministic — no LLM)
            decision = decide(classification, case.policy, case.escalation_count)
            case.add_message(
                MessageRole.SYSTEM.value,
                "🤖 Decision: %s — %s" % (decision.action, decision.reason),
            )
            store.save(case)

            # Apply the decision (may send a message, change status, etc.)
            try:
                done = _apply_decision(case, decision, channel, llm, store)
            except Exception as e:
                case.add_message(MessageRole.SYSTEM.value,
                                 "Error applying decision: %s" % e)
                store.save(case)
                continue

            if done:
                break

        else:
            # MAX_TURNS reached without resolution
            if not case.is_terminal() and case.status != CaseStatus.NEEDS_APPROVAL.value:
                case.status = CaseStatus.ABANDONED.value
                case.add_message(
                    MessageRole.SYSTEM.value,
                    "⚠️ Max turns (%d) reached without resolution; abandoning." % MAX_TURNS,
                )
                store.save(case)

    finally:
        store.close()


def _handle_resume(case: Case, channel, llm: LLMClient, store: CaseStore) -> None:
    """Resume a case after the human clicked approve or reject."""

    # Find the user's reply in the transcript (added by the /approve endpoint)
    user_reply = ""
    for m in reversed(case.transcript):
        if m.role == MessageRole.SYSTEM.value and m.text.startswith("User reply:"):
            user_reply = m.text[len("User reply:"):].strip()
            break

    if not user_reply:
        # No decision yet — stay paused
        return

    approved = user_reply.lower() in ("true", "yes", "approve", "approved", "accept")

    if approved:
        # User approved — accept the last offer
        case.add_message(MessageRole.SYSTEM.value, "✅ User approved. Accepting the offer.")
        store.save(case)

        # Find the last offer details
        offer_amount = 0.0
        offer_kind = OutcomeKind.REFUND.value
        for m in reversed(case.transcript):
            if m.role == MessageRole.COUNTERPARTY.value and m.meta:
                offer_amount = m.meta.get("offer_amount", 0.0)
                offer_kind = m.meta.get("offer_kind", OutcomeKind.REFUND.value)
                break

        text = _compose_message(case, ActionType.ACCEPT.value, llm)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        case.status = CaseStatus.RESOLVED.value
        case.outcome_amount = offer_amount
        case.outcome_kind = offer_kind
        store.save(case)
    else:
        # User rejected — push back and continue negotiating
        case.add_message(MessageRole.SYSTEM.value,
                         "❌ User rejected. Pushing back and resuming negotiation.")
        case.status = CaseStatus.OPEN.value
        store.save(case)

        extra = "The customer has reviewed and rejected the offer. " + (user_reply if user_reply else "")
        text = _compose_message(case, ActionType.COUNTER_OFFER.value, llm,
                                extra_context=extra)
        channel.send(case, text)
        case.add_message(MessageRole.AGENT.value, text)
        store.save(case)
