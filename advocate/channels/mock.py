"""
MockChannel — a working, demoable counterparty.
===============================================

This lets the WHOLE resolution loop run end-to-end with no real website and no
browser automation. It plays the role of a company's support agent in one of
two modes:

* mode="llm"      -> an LLM role-plays a realistic, mildly adversarial support
                     rep (deflects once, lowballs, then settles if pushed).
                     This is the impressive demo: a genuine multi-turn
                     negotiation against an LLM "opponent".
* mode="scripted" -> returns a fixed list of replies in order. Useful for
                     deterministic demos and when you have no API key.

>>> CANDIDATES: replace this with real channels (Playwright web chat / ticket
    portal, email via IMAP/SMTP). Keep the same `Channel` interface and the
    orchestrator will work unchanged.
"""

import json
from typing import List, Optional

from .base import Channel
from ..llm import LLMClient
from ..models import Case, MessageRole


COUNTERPARTY_SYSTEM = """\
You are role-playing a customer-support representative for a company, talking to
a customer (or their agent) in a chat window. Stay in character.

Behaviour rules (make it realistic, mildly adversarial, but resolvable):
- Turn 1: be polite but DEFLECT or stall once ("let me check", ask for an order
  number or proof) — do not resolve immediately.
- If they provide info/evidence: make a LOWBALL offer (well below what they want,
  or offer store credit instead of a refund).
- If they push back / counter / escalate firmly: improve the offer toward a fair
  resolution, and eventually agree to a full refund if pressed twice.
- Keep replies short (1-3 sentences), like a real chat agent.
- When you finally agree, clearly state the resolution and amount.

Respond with ONLY the support rep's chat message text (no JSON, no quotes).
"""


class MockChannel(Channel):
    name = "mock"

    def __init__(self, mode: str = "llm", llm: Optional[LLMClient] = None,
                 scripted_replies: Optional[List[str]] = None):
        self.mode = mode
        self.llm = llm
        self.scripted_replies = list(scripted_replies or [])
        self._script_idx = 0

    def open_case(self, case: Case, opening_statement: str) -> None:
        print("\n[%s] Opening %s channel with: %s"
              % (self.name, case.plan.channel if case.plan else "chat",
                 case.plan.target_counterparty if case.plan else "support"))
        self.send(case, opening_statement)

    def send(self, case: Case, text: str) -> None:
        # In a real channel this types into the chat box / sends the email.
        # Here we just record it on the transcript (the orchestrator also logs).
        pass

    def receive(self, case: Case, timeout_s: float = 0.0) -> Optional[str]:
        if self.mode == "scripted":
            if self._script_idx >= len(self.scripted_replies):
                return None
            reply = self.scripted_replies[self._script_idx]
            self._script_idx += 1
            return reply
        return self._llm_reply(case)

    # -- LLM counterparty ---------------------------------------------------

    def _llm_reply(self, case: Case) -> str:
        if self.llm is None:
            raise RuntimeError("MockChannel(mode='llm') needs an LLMClient.")
        convo = []
        for m in case.transcript:
            if m.role == MessageRole.AGENT.value:
                convo.append({"role": "user", "content": m.text})
            elif m.role == MessageRole.COUNTERPARTY.value:
                convo.append({"role": "assistant", "content": m.text})
        context = {
            "the_customer_wants": case.goal,
            "order_context": case.context,
        }
        messages = [
            {"role": "system", "content": COUNTERPARTY_SYSTEM},
            {"role": "user", "content": "Case background: " + json.dumps(context)},
        ] + convo
        return self.llm.chat(messages, temperature=0.7, max_tokens=200).strip()
