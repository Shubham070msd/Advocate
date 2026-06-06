# Advocate — Autonomous Consumer-Advocacy Agent

Theme 3: Agentic Productivity · AI-Powered Consumer Advocacy

**Advocate** is an autonomous agent that resolves consumer issues on a user's
behalf. A user declares a desired outcome — *"get a full refund for order #4821;
it arrived broken"* — plus a **Resolution Policy** (acceptance thresholds, hard
"never" rules, an escalation budget). The agent then:

- **Plans** a strategy from the goal and policy.
- **States the case** to a support channel.
- **Converses, waits, and persists** — interpreting each reply, countering
  lowball offers, escalating around deflection and retention dark-patterns, and
  pausing for human approval at a real guardrail —

…until the issue is **resolved**, **denied**, or its **escalation budget runs
out**.

---

## Core Innovation — The Trust Boundary

Most agent systems let the LLM make all decisions. Advocate draws a hard line:

> **The LLM perceives and phrases. Deterministic code decides.**

The Strategist and Classifier use an LLM to understand context and draft
messages. But the **Negotiator** — the component that makes consequential
decisions (accept money? cross a guardrail? give up?) — is **pure deterministic
Python**. No LLM, no I/O, no randomness.

A model hallucination can **never**:
- Silently accept a payment below the user's threshold
- Accept a forbidden outcome (like store credit when the user wants cash)
- Exceed the escalation budget
- Fall for a retention dark pattern on a cancellation goal

---

## Technology Stack

| Layer | Choice |
|---|---|
| **Language** | Python 3.8+ — **standard library only** (zero `pip install` required) |
| **Web / API server** | stdlib `http.server` (`ThreadingHTTPServer`), JSON over HTTP |
| **Persistence** | SQLite (`sqlite3`) — WAL mode, one row per Case as JSON blob; survives restarts |
| **LLM provider** | Groq (Llama 3.3 70B Versatile) — free tier, fast inference |
| **LLM client** | Provider-agnostic, built on `urllib`. Works with OpenAI, Anthropic Claude, Groq, DeepSeek, and Ollama |
| **Frontend** | Single-file vanilla HTML/JS dashboard (`web/index.html`) that polls the API every 1.5s |

---

## Architecture

```
                           ┌─────────────────────────────────────────────┐
   Browser (web/index.html)│  Dashboard: create case, live timeline,      │
   ──────────────────────► │  approve / reject (polls the API every 1.5s) │
                           └───────────────┬─────────────────────────────┘
                                           │ HTTP (JSON)
                           ┌───────────────▼─────────────────────────────┐
   server.py (stdlib http) │  GET/POST /api/cases · run_agent(case_id)    │  ← spawns a
   ──────────────────────► │  serves UI + storage-backed API              │    background thread
                           └───────────────┬─────────────────────────────┘
                                           │
                  ┌────────────────────────▼───────────────────────────────┐
                  │  advocate/agent/orchestrator.py   (THE DURABLE LOOP)    │
                  │                                                          │
                  │   plan ─► open ─► [ receive ─► classify ─► decide ─►     │
                  │                     act ─► persist ] ─► terminal/pause   │
                  └───┬──────────────┬──────────────┬─────────────┬─────────┘
                      │              │              │             │
              ┌───────▼──┐   ┌───────▼─────┐  ┌─────▼──────┐ ┌────▼────────────┐
              │strategist│   │ classifier  │  │ negotiator │ │   Channel       │
              │  (LLM)   │   │   (LLM)     │  │  (CODE —   │ │  (send/receive) │
              │ →CasePlan│   │→Classifcatn │  │  no LLM!)  │ │ Mock | Web | …  │
              └──────────┘   └─────────────┘  │ →Decision  │ └────────────────┘
                                               └────────────┘
                                    │ persist after every step
                           ┌────────▼─────────┐      ┌──────────────────┐
                           │ store.py (SQLite) │      │  llm.py (provider│
                           │  Case JSON blobs  │      │  -agnostic chat) │
                           └──────────────────┘      └──────────────────┘
```

**Key design seam:** the LLM *understands and phrases* (strategist, classifier),
but the **consequential decision** — accept money? cross a guardrail? give up? —
is made by **deterministic code** in `negotiator.py`. A model mistake can never
silently approve a payment.

---

## Data Flow

**Status state machine:**

```
NEW ─► PLANNED ─► OPEN ─►(reply)─► [ negotiating ] ──► RESOLVED
                          │                       └──► DENIED
                          ├──► NEEDS_APPROVAL ─(human)─┤      (escalation budget
                          └──► WAITING (parked) ───────┘       exhausted) ─► ABANDONED
```

**Step by step:**

1. The user fills the dashboard form and submits → `POST /api/cases` stores the
   Case and spawns `run_agent(case_id)` on a background thread.
2. The orchestrator **plans** (`strategist.make_plan` → `CasePlan`), **opens**
   the channel, and sends the opening statement.
3. Each turn: **receive** a reply → **classify** it (`classifier.classify` →
   `Classification`) → **decide** the next action (`negotiator.decide` →
   `Decision`) → **act** (send a message / update status) → **persist**
   (`store.save`).
4. The dashboard polls every ~1.5 s and animates the timeline as the Case
   advances.
5. At a guardrail the agent sets `NEEDS_APPROVAL` and pauses. The human clicks
   approve / reject → `POST /api/cases/<id>/approve` records the decision and
   **resumes** the agent on a new background thread.
6. The loop ends at a terminal state: `RESOLVED`, `DENIED`, or `ABANDONED`.

---

## Project Structure

```
Advocate/
├── server.py                     MODIFIED     web server + agent wiring + approve/resume
├── web/index.html                PROVIDED     dashboard (case list, timeline, approve)
├── examples/                     PROVIDED     sample cases (refund, subscription cancel)
├── advocate/
│   ├── llm.py                    PROVIDED     provider-agnostic LLM client
│   ├── models.py                 PROVIDED     Case / Policy / Plan / Classification / Decision
│   ├── store.py                  PROVIDED     SQLite Case store (save/get/list)
│   ├── channels/
│   │   ├── base.py               PROVIDED     Channel interface (send/receive)
│   │   ├── mock.py               PROVIDED     simulated adversarial support rep
│   │   ├── web_playwright.py     STUB         real browser channel (future scope)
│   │   └── email_channel.py      STUB         real email channel (future scope)
│   └── agent/
│       ├── strategist.py         PROVIDED     plan a CasePlan from goal+policy (LLM)
│       ├── classifier.py         PROVIDED     interpret one reply → Classification (LLM)
│       ├── negotiator.py         ✅ BUILT      deterministic decision engine (162 lines)
│       └── orchestrator.py       ✅ BUILT      durable resolution loop (409 lines)
├── CLAUDE.md                     ADDED        project context for Claude Code
├── .env.example                  PROVIDED     LLM configuration template
└── requirements.txt              PROVIDED     (stdlib only — zero deps)
```

---

## What Was Built

### Negotiator — `advocate/agent/negotiator.py` (162 lines)

The **deterministic decision engine** — the agent's trust boundary. A pure
function `decide(classification, policy, escalation_count) → Decision` that maps
every inbound message type to exactly one action:

| Message Type | Action | Condition |
|---|---|---|
| `FINAL_RESOLUTION` | `MARK_RESOLVED` | Counterparty confirmed resolution |
| `INFO_REQUEST` | `PROVIDE_INFO` | They need more details from us |
| `UNKNOWN` | `PROVIDE_INFO` | Unclear reply; ask to clarify |
| `DENIAL` | `ESCALATE` / `MARK_DENIED` | Escalate if budget remains; else deny |
| `DEFLECTION` / dark-pattern | `ESCALATE` / `PAUSE_FOR_APPROVAL` | Escalate if budget; else ask user |
| `OFFER` (forbidden kind) | `COUNTER_OFFER` | Never accept forbidden outcomes |
| `OFFER` (≥ min amount) | `ACCEPT` | Meets minimum acceptable amount |
| `OFFER` (< min, ask user) | `PAUSE_FOR_APPROVAL` | Below threshold; pause for human |
| `OFFER` (< min, auto) | `COUNTER_OFFER` | Auto-counter toward target |
| `OFFER` (cancellation goal) | `ESCALATE` | Money/discount = retention bait |

**Key constraints enforced:**
- **No LLM** — pure, auditable Python. Same inputs → same Decision, every time.
- Never returns `ACCEPT` when `offer_kind` is in `policy.forbidden_outcomes`.
- Never returns `ACCEPT` for an amount below `min_acceptable_amount`.
- Escalates only while `escalation_count < escalation_budget`; else pauses or denies.
- A cancellation goal (`target_amount == 0`, `min_acceptable_amount == 0`) never
  accepts a money/discount offer — treats it as retention bait and escalates.

### Orchestrator — `advocate/agent/orchestrator.py` (409 lines)

The **durable, resumable resolution loop** that ties everything together:

- **`resolve_case(case_id, db_path)`** — runs (or resumes) the full lifecycle.
- **`_apply_decision(case, decision, channel, llm, store)`** — maps each action
  to its side effects: compose & send messages, update status, set outcomes.
- **`_compose_message(case, action, llm)`** — uses the LLM only to *phrase*
  outbound messages. The action was already decided by the negotiator; the LLM
  never overrides it. Includes hardcoded fallback messages for resilience.
- **`_handle_resume(case, channel, llm, store)`** — resumes a case after the
  human clicked approve or reject from the dashboard.

**Key properties:**
- Persists the Case after **every** step so the dashboard animates live.
- Increments `escalation_count` on `ESCALATE`; uses the strategist's escalation
  ladder for progressively firmer messages.
- `MAX_TURNS = 12` safety cap so a buggy loop can never run forever.
- One bad LLM/channel reply is caught and recorded, not fatal to the case.
- 0.8s delay between turns for visual dashboard animation.

### Server Wiring — `server.py` (modified)

- Enabled the `run_agent()` → `resolve_case()` hand-off on a background thread.
- Passes counterparty mode (`llm` or `scripted`) through `case.context`.
- Fixed the `/approve` endpoint to record the human's decision **and** spawn a
  new background thread to resume the agent.

---

## Setup and Run

**Requirements:** Python 3.8+. **Zero third-party dependencies.**

```bash
# 1. Clone the repository
git clone https://github.com/Shubham070msd/Advocate.git
cd Advocate

# 2. Configure your LLM provider
cp .env.example .env
# Edit .env — Groq is free and fast (console.groq.com):
#   ADVOCATE_LLM_PROVIDER=groq
#   ADVOCATE_LLM_API_KEY=gsk_your_key_here
#   ADVOCATE_LLM_MODEL=llama-3.3-70b-versatile

# 3. Run
python3 server.py           # → http://localhost:8000
```

**Switching providers** is an `.env` change only:

```bash
# Groq (used in this build — free tier)
ADVOCATE_LLM_PROVIDER=groq       ADVOCATE_LLM_API_KEY=gsk_...     ADVOCATE_LLM_MODEL=llama-3.3-70b-versatile
# OpenAI
ADVOCATE_LLM_PROVIDER=openai     ADVOCATE_LLM_API_KEY=sk-...      ADVOCATE_LLM_MODEL=gpt-4o-mini
# Anthropic Claude (adapter built in)
ADVOCATE_LLM_PROVIDER=anthropic  ADVOCATE_LLM_API_KEY=sk-ant-...  ADVOCATE_LLM_MODEL=claude-sonnet-4-6
# Ollama (local, free; run `ollama serve` first)
ADVOCATE_LLM_PROVIDER=ollama     ADVOCATE_LLM_MODEL=llama3.1
```

**Reset between test runs:**

```bash
rm advocate.db && python3 server.py
```

---

## Dataset Schema Reference

A **Case** is the unit of work:

| Field | Type | Meaning |
|---|---|---|
| `goal` | string | The desired outcome, in plain language. **Required.** |
| `context` | object | Free-form facts (order id, account email, amounts, dates). |
| `evidence` | string[] | Filenames / descriptions of supporting evidence. |
| `policy` | object | The `ResolutionPolicy` (below) — the agent's mandate. |

**ResolutionPolicy** — the guardrails the negotiator never crosses:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `currency` | string | `"INR"` | Currency label for amounts. |
| `min_acceptable_amount` | number | `0` | Auto-accept an acceptable-kind offer at or above this. |
| `target_amount` | number | `0` | What counter-offers aim for. `0` ⇒ a non-money / cancellation goal. |
| `forbidden_outcomes` | string[] | `[]` | Outcome kinds to **never** accept (e.g. `["store_credit"]`). |
| `ask_below_threshold` | bool | `true` | Below threshold: `true` → ask the user; `false` → auto-counter. |
| `escalation_budget` | int | `2` | How many times the agent may escalate before pausing / giving up. |
| `notes` | string | `""` | Extra instructions surfaced to the LLM agents. |

**Enums:**

| Enum | Values |
|---|---|
| `CaseStatus` | `new`, `planned`, `open`, `waiting`, `needs_approval`, `resolved`, `denied`, `abandoned` |
| `MessageType` | `offer`, `info_request`, `deflection`, `denial`, `final_resolution`, `unknown` |
| `ActionType` | `provide_info`, `counter_offer`, `accept`, `escalate`, `pause_for_approval`, `mark_resolved`, `mark_denied`, `abandon` |
| `OutcomeKind` | `refund`, `replacement`, `store_credit`, `apology`, `none` |

---

## Testing Guide

### Test 1 — Refund Case
1. Click **"refund example"** → **Start Case**
2. Watch Advocate negotiate a ₹3,200 refund for a broken ceramic dinner set
3. The agent should: open → handle deflection → counter lowball → recover full amount
4. **Verify:** store credit offers are never accepted (forbidden outcome)

### Test 2 — Subscription Cancellation
1. Click **"cancel example"** → **Start Case**
2. Watch Advocate handle retention dark patterns
3. The agent should: recognize discount offers as retention bait → escalate
4. **Verify:** money/discount offers are NOT accepted (cancellation goal)

### Test 3 — Human-in-the-Loop
1. Start a refund case with `min_acceptable_amount` set higher than likely offers
2. When `NEEDS_APPROVAL` fires, click **Reject** → agent pushes back
3. Or click **Approve** → agent accepts the offer
4. **Verify:** the loop resumes correctly after both approve and reject

---

## Future Scope

**Near-term:**
- Real channel connectors — Playwright browser automation for live chat widgets
- IMAP/SMTP email channel for email-based disputes
- Evidence attachment handling — automatic photo/invoice extraction
- Slack/Teams notifications when a case reaches `NEEDS_APPROVAL`

**Medium-term:**
- Multi-company playbook library — company-specific escalation strategies
- Natural-language policy entry — parse user intent into structured `ResolutionPolicy`
- Chargeback and regulatory filing stubs when negotiation fails
- Voice channel support — IVR navigation and call-transcript classification

**Long-term:**
- Org-wide consumer intelligence — aggregate outcomes across thousands of disputes
- Legal document generation — small-claims filings, GDPR erasure requests
- Cross-jurisdiction policy engine — apply country-specific consumer protection rules
- Confidence-gated auto-accept for offers clearly exceeding the target

---

## Author

**Manjunath Huddar**

| | |
|---|---|
| **GitHub** | [github.com/Shubham070msd](https://github.com/Shubham070msd) |
| **Portfolio** | [manjunath-07.vercel.app](https://manjunath-07.vercel.app) |
| **LinkedIn** | [linkedin.com/in/manjunath-huddar-devops](https://linkedin.com/in/manjunath-huddar-devops) |

Built for **HackerEarth × Microsoft Build AI Day** — Theme 3: Agentic Productivity
