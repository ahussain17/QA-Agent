# P2P QA Agent

This project is a runnable, evidence-driven QA agent for an unknown Purchase-to-Pay API. It discovers OpenAPI at runtime, finds safe seed data and SKU/item identifiers, executes an exact-boundary happy path, attacks seven financial controls in isolated scenarios, and writes a validated JSON report.

The default target is `http://localhost:8000`. Non-loopback targets are blocked unless `--allow-nonlocal` is explicitly supplied.

## Install

Python 3.11 or newer is required.

Windows PowerShell:

```powershell
cd "C:\path\to\Interview Challenge QA"
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS/Linux:

```bash
cd "/path/to/Interview Challenge QA"
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

LLM reasoning is optional at runtime so discovery and deterministic checks still work in offline CI. To enable agent planning, schema interpretation, validation-error repair, and evidence explanations, set both `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env`. There is deliberately no hard-coded model identifier. Credentials are read by the SDK and are never copied into evidence. The Responses API integration follows the official [OpenAI function-calling flow](https://developers.openai.com/api/docs/guides/function-calling): the model receives only a local, read-only `inspect_operation` tool—never shell or unrestricted network access.

## Commands

Start the included local P2P API in the first terminal:

```text
python -m p2p_demo --profile secure
```

Its Swagger documentation is available at `http://localhost:8000/docs`. In a second terminal, run the QA agent:

```text
python -m p2p_qa discover --base-url http://localhost:8000
python -m p2p_qa run --base-url http://localhost:8000 --mode happy
python -m p2p_qa run --base-url http://localhost:8000 --mode adversarial
python -m p2p_qa run --base-url http://localhost:8000 --mode all --report reports/qa-report.json
```

The API stores data in memory and resets whenever it restarts. To demonstrate that the judge detects real failures, restart it with an intentional vulnerability:

```text
python -m p2p_demo --profile vulnerable-overpayment
python -m p2p_demo --profile vulnerable-gl
python -m p2p_demo --profile vulnerable-all
```

Run only one API process/profile at a time.

All run options:

```text
--verbose
--timeout FLOAT
--max-actions INTEGER
--seed INTEGER
--fail-on-inconclusive
--allow-nonlocal
```

Exit codes are `0` for no demonstrated breach/failure, `1` for a breached control or failed happy path, `2` for runtime errors, and `3` when `--fail-on-inconclusive` is set and evidence is inconclusive.

## 60-second demo

Terminal 1:

```powershell
python -m p2p_demo --profile secure
```

Terminal 2:

```powershell
python -m pip install -e ".[dev]"
python -m p2p_qa discover --base-url http://localhost:8000
python -m p2p_qa run --base-url http://localhost:8000 --mode all --report reports/qa-report.json --verbose
python -m pytest
Get-Content reports\qa-report.json
```

On macOS/Linux, the final command is `cat reports/qa-report.json`.

## Architecture

- `SchemaExplorer` downloads and hashes OpenAPI, resolves local `$ref` values, indexes operations, probes only parameter-free GETs, and searches schemas/examples/enums/seed responses for SKU or item identifiers.
- `ApiClient` validates the origin, accepts only root-relative paths, refuses redirects, enforces timeouts/action budgets, preserves exact Decimal JSON numbers, and records sanitized evidence.
- `ExplorationAgent` obtains an active vendor and discovered SKU, then creates, submits, receives, invoices at the exact received-value boundary, matches, and approves.
- `AdversarialAgent` creates isolated scenarios for unmatched approval, excess receipt, overpayment, inactive vendors, partial receipts, duplicates, and GL balance.
- `InvariantJudge` performs all monetary calculations and verdicts with `decimal.Decimal`; model text cannot override it.
- `ReportWriter` validates the Pydantic report model before writing JSON and prints a compact terminal table.

LLM reasoning is used for plan formation, interpreting operation schemas through a read-only tool, proposing schema-constrained repairs after 4xx validation feedback, and concise failure explanation. The deterministic fallback produces a transparent plan when no model is configured. LLM output is advisory: request fields are checked against OpenAPI and all financial verdicts remain code-based.

## Evidence and control semantics

Every HTTP step records its purpose, timestamp, sanitized request/response, duration, expectation, observation, interpretation, assertions, attempt number, and verdict. Status codes alone are insufficient: a rejected negative operation is `HELD` only when observable before/after state also remained unchanged. Missing mutation evidence becomes `INCONCLUSIVE`, never `HELD`.

Received value is calculated as `sum(received_quantity * agreed_unit_price)` with `Decimal`. Overpayment is attacked one cent above received value; the happy path checks the exact boundary. Partial receipt requires numeric proof plus an explicit partial/mismatch indication; a generic rejection is `INCONCLUSIVE`. GL evidence is searched in approval output and discovered journal/ledger GET operations; unavailable lines are `INCONCLUSIVE`.

## Schema adaptation and safety

Payloads are synthesized only from request-schema properties, required fields, enums, defaults, and examples. A 4xx validation response can trigger at most two schema-repair attempts; 5xx responses are never blindly retried. If OpenAPI is unavailable, discovery limits itself to documented safe GET behavior and refuses to invent mutation payloads. If no SKU is found, dependent tests are explicitly `INCONCLUSIVE` and the report lists every inspected source.

Safety restrictions include loopback-only execution by default, exact-origin URL checks, root-relative request paths, no redirect following, bounded actions, bounded timeouts, no shell/network tools for the LLM, secret-key/header redaction, and unique `qa-<UTC>-<suffix>` identifiers.

## Tests and sample report

```text
python -m pytest
```

The 27-test suite uses `httpx.MockTransport` plus HTTPX's in-process ASGI transport. It covers happy, adversarial-only, and combined execution; every control; vulnerable overpayment and unbalanced-GL breaches; missing GL evidence; bounded recovery; redaction; serialization; URL safety; exit codes; invalid amounts; repeat behavior; exact decimal boundaries; and concurrent duplicate creation. `reports/sample-report.json` is generated from the mocked model and validates against the same Pydantic report model used in production.

## Role alongside regression tests

Traditional regression tests are fast, fixed, and ideal for known contracts. This agent complements them by rediscovering the live contract, selecting available records, adapting within bounded rules, and probing cross-step financial behavior that can drift even while endpoint-level tests pass. It is not a replacement for unit, contract, migration, concurrency, or load tests.

For scheduled production monitoring, use a dedicated tenant and least-privilege credentials, add an explicit allowlist and mutation window, idempotency keys and cleanup/retention policies, rate limits, distributed tracing, alert deduplication, encrypted artifact storage, approval for high-risk writes, SLO-aware retry policy, and a read-only/canary mode. Production observability must expose immutable invoice and journal audit trails; without them, non-mutation and GL checks remain inconclusive.

## Known limitations

- An API that exposes no valid SKU through OpenAPI or safe GET data cannot be mutated safely.
- A rejected request without observable before/after state cannot prove non-mutation.
- GL balance cannot be proven without approval or journal/ledger lines.
- Runtime schema synthesis handles common object/array/enum/default patterns; highly polymorphic or domain-specific schemas may require the bounded LLM repair path or a new deterministic semantic alias.
