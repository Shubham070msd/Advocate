# Advocate — Autonomous Consumer-Advocacy Agent

## Project Overview
An autonomous AI agent that fights consumer disputes (refunds, cancellations, billing) on the user's behalf. The agent negotiates with a company's support channel using a multi-turn loop, detects dark patterns, and pauses for human approval at guardrails.

## Architecture
```
User Dashboard (web/index.html)
    ↓ HTTP JSON API
server.py (stdlib http.server)
    ↓ spawns background thread
orchestrator.py → THE LOOP (plan → open → classify → decide → act → persist)
    ↓ uses
strategist.py (LLM) → plans the negotiation strategy
classifier.py (LLM) → interprets each counterparty reply
negotiator.py (CODE, NO LLM) → deterministic decision engine
MockChannel (LLM) → simulates adversarial support rep
    ↓ persists
store.py (SQLite) → Case JSON blobs, WAL mode
```

## Core Design Principle
**LLM perceives and phrases. Code decides.** The negotiator is pure deterministic Python — no LLM, no I/O, no randomness. A model hallucination can never silently accept a bad deal or violate a "never" rule. This is the trust boundary.

## Status State Machine
```
NEW → PLANNED → OPEN → (negotiating loop) → RESOLVED / DENIED / ABANDONED
                  ↓
            NEEDS_APPROVAL → (human approves/rejects) → resumes loop
```

## What's PROVIDED (don't modify unless enhancing)
- `advocate/llm.py` — Provider-agnostic LLM client (OpenAI/Anthropic/Groq/DeepSeek/Ollama)
- `advocate/models.py` — Case, ResolutionPolicy, CasePlan, Classification, Decision, all enums
- `advocate/store.py` — SQLite persistence
- `advocate/channels/base.py` — Channel interface
- `advocate/channels/mock.py` — LLM role-plays stubborn support rep
- `advocate/agent/strategist.py` — Plans CasePlan from goal+policy (LLM)
- `advocate/agent/classifier.py` — Classifies inbound replies (LLM)
- `web/index.html` — Dashboard with case list, timeline, approve/reject
- `server.py` — HTTP server shell
- `examples/` — refund_case.json, subscription_cancel_case.json

## What needs to be BUILT/MODIFIED

### Task 1: `advocate/agent/negotiator.py`
Pure deterministic `decide(classification, policy, escalation_count) → Decision`.
Rules:
- FINAL_RESOLUTION → MARK_RESOLVED
- INFO_REQUEST → PROVIDE_INFO
- DENIAL → ESCALATE (if budget) else MARK_DENIED
- DEFLECTION/dark-pattern → ESCALATE (if budget) else PAUSE_FOR_APPROVAL
- UNKNOWN → PROVIDE_INFO
- OFFER with forbidden kind → COUNTER_OFFER
- OFFER >= min_acceptable → ACCEPT
- OFFER below + ask_below_threshold → PAUSE_FOR_APPROVAL
- OFFER below otherwise → COUNTER_OFFER
- Cancellation goal (target=0, min=0): money/discount offers are retention bait → ESCALATE/PAUSE

### Task 2: `advocate/agent/orchestrator.py`
`resolve_case(case_id, db_path)` — the durable loop:
1. Load case, init LLM + channel
2. Handle resume if NEEDS_APPROVAL
3. PLAN → make_plan() → status=PLANNED → save
4. OPEN → channel.open_case() → add agent message → status=OPEN → save
5. LOOP (cap MAX_TURNS=12): receive → classify → decide → _apply_decision → persist each step
6. Handle errors gracefully

`_apply_decision(case, decision, channel, llm, store) → bool`:
- PROVIDE_INFO → compose & send info message
- COUNTER_OFFER → compose & send counter
- ESCALATE → send escalation step, escalation_count += 1
- ACCEPT → status=RESOLVED, set outcome
- MARK_RESOLVED → status=RESOLVED
- MARK_DENIED → status=DENIED
- ABANDON → status=ABANDONED
- PAUSE_FOR_APPROVAL → status=NEEDS_APPROVAL, return True

### Task 3: Wire `server.py`
- Uncomment `resolve_case` import and call in `run_agent()`
- Fix `/approve` endpoint to re-invoke `run_agent` after human decision so the case resumes

## Key Data Types (from models.py)
```python
CaseStatus: new, planned, open, waiting, needs_approval, resolved, denied, abandoned
MessageType: offer, info_request, deflection, denial, final_resolution, unknown
ActionType: provide_info, counter_offer, accept, escalate, pause_for_approval, mark_resolved, mark_denied, abandon
OutcomeKind: refund, replacement, store_credit, apology, none
```

## Tech Stack
- Python 3.8+ (stdlib only — no pip install needed for core)
- SQLite via stdlib sqlite3
- LLM via urllib (no openai SDK)
- Vanilla HTML/JS dashboard

## Running
```bash
cp .env.example .env  # add your API key
python3 server.py     # http://localhost:8000
```

## LLM Configuration (.env)
```
ADVOCATE_LLM_PROVIDER=groq
ADVOCATE_LLM_API_KEY=gsk_...
ADVOCATE_LLM_MODEL=llama-3.3-70b-versatile
```

## Testing
- Click "refund example" → Start Case → watch the negotiation
- Click "cancel example" → Start Case → verify retention bait is escalated
- Test approve/reject flow when NEEDS_APPROVAL fires

## Enhancement Ideas (after core works)
- Add case analytics (turns, dark patterns caught, escalation steps)
- Add more example cases (billing dispute, warranty)
- Improve dashboard UI (decision trail, offer history chart)
- Add case outcome summary generation
- Update README with your changes
