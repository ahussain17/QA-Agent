# Builder Interview Challenge — QA Track
**Role:** Builder 2 (Agentic QA & Engineering Acceleration)  
**Format:** 60-minute live vibe-coding session with the interviewer  
**Tools:** Use whatever you want — Cursor, Copilot, ChatGPT, Claude. That's the point.

---

## The Scenario

Your team's Dev Builder just shipped a Purchase-to-Pay (P2P) API for a building materials ERP. It handles vendors, purchase orders, goods receipts, invoice matching, and GL posting. The code is running. Nobody has written a single test.

Your mission: don't write a test suite — **build a QA agent** that can autonomously explore the API, find the edge cases that would cause a financial error, and report what it found.

This is not a Selenium job. This is an agentic verification problem.

---

## The API Under Test

A running P2P API is available at `http://localhost:8000` (the interviewer will spin it up). Full endpoint list:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/vendors` | List all vendors |
| `POST` | `/vendors` | Create vendor |
| `POST` | `/purchase-orders` | Create draft PO with line items |
| `POST` | `/purchase-orders/{id}/submit` | Submit PO |
| `POST` | `/purchase-orders/{id}/receive` | Record goods receipt (partial ok) |
| `GET` | `/purchase-orders/{id}` | PO detail + receipt status |
| `POST` | `/invoices` | Create invoice against a PO |
| `POST` | `/invoices/{id}/match` | 3-way match (PO + receipt + invoice) |
| `POST` | `/invoices/{id}/approve` | Approve matched invoice + post to GL |
| `GET` | `/vendors/{id}/exposure` | Total open AP liability |

---

## The Financial Rules (what you're protecting)

These are the invariants an auditor would care about. Your agent must catch violations:

1. **Overpayment protection** — Invoice amount must never exceed the value of goods actually received
2. **3-way match gate** — An invoice cannot be approved unless it has passed matching
3. **Partial receipt flag** — If goods are only partially received, the invoice match should surface this, not silently pass
4. **Inactive vendor gate** — No new POs against inactive vendors
5. **GL balance** — Every approved invoice must produce a balanced GL entry (debits = credits)
6. **Duplicate invoice detection** — Same `invoice_number` from same vendor should be rejected

---

## Your Mission (60 min core)

### Core (must ship)

**1. An Exploration Agent**  
Build an agent that, given nothing but the base URL, can:
- Discover what vendors and SKUs exist
- Construct a valid end-to-end P2P workflow autonomously (create vendor → PO → receipt → invoice → match → approve)
- Log each step with the request, response, and its own interpretation of whether the outcome was correct

Use LangChain, LangGraph, raw OpenAI tool-calling, or whatever you prefer. The agent should be runnable from the CLI.

**2. An Adversarial Agent**  
A second agent (or mode) that deliberately tries to break the financial rules:
- Try to approve an unmatched invoice
- Try to receive more qty than ordered
- Try to create an invoice for more than the received goods value
- Try to use an inactive vendor

For each attempt, verify that the API correctly rejected it and log whether the guardrail held.

**3. A Judge Report**  
After both agents run, produce a structured JSON report:
```json
{
  "happy_path": { "status": "PASS|FAIL", "steps": [...] },
  "adversarial": [
    { "rule": "overpayment_protection", "status": "HELD|BREACHED", "evidence": {...} }
  ],
  "summary": "..."
}
```

### Stretch Goals (if time allows)

- **Self-healing**: If an endpoint returns an unexpected schema, the agent should attempt to adapt its tool call rather than crash
- **Synthetic data stress test**: Generate 50 randomized POs with varying partial receipts and mismatched invoice amounts; run the adversarial agent against all of them and surface the failure rate
- **DSPy prompt optimization**: Use DSPy to optimize the prompt that classifies whether an API response represents a financial integrity violation

---

## What We're Watching For

- **Agent architecture** — Is the exploration agent reactive or does it have a plan? How does it handle unexpected API responses?
- **Financial intuition** — Do you instinctively know which rules are the dangerous ones? Do you probe for off-by-one errors and floating point issues?
- **Adversarial creativity** — Does your red-team agent find edge cases we didn't spec? (e.g., zero-amount invoices, negative quantities, race conditions)
- **Eval rigor** — Is your Judge agent just checking status codes, or is it actually verifying business logic?
- **AI leverage** — Are you using the LLM to reason about failures, or just as a glorified HTTP client?
- **Shipping** — A runnable agent with actual output by the end of the session.

---

## Setup Notes

- The interviewer will provide a running API + seed data
- You can use any Python libraries — `langchain`, `openai`, `httpx`, `pydantic` are all fair game
- No UI needed — CLI output and the JSON report are the deliverable

---

## Conversation Starters (we'll use these to probe during the session)

Be ready to discuss:
- How would you make this QA agent **self-updating** — i.e., when the API schema changes, the agent discovers and adapts without manual updates to your code?
- What's the difference between testing this API with a traditional test suite vs. an adversarial agent? When does each approach win?
- If you had to add **continuous monitoring** so this agent runs against production on a schedule, what changes?
