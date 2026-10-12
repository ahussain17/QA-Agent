# P2P QA Agent — Interview Guide

This guide is based on the source code and report artifacts in this repository. It deliberately separates what the project implements today from ideas that would belong in a production version. The optional LLM does **not** dispatch the workflow: Python selects and executes the HTTP actions, while the LLM can suggest a plan for the report, propose a bounded payload repair, and explain evidence. All financial calculations and verdicts are deterministic.

## 1. Project overview and business purpose

The project contains two Python packages with different jobs:

- `p2p_qa` is the system that performs QA. It discovers a Purchase-to-Pay API, creates test transactions, attacks financial controls, judges the evidence, and writes a JSON report.
- `p2p_demo` is an in-memory FastAPI system under test. It makes the interview demonstration self-contained. In a real engagement, the company's P2P API replaces this package; the QA agent remains the product being demonstrated.

Purchase-to-Pay systems turn purchasing activity into liabilities and accounting entries. A defect can approve an invoice before matching, pay for goods never received, accept a duplicate invoice, or post an unbalanced journal. Those are financially meaningful failures, so a simple “endpoint returned 200” assertion is not enough. This project combines contract discovery, stateful API workflows, deliberately invalid actions, before/after observation, exact decimal arithmetic, and evidence-rich reporting.

The business value is:

1. Find integration and control failures that isolated endpoint tests can miss.
2. Adapt to a changing OpenAPI contract within explicit bounds.
3. Produce an auditable explanation of what was sent, observed, calculated, and concluded.
4. Distinguish a proven control from a mere rejection and distinguish missing evidence from success.
5. Demonstrate a safe agentic pattern in which an LLM can assist but cannot override financial rules.

## 2. Verified repository structure

Generated caches and bytecode are omitted. The important checked-in and generated artifacts are:

```text
Interview Challenge QA/
├── .env.example
├── Interview Challenge QA.md
├── README.md
├── pyproject.toml
├── docs/
│   └── INTERVIEW_GUIDE.md
├── reports/
│   ├── live-happy-report.json
│   ├── local-secure-report.json
│   ├── local-vulnerable-gl-report.json
│   ├── local-vulnerable-overpayment-report.json
│   ├── qa-report.json
│   └── sample-report.json
├── src/
│   ├── p2p_qa/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── agents.py
│   │   ├── cli.py
│   │   ├── client.py
│   │   ├── judge.py
│   │   ├── llm.py
│   │   ├── models.py
│   │   ├── payloads.py
│   │   ├── reporting.py
│   │   ├── runner.py
│   │   ├── schema.py
│   │   └── utils.py
│   └── p2p_demo/
│       ├── __init__.py
│       ├── __main__.py
│       └── app.py
└── tests/
    ├── conftest.py
    ├── test_demo_api.py
    ├── test_judge.py
    ├── test_recovery_security.py
    ├── test_reporting.py
    └── test_workflow.py
```

There is no `main.py` file in either package. Do not mention one in an interview.

### Actual entry points

| How it is started | Actual path | Meaning |
|---|---|---|
| `python -m p2p_qa ...` | `p2p_qa/__main__.py` imports `app` from `cli.py` and calls it | Module execution through Typer |
| `p2p-qa ...` | `pyproject.toml` maps the installed script to `p2p_qa.cli:app` | Equivalent installed console command |
| `python -m p2p_demo ...` | `p2p_demo/__main__.py` calls its `main()` | Starts Uvicorn with an argparse-selected behavior profile |
| `p2p-demo-api ...` | `pyproject.toml` maps to `p2p_demo.__main__:main` | Equivalent installed demo-server command |
| `p2p_demo/app.py:app` | Module-level FastAPI application | Can be imported by an ASGI server, using `P2P_DEMO_PROFILE` |

`__main__.py` is a thin module launcher. `cli.py` owns the real user-facing commands. `runner.py` owns orchestration. There is no separate `main.py` abstraction.

## 3. Architecture in beginner-friendly terms

Think of the system as a careful test investigator:

1. **Read the map.** `SchemaExplorer` fetches `/openapi.json`, resolves local schema references, indexes operations, reads safe collections, and searches for a real SKU.
2. **Construct only permitted requests.** `PayloadFactory` uses OpenAPI properties, requirements, examples, defaults, enums, and a controlled context. It refuses to invent a mutation body when no request schema is available.
3. **Send bounded requests.** `ApiClient` restricts the target origin, uses a timeout and action budget, refuses redirects, sends exact decimal JSON, and captures sanitized evidence.
4. **Run a valid business workflow.** `ExplorationAgent` selects or creates an active vendor and runs PO → submit → receive → invoice → match → approve.
5. **Attack controls in isolated scenarios.** `AdversarialAgent` attempts unmatched approval, excess receipt, overpayment, inactive-vendor purchasing, a partial-receipt match, a duplicate invoice, and an unbalanced GL observation.
6. **Judge with code.** `InvariantJudge` uses `Decimal`, response state, and before/after evidence. Model prose cannot alter a verdict.
7. **Report.** Pydantic models validate the report; `ReportWriter` saves indented JSON and prints a summary table.

### Mermaid data-flow diagram

```mermaid
flowchart TD
    U[CLI command] --> C[cli.py / Typer]
    C -->|discover| AC1[ApiClient]
    AC1 --> SE1[SchemaExplorer.discover]
    SE1 --> DJ[DiscoveryReport JSON to terminal]

    C -->|run| R[runner.execute]
    R --> LR[make_reasoner]
    LR -->|key + model| OA[OpenAIReasoner]
    LR -->|otherwise| LF[LocalReasoner]
    R --> AC2[ApiClient: timeout, origin, action budget]
    AC2 --> SE2[SchemaExplorer]
    SE2 --> OAS[OpenAPI operations + safe data + SKU candidates]
    OAS --> EA[ExplorationAgent]
    OAS --> AA[AdversarialAgent]
    OA -. plan metadata / bounded repair / explanation .-> EA
    OA -. bounded repair / explanation .-> AA
    LF -. fixed plan / no repair / stock explanation .-> EA
    LF -. stock explanation .-> AA
    EA --> PF[PayloadFactory]
    AA --> PF
    PF --> AC2
    AC2 <--> API[(P2P API or p2p_demo)]
    EA --> HP[HappyPathReport]
    AA --> J[InvariantJudge]
    J --> CR[ControlResult list]
    HP --> S[reporting.summarize]
    CR --> S
    SE2 --> S
    S --> QR[Pydantic QAReport]
    QR --> RW[ReportWriter]
    RW --> FILE[reports/*.json]
    RW --> TERM[Rich terminal summary + exit code]
```

The dotted LLM arrows are advisory. The current Python methods determine the actions and their order.

## 4. Module-by-module reference

### `src/p2p_qa/__init__.py`

- **Responsibility:** package metadata.
- **Main members:** `__version__ = "0.1.0"`.
- **Input/output:** none at runtime beyond import metadata.
- **Called by:** normal package imports.
- **Workflow contribution:** identifies the package; it does not orchestrate tests.

### `src/p2p_qa/__main__.py`

- **Responsibility:** support `python -m p2p_qa`.
- **Main members:** imports `app` and calls it inside the standard `if __name__ == "__main__"` guard.
- **Input:** command-line arguments consumed by Typer.
- **Output:** whatever `cli.app` prints and its process exit code.
- **Calls:** `p2p_qa.cli:app`.
- **Workflow contribution:** executable bridge only.

### `src/p2p_qa/cli.py`

- **Responsibility:** command-line interface.
- **Main objects/functions:** Typer `app`, `discover()`, and `run_command()` registered as `run`.
- **Inputs:** `--base-url`, `--timeout`, `--max-actions`, and `--allow-nonlocal`; `run` also accepts `--mode`, `--report`, `--seed`, `--fail-on-inconclusive`, and `--verbose`.
- **Outputs:** discovery JSON, a Rich result table, optional complete report JSON, the saved report path, and an exit code.
- **Calls:** `load_dotenv`, `ApiClient`, `SchemaExplorer`, `runner.execute`, `ReportWriter`, and `exit_code`.
- **Workflow contribution:** validates the mode and exposes the application without embedding financial logic.
- **Important nuance:** `--verbose` prints more final output; it is not a live request trace.

### `src/p2p_qa/runner.py`

- **Responsibility:** construct one complete run.
- **Main functions:** `make_run_id()` and `execute()`.
- **Inputs:** target origin, mode, timeout, action count, optional seed, nonlocal permission, and optional `httpx` transport for tests.
- **Outputs:** a fully summarized `QAReport`.
- **Calls:** `make_reasoner`, `ApiClient`, `SchemaExplorer`, `ExplorationAgent`, `AdversarialAgent`, and `summarize`.
- **Workflow contribution:** discovery always runs first; then it invokes happy, adversarial, or both modes with one shared client and action budget.
- **Current behavior:** `--seed` is recorded and seeds Python's `random`, but the workflow currently does not make random choices. The run suffix uses `secrets`, so the seed does not make resource IDs reproducible.

### `src/p2p_qa/client.py`

- **Responsibility:** safe, bounded synchronous HTTP access and raw evidence capture.
- **Main classes:** `ApiResult`, `ActionLimitExceeded`, and `ApiClient`.
- **Inputs:** a validated base URL, a root-relative path, method, optional JSON-compatible body, timeout, and maximum action count.
- **Outputs:** `ApiResult` containing timestamp, method, full URL, sanitized request and response, status, duration, and optional transport error.
- **Called by:** discovery, agents, and tests.
- **Workflow contribution:** centralizes all API traffic and maintains `history`.
- **Safety:** refuses absolute/non-root-relative paths, verifies scheme/host/port, disables redirects, and defaults to loopback targets.
- **Boundary:** it catches `httpx.HTTPError`, but action-limit and serialization errors can propagate instead of becoming a report step.

### `src/p2p_qa/schema.py`

- **Responsibility:** OpenAPI and safe-data discovery.
- **Main members:** `FALLBACK_ENDPOINTS`, `Operation`, and `SchemaExplorer`.
- **Inputs:** `/openapi.json` and parameter-free GET responses.
- **Outputs:** normalized operations, request schemas, response metadata, schema hash, safe data, SKU candidates, limitations, HTTP evidence, and `DiscoveryReport`.
- **Called by:** CLI discovery, runner, agents, payload repair, and LLM summary generation.
- **Important methods:**
  - `discover()` fetches the contract, parses it, probes safe GETs, searches SKUs, and classifies discovery.
  - `resolve()` expands local `#/...` references and merges `allOf` properties and required fields.
  - `_parse_operations()` indexes GET/POST/PUT/PATCH/DELETE operations and JSON request schemas.
  - `_probe_safe_gets()` calls discovered parameter-free GET paths.
  - `_discover_skus()` searches OpenAPI and safe response data for common item/SKU keys, enums, and examples.
  - `find_operation()` offers bounded fuzzy lookup when the exact expected path is unavailable.
- **Important nuance:** discovery `PASS` means a nonempty OpenAPI document was fetched. It does not by itself prove that the required P2P operations, schemas, seed vendor, or SKU exist.

### `src/p2p_qa/payloads.py`

- **Responsibility:** schema-constrained request-body synthesis and deterministic repair.
- **Main class:** `PayloadFactory`.
- **Inputs:** a resolved request schema plus context such as vendor ID, SKU, quantity, price, amount, invoice number, run ID, or status.
- **Outputs:** a dictionary containing only schema-declared top-level properties, or a refusal/error when the schema is unavailable.
- **Called by:** `BaseAgent` and `AdaptiveRequester`.
- **Behavior:** supports object/array shapes, required fields, const/example/default/enum, first `oneOf`/non-null `anyOf`, primitive types, and semantic field aliases.
- **Repair:** reads structured validation errors for missing fields, rebuilds from the same schema, and adds absent schema-backed values.
- **Limit:** it does not fully implement every JSON Schema keyword, discriminator, regex, `multipleOf`, or complex nested repair.

### `src/p2p_qa/llm.py`

- **Responsibility:** keep model-provider behavior behind a narrow `Reasoner` interface.
- **Main members:** `Reasoner` protocol, `LocalReasoner`, `OpenAIReasoner`, and `make_reasoner()`.
- **Inputs:** discovered metadata, a failed schema-bound payload and validation response, or deterministic evidence requiring a short explanation.
- **Outputs:** plan records, a possible repaired dictionary, or short explanatory prose.
- **Called by:** runner and agents.
- **Actual model behavior:**
  - Selected only when both `OPENAI_MODEL` and `OPENAI_API_KEY` are present.
  - Uses the Responses API with `store=False`.
  - Receives one local read-only `inspect_operation(index)` function while planning.
  - Has no shell tool and no unrestricted network tool.
  - Falls back to `LocalReasoner` behavior on most call/parse failures.
- **Critical interview statement:** the plan is saved as report metadata, not executed as a dynamic policy. Fixed Python methods still choose every business action. The LLM cannot decide `HELD` or `BREACHED`.

### `src/p2p_qa/agents.py`

- **Responsibility:** stateful workflow execution and attack scenario construction.
- **Helpers:** `_records`, `_po_lines`, `_resource_id`, `_is_active`, `_path`, and `_step` normalize common API shapes and produce `StepEvidence`.
- **Main classes:**
  - `AdaptiveRequester`: initial request plus at most two repair retries. It never blindly retries a transport error or 5xx.
  - `WorkflowState`: accumulated vendor/PO/invoice IDs, responses, steps, and error.
  - `BaseAgent`: shared operation lookup, vendor selection/creation, state observation, and reusable setup flow.
  - `ExplorationAgent`: runs the positive end-to-end path and checks the observed exact-value boundary.
  - `AdversarialAgent`: runs seven fixed negative/control scenarios and delegates verdicts to the judge.
- **Inputs:** discovered operations/data/SKU, a reasoner, run ID, and business scenario quantities.
- **Outputs:** `HappyPathReport` or a list of `ControlResult` objects.
- **Called by:** `runner.execute()`.
- **Important boundary:** positive setup requests can use adaptive repair. Most direct attack requests do not; this avoids turning an intended negative test into an unbounded exploratory loop, but it means a validation 4xx should be interpreted carefully.

### `src/p2p_qa/judge.py`

- **Responsibility:** deterministic financial and state verdicts.
- **Main class:** `InvariantJudge`.
- **Inputs:** quantities/prices, status codes, match output, journal lines, and observable before/after state.
- **Outputs:** `Decimal` values, `AssertionResult`, or `ControlResult`.
- **Called by:** agents and unit tests.
- **Important methods:**
  - `received_value()` calculates `Σ(received quantity × agreed unit price)`.
  - `unchanged()` compares observable state exactly.
  - `rejection_control()` requires rejection plus unchanged state for `HELD`.
  - `overpayment()` evaluates the attack boundary, approval outcome, and exposure mutation.
  - `partial_receipt()` requires numeric partial receipt and explicit disclosure.
  - `gl_balance()` discovers journal lines and totals debit/credit fields with `Decimal`.
- **Core guarantee:** no LLM text can change these outcomes.

### `src/p2p_qa/models.py`

- **Responsibility:** Pydantic data contracts for evidence and the final report.
- **Main models:** `AssertionResult`, `StepEvidence`, `RunInfo`, `OperationInfo`, `DiscoveryReport`, `HappyPathReport`, `ControlResult`, `SummaryReport`, and `QAReport`.
- **Inputs:** Python values from the explorer, agents, judges, and runner.
- **Outputs:** validated in-memory models and JSON-safe dumps.
- **Called by:** nearly every package module and the tests.
- **Workflow contribution:** makes missing or invalid report sections fail validation before persistence. `passed=None` explicitly represents unavailable evidence.

### `src/p2p_qa/reporting.py`

- **Responsibility:** calculate the top-level result, serialize reports, print the table, and map outcomes to process exit codes.
- **Main members:** `summarize`, `ReportWriter.write`, `ReportWriter.terminal_summary`, and `exit_code`.
- **Inputs:** `QAReport`, output path, and optional `fail_on_inconclusive` policy.
- **Outputs:** `SummaryReport`, saved UTF-8 JSON, terminal output, and integer exit code.
- **Called by:** runner, CLI, and tests.
- **Workflow contribution:** applies a deterministic priority: failure/breach, error, inconclusive, then pass.

### `src/p2p_qa/utils.py`

- **Responsibility:** URL validation, redaction, decimal conversion, JSON traversal, field discovery, response decoding, stable hashing, and exact JSON encoding.
- **Main functions:** `validate_base_url`, `sanitize`, `jsonable`, `decimal`, `walk`, `find_first`, `response_json`, `stable_json`, and `exact_json_bytes`.
- **Inputs/outputs:** small generic values; these helpers return validated strings, redacted structures, `Decimal` values, traversal results, or JSON bytes.
- **Called by:** client, schema explorer, payload/judge/agent logic, and tests.
- **Financial safety:** `exact_json_bytes` serializes `Decimal` directly and rejects Python `float` values.

### `src/p2p_demo/__main__.py`

- **Responsibility:** start the demonstration API.
- **Main function:** `main()` parses `--host`, `--port`, `--profile`, and `--reload`, then calls `uvicorn.run(create_app(profile), ...)`.
- **Input/output:** CLI settings in; a running ASGI server and logs out.
- **Called by:** `python -m p2p_demo` or `p2p-demo-api`.

### `src/p2p_demo/app.py`

- **Responsibility:** provide a disposable in-memory P2P API for demonstration and integration tests.
- **Pydantic inputs:** `VendorCreate`, `POLine`, `PurchaseOrderCreate`, `ReceiptLine`, `GoodsReceipt`, and `InvoiceCreate`.
- **State:** `DemoState` holds two vendors, two active items, POs, invoices, and journals behind an `RLock`.
- **Factory:** `create_app(profile)` returns FastAPI configured as `secure`, `vulnerable-overpayment`, `vulnerable-gl`, or `vulnerable-all`.
- **Endpoints:** health, items, vendor list/create/exposure, PO list/create/submit/receive/detail, invoice list/create/detail/match/approve, and journal lookup.
- **Outputs:** FastAPI generates `/openapi.json` and `/docs`; monetary response values are formatted strings.
- **Profiles:**
  - `secure`: enforces all implemented demo controls.
  - `vulnerable-overpayment`: allows an invoice above received value and allows the related partial PO to match.
  - `vulnerable-gl`: posts a journal with credit one cent below debit.
  - `vulnerable-all`: disables several business gates and also posts an unbalanced journal.
- **Limit:** all data is process memory. Restarting resets it; this is not a production service or database model.

### Configuration, reports, and tests

| File | Responsibility and use |
|---|---|
| `pyproject.toml` | Requires Python 3.11+, declares dependencies, test configuration, package paths, and both console scripts. |
| `.env.example` | Documents optional `OPENAI_API_KEY`, `OPENAI_MODEL`, and `P2P_DEMO_PROFILE`; contains no credential. |
| `README.md` | Compact installation, architecture, safety, command, and demo reference. |
| `Interview Challenge QA.md` | Original challenge text/reference; it is not imported or executed. |
| `tests/conftest.py` | Disables live LLM credentials automatically and supplies a stateful `httpx.MockTransport` plus OpenAPI fixture. |
| `tests/test_workflow.py` | Happy path, all seven controls, and direct breach/inconclusive judge demonstrations. |
| `tests/test_judge.py` | Exact deterministic verdict semantics, partial disclosure, GL, non-mutation, and decimal arithmetic. |
| `tests/test_recovery_security.py` | Two-retry limit, repair, redaction, loopback/origin safety, and nested line-ID extraction. |
| `tests/test_reporting.py` | JSON round trip, exit-code policy, and CLI propagation. |
| `tests/test_demo_api.py` | Secure/vulnerable profiles, input validation, decimal boundary, duplicate lines, repeated actions, and concurrent duplicate invoices. |
| `reports/sample-report.json` | Mock-backed example report. |
| `reports/local-secure-report.json` | Local secure demo integration evidence. |
| `reports/local-vulnerable-gl-report.json` | Intentional unbalanced-journal breach evidence. |
| `reports/local-vulnerable-overpayment-report.json` | Intentional overpayment breach evidence. |
| `reports/live-happy-report.json` | Earlier unavailable-local-API attempt; it is not a passing live report. |
| `reports/qa-report.json` | Most recent default report artifact in the working tree. |

## 5. Complete workflow trace

The concrete happy-path flow is fixed in Python:

1. `cli.run_command()` loads `.env`, validates `--mode`, and calls `runner.execute()`.
2. `execute()` creates a unique ID such as `qa-20261011T171731Z-e32093`, selects the local or OpenAI reasoner, and opens one `ApiClient`.
3. `SchemaExplorer.discover()` requests `/openapi.json`, computes a SHA-256 hash of stable JSON, resolves request schemas, and indexes operations.
4. It requests all parameter-free GET operations. In `p2p_demo`, this includes `/health`, `/items`, `/vendors`, `/purchase-orders`, and `/invoices`.
5. It finds `STONE-1` and `CEMENT-50` from OpenAPI/safe GET data and captures active and inactive seed vendors.
6. `ExplorationAgent` asks the reasoner for a plan. This plan is saved; it does not control execution.
7. `BaseAgent.setup("happy", do_match=True, do_approve=True)` selects `vendor-active`.
8. `PayloadFactory` builds a PO using only the discovered `PurchaseOrderCreate` fields. Default scenario values are quantity `2` and unit price `10`.
9. The client creates the PO, extracts the PO and nested line identifiers, submits it, and receives quantity `2`.
10. It creates an invoice for `20`, matches it, and approves it.
11. The current happy-path code fetches PO detail, obtains receipt lines, computes the observed received value with `Decimal`, and compares it with the invoice request amount. Missing evidence becomes `INCONCLUSIVE`; inequality becomes `FAIL`.
12. `AdversarialAgent` creates separate POs/invoices for its seven scenarios. The scenarios can reuse the active seed vendor but use unique invoice numbers/run identifiers.
13. `InvariantJudge` creates named assertions and a control verdict from the evidence.
14. `reporting.summarize()` counts results and chooses the overall status.
15. `ReportWriter.write()` dumps and revalidates the report before saving it. The CLI prints a table and exits according to `exit_code()`.

### Traditional automation versus this agent

Traditional API regression tests normally know the endpoint paths, request bodies, and test records in advance. They are fast, repeatable, and ideal for stable known behavior. This project adds runtime contract discovery, safe seed inspection, schema-constrained payload synthesis, bounded repair after validation feedback, and cross-step control scenarios. That is the agentic portion.

It is not a replacement for normal tests. The execution policy is still deliberately constrained and largely predetermined. A mature suite should retain unit, schema/contract, database migration, integration, performance, concurrency, and security testing.

### LLM reasoning versus deterministic judgment

| LLM-assisted | Deterministic Python |
|---|---|
| Produces a human-readable plan | Selects and orders actual API calls |
| Can inspect one discovered operation schema during planning | Restricts paths/origin and enforces action limits |
| May propose one schema-constrained repair after deterministic repair cannot progress | Calculates received value and GL totals with `Decimal` |
| May explain a non-held result | Decides every `HELD`, `BREACHED`, `INCONCLUSIVE`, and `ERROR` verdict |
| Falls back if absent or invalid | Operates without an API key |

## 6. Installation and every run command

Use two terminals: one for the demo API and one for the QA agent. Run only one demo profile at a time.

### Windows PowerShell

#### Install

```powershell
cd "C:\Users\a_hos\Downloads\Interview Challenge QA"

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

If Python 3.11 is not installed, any supported Python 3.11 or newer interpreter can create the environment. Confirm which interpreter is active:

```powershell
python --version
python -c "import sys; print(sys.executable)"
```

#### Terminal 1: run the secure API

```powershell
cd "C:\Users\a_hos\Downloads\Interview Challenge QA"
.\.venv\Scripts\Activate.ps1
python -m p2p_demo --profile secure
```

Then open:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/health`

#### Terminal 2: run the agent

```powershell
cd "C:\Users\a_hos\Downloads\Interview Challenge QA"
.\.venv\Scripts\Activate.ps1

# Discovery only; does not intentionally mutate data
python -m p2p_qa discover --base-url http://localhost:8000

# Happy path only
python -m p2p_qa run `
  --base-url http://localhost:8000 `
  --mode happy `
  --report reports/happy-report.json

# Adversarial controls only
python -m p2p_qa run `
  --base-url http://localhost:8000 `
  --mode adversarial `
  --report reports/adversarial-report.json

# Complete run
python -m p2p_qa run `
  --base-url http://localhost:8000 `
  --mode all `
  --report reports/qa-report.json `
  --verbose

# Unit/integration tests
python -m pytest

# View the saved report
Get-Content reports\qa-report.json
```

The installed command names are equivalent:

```powershell
p2p-demo-api --profile secure
p2p-qa discover --base-url http://localhost:8000
p2p-qa run --base-url http://localhost:8000 --mode all
```

#### Demonstrate intentional breach detection

Stop the secure server with `Ctrl+C`, then start a vulnerable profile:

```powershell
python -m p2p_demo --profile vulnerable-overpayment
```

In the agent terminal:

```powershell
python -m p2p_qa run `
  --base-url http://localhost:8000 `
  --mode adversarial `
  --report reports/local-vulnerable-overpayment-report.json `
  --max-actions 150

$LASTEXITCODE
```

Expected demonstration outcome: `overpayment_protection` is `BREACHED`, overall is `FAIL`, and the process returns `1`.

To demonstrate GL detection instead:

```powershell
python -m p2p_demo --profile vulnerable-gl
python -m p2p_qa run --base-url http://localhost:8000 --mode adversarial --report reports/local-vulnerable-gl-report.json --max-actions 150
```

### macOS/Linux

#### Install

```bash
cd "/path/to/Interview Challenge QA"
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

#### Terminal 1

```bash
cd "/path/to/Interview Challenge QA"
source .venv/bin/activate
python -m p2p_demo --profile secure
```

#### Terminal 2

```bash
cd "/path/to/Interview Challenge QA"
source .venv/bin/activate

python -m p2p_qa discover --base-url http://localhost:8000
python -m p2p_qa run --base-url http://localhost:8000 --mode happy --report reports/happy-report.json
python -m p2p_qa run --base-url http://localhost:8000 --mode adversarial --report reports/adversarial-report.json
python -m p2p_qa run --base-url http://localhost:8000 --mode all --report reports/qa-report.json --verbose
python -m pytest
cat reports/qa-report.json
```

### CLI options

| Option | Meaning |
|---|---|
| `--base-url` | API origin only, such as `http://localhost:8000`; no path/query/credentials |
| `--mode happy|adversarial|all` | Which workflows to run |
| `--report PATH` | Output report, default `reports/qa-report.json` |
| `--timeout FLOAT` | Per-request HTTP timeout, minimum 0.1 seconds |
| `--max-actions INTEGER` | Shared request budget for discovery and workflows |
| `--seed INTEGER` | Recorded and passed to `random.seed`; currently no scenario choices use it |
| `--fail-on-inconclusive` | Exit `3` when included evidence is inconclusive |
| `--allow-nonlocal` | Permit an HTTP(S) origin that is not loopback |
| `--verbose` | Print additional final JSON/detail, not a streaming trace |

### Optional OpenAI configuration

The agent works without an LLM. The default report model value is `deterministic-fallback` when `OPENAI_MODEL` is empty.

PowerShell session:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_MODEL = "your-configured-model"
python -m p2p_qa run --base-url http://localhost:8000 --mode all
```

macOS/Linux session:

```bash
export OPENAI_API_KEY='your-key'
export OPENAI_MODEL='your-configured-model'
python -m p2p_qa run --base-url http://localhost:8000 --mode all
```

Never put a real key in source control or interview screenshots. One current reporting caveat is that `RunInfo.model` reads the environment variable directly; if a model is set but the key is absent, local reasoning is used even though the report can still show that model name.

### Using a remote staging API

```powershell
python -m p2p_qa run `
  --base-url https://p2p-staging.example.com `
  --allow-nonlocal `
  --mode all `
  --report reports/staging-report.json
```

Use a dedicated test tenant. The current client does not implement authentication headers, cleanup, or an allowlist beyond exact origin. A random public OpenAPI server is useful only for discovery demonstrations; it cannot validate P2P controls unless it implements the required business workflow and exposes suitable data.

## 7. Demo API contract and concrete examples

These examples match `p2p_demo`. A different target can expose different field names; the agent discovers them rather than assuming these examples universally.

### Seed data

```json
[
  {"id":"vendor-active","name":"Acme Building Materials","code":"ACME","active":true,"status":"active"},
  {"id":"vendor-inactive","name":"Dormant Stone Supply","code":"DORMANT","active":false,"status":"inactive"}
]
```

```json
[
  {"sku":"STONE-1","description":"Cut limestone block","active":true},
  {"sku":"CEMENT-50","description":"50 lb cement bag","active":true}
]
```

### Create, submit, and receive a PO

```http
POST /purchase-orders
Content-Type: application/json

{
  "vendor_id": "vendor-active",
  "lines": [{"sku": "STONE-1", "quantity": 2, "unit_price": 10.00}],
  "description": "QA happy"
}
```

Successful creation returns `201` with an ID, status `draft`, and line fields including `ordered_quantity`, `received_quantity`, and `unit_price`.

```http
POST /purchase-orders/{po_id}/submit
```

```http
POST /purchase-orders/{po_id}/receive
Content-Type: application/json

{"lines":[{"sku":"STONE-1","quantity":2}]}
```

### Create, match, and approve an invoice

```http
POST /invoices
Content-Type: application/json

{
  "vendor_id": "vendor-active",
  "po_id": "po-...",
  "invoice_number": "qa-...-happy-invoice",
  "amount": 20.00
}
```

```http
POST /invoices/{invoice_id}/match
POST /invoices/{invoice_id}/approve
```

Secure approval includes journal evidence similar to:

```json
{
  "status": "approved",
  "invoice_id": "invoice-...",
  "journal": {
    "id": "journal-...",
    "invoice_id": "invoice-...",
    "lines": [
      {"account": "Expense", "debit": "20.00", "credit": "0.00"},
      {"account": "Accounts Payable", "debit": "0.00", "credit": "20.00"}
    ]
  }
}
```

## 8. Business invariants and exact verdict logic

The project reports seven adversarial rows because over-receipt is tested in addition to the six core financial controls.

### 8.1 Three-way-match gate (`three_way_match_gate`)

- **Preconditions/test data:** active vendor; submitted PO for 2 × 10; receive 2; create invoice 20; do not call match.
- **Calls:** setup calls, `GET /vendors/{id}/exposure`, `POST /invoices/{id}/approve`, then exposure again.
- **Expected response:** approval should be rejected with a 4xx.
- **Expected state:** vendor exposure before and after must be exactly equal.
- **Deterministic assertions:** `prohibited_operation_rejected` and `observable_state_unchanged`.
- **HELD:** 4xx plus equal observable exposure.
- **BREACHED:** approval returns 2xx or exposure changes, even if the API also returned an error response elsewhere.
- **INCONCLUSIVE:** rejection occurred but one or both exposure snapshots are unavailable, or another non-proving outcome occurs.
- **ERROR:** judge received no HTTP status.

### 8.2 Excess goods receipt (`excess_goods_receipt`)

- **Preconditions/test data:** active vendor; PO submitted without any receipt or invoice.
- **Calls:** `GET /purchase-orders/{id}`, derive ordered quantity, attempt a receipt of `ordered + 1`, then read the PO again.
- **Expected response:** over-receipt should be 4xx.
- **Expected state:** PO detail and received quantity remain unchanged.
- **Deterministic assertions:** rejection plus exact before/after PO equality.
- **HELD:** 4xx and unchanged observed PO.
- **BREACHED:** 2xx or changed state.
- **INCONCLUSIVE:** ordered quantity or before/after PO detail cannot be observed.
- **Why it matters:** accepting too much quantity inflates received value and can make a later overpayment look valid.

### 8.3 Overpayment protection (`overpayment_protection`)

- **Preconditions/test data:** PO quantity 2 at price 10; receive only 1.
- **Calls:** observe PO detail; compute received value; read exposure; create invoice at `received_value + 0.01`; if creation succeeds, attempt match and then approval; read exposure again.
- **Calculation:** `received_value = Σ(received_quantity × agreed_unit_price)` using `Decimal`.
- **Expected response/state:** the over-value invoice must never become an approved payable, and exposure must not increase.
- **Assertions:** attack is actually above received value, invoice is not approved, and exposure is unchanged.
- **HELD:** attack is above the observed boundary, approval is known false, and exposure is observably unchanged.
- **BREACHED:** approval succeeds or exposure changes.
- **INCONCLUSIVE:** the attack boundary is not above value, or final non-approval/non-mutation evidence is incomplete.
- **ERROR:** approval outcome cannot be determined and no mutation already proves a breach.
- **Boundary behavior:** the happy path separately tests equality: invoice 20 for received value 20 must complete successfully.

### 8.4 Inactive-vendor gate (`inactive_vendor_gate`)

- **Preconditions/test data:** select or create an inactive vendor and a discovered SKU.
- **Calls:** if declared, snapshot `GET /purchase-orders`; attempt `POST /purchase-orders`; snapshot collection again.
- **Expected response:** 4xx.
- **Expected state:** no new PO for that vendor.
- **Assertions:** rejection plus exact filtered collection equality.
- **HELD:** 4xx and observable unchanged vendor-specific PO collection.
- **BREACHED:** 2xx or changed collection.
- **INCONCLUSIVE:** no inactive vendor/schema/SKU, or the target lacks an observable PO collection. This is intentional: the original minimum endpoint list does not require `GET /purchase-orders`, and 4xx alone does not prove non-mutation.

### 8.5 Partial-receipt disclosure (`partial_receipt_disclosure`)

- **Preconditions/test data:** PO quantity 2 at price 10; receive 1; invoice 10.
- **Calls:** read PO detail, verify numeric quantities, then call match.
- **Expected response:** explicit `partial`, mismatch, shortage, or exception state; it must not silently claim a clean full match.
- **Assertions:** `0 <= received < ordered` and explicit partial-state disclosure.
- **HELD:** the numeric state is partial and the response explicitly identifies partial/mismatch status.
- **BREACHED:** response claims a clean match without a partial indication.
- **INCONCLUSIVE:** quantities are unavailable, receipt is not actually partial, or the API gives a generic rejection without explicit business-state evidence.
- **ERROR:** no match HTTP status.

### 8.6 Duplicate invoice protection (`duplicate_invoice_protection`)

- **Preconditions/test data:** create one invoice through normal setup; preserve its exact vendor and invoice number.
- **Calls:** if declared, snapshot `GET /invoices`; repeat the same `POST /invoices` payload; snapshot again.
- **Expected response:** duplicate request is 4xx.
- **Expected state:** count/state for that vendor and invoice number is unchanged.
- **Assertions:** rejection plus exact filtered before/after collection equality.
- **HELD:** 4xx and one observable record remains one.
- **BREACHED:** duplicate succeeds or state changes.
- **INCONCLUSIVE:** invoice collection is not observable. Exposure alone is insufficient here because the first invoice is not necessarily approved.

### 8.7 GL balance (`gl_balance`)

- **Preconditions/test data:** full matched and approved invoice.
- **Calls:** inspect approval response; if it has no debit/credit data, find a discovered GET operation containing `journal` or `ledger` and request it with the journal or invoice identifier.
- **Expected response/state:** numeric journal lines tied to approval should be observable.
- **Calculation:** sum all found debit amounts and all credit amounts with `Decimal`.
- **HELD:** total debits equal total credits.
- **BREACHED:** observable numeric totals differ.
- **INCONCLUSIVE:** no lines, malformed amounts, or insufficient journal observability.
- **Current scope limitation:** equal zero totals can pass, and the judge does not independently prove linkage beyond the response/identifier route used to obtain the evidence.

### Extra input and repeat-behavior coverage in `p2p_demo`

The demo API tests also cover negative PO/receipt quantity, zero invoice amount, duplicate SKU lines, decimal exact/sub-cent boundaries, repeated PO submission, repeated receipt, repeated approval, and four concurrent duplicate invoice attempts. These checks validate the demo target and test fixtures; the current `AdversarialAgent` does not dynamically run all of them against arbitrary external APIs.

## 9. Status, report, and exit semantics

### Status vocabulary

- **Discovery `PASS`:** a nonempty OpenAPI dictionary was returned. It does not guarantee P2P completeness.
- **Happy path `PASS`:** required positive calls returned 2xx and the exact-boundary comparison was observable and equal.
- **Happy path `FAIL`:** an expected positive workflow step failed for a reason treated as a product/workflow failure, or the observed boundary was unequal.
- **`HELD`:** a control was actually demonstrated. Rejection controls normally require both 4xx and observable non-mutation.
- **`BREACHED`:** a prohibited action succeeded, observable state changed, partial goods became a clean match, or GL totals differed.
- **`INCONCLUSIVE`:** evidence, schema, data, operation, or observability was insufficient. It is never converted to success.
- **`ERROR`:** transport/runtime evidence prevented execution or judgment in paths that preserve an error distinction.

Individual `StepEvidence.verdict` describes that HTTP step. A control verdict comes from combined scenario evidence and may legitimately differ from a single step label.

### Report structure

```json
{
  "run": {
    "id": "qa-...",
    "started_at": "...",
    "finished_at": "...",
    "base_url": "http://localhost:8000",
    "schema_hash": "...",
    "model": "deterministic-fallback",
    "seed": null
  },
  "discovery": {
    "status": "PASS",
    "operations": [],
    "data_sources": [],
    "limitations": [],
    "inspected_for_sku": [],
    "sku_candidates": [],
    "evidence": []
  },
  "happy_path": {
    "status": "PASS",
    "steps": [],
    "plan": [],
    "resources": {}
  },
  "adversarial": [
    {
      "rule": "overpayment_protection",
      "status": "HELD",
      "evidence": {},
      "assertions": [],
      "steps": [],
      "explanation": ""
    }
  ],
  "summary": {
    "overall_status": "PASS",
    "held": 7,
    "breached": 0,
    "inconclusive": 0,
    "errors": 0,
    "highest_risk_findings": []
  }
}
```

Every stored HTTP step includes name/time, purpose, method/URL, sanitized body, status/body, duration, expected/observed outcomes, interpretation, deterministic assertions, verdict, and attempt number.

### Overall-status calculation

Priority in `summarize()` is:

1. `FAIL` if a non-skipped happy path is `FAIL` or any control is `BREACHED`.
2. `ERROR` if discovery/happy/control contains an applicable error.
3. `INCONCLUSIVE` if the executed happy path or any control is inconclusive.
4. Otherwise `PASS`.

In adversarial-only mode, happy path is represented as `INCONCLUSIVE` with `resources.not_run=true`; summary logic ignores that placeholder.

### Process exit codes

| Code | Meaning |
|---|---|
| `0` | No happy-path failure or proven breach/error; inconclusive is allowed by default |
| `1` | Happy path `FAIL` or at least one `BREACHED` control |
| `2` | Discovery/happy/control `ERROR` according to the report |
| `3` | `--fail-on-inconclusive` was set and applicable evidence is inconclusive |

An unhandled exception, such as action-budget exhaustion, can still terminate outside this semantic mapping; that is a current robustness limitation.

### Safety behavior

- Base URL must be an absolute `http` or `https` origin with no credentials, query, fragment, or non-root path.
- Loopback is the default. Non-loopback needs `--allow-nonlocal`.
- Every request path must begin with one `/`, cannot begin with `//`, and is rechecked against the exact scheme/host/port.
- Redirect following is disabled.
- Requests have a configured timeout and a shared action count.
- Request bodies use exact `Decimal` encoding; Python floats are refused.
- Evidence recursively redacts values under keys matching authorization/key/token/secret/password/cookie patterns and masks bearer/OpenAI-key-looking strings.
- The LLM gets metadata only and no network/shell capability.
- Created records use unique run-prefixed text where the target schema permits it.
- The current implementation has no authentication injection, cleanup, response-size bound, production tenant allowlist, or write-approval gate.

## 10. Diagnosis of the earlier all-inconclusive run

The previously quoted console output was:

```text
Discovery                    PASS
Happy path                   INCONCLUSIVE
three_way_match_gate         INCONCLUSIVE
excess_goods_receipt         INCONCLUSIVE
overpayment_protection       INCONCLUSIVE
inactive_vendor_gate         INCONCLUSIVE
partial_receipt_disclosure   INCONCLUSIVE
duplicate_invoice_protection INCONCLUSIVE
gl_balance                   INCONCLUSIVE
Overall                      INCONCLUSIVE
```

No JSON artifact in the current `reports/` directory exactly matches that output, so the exact first request, response, and limitation cannot be forensically proven from the repository. Do not pretend otherwise in an interview.

The architecture makes the likely root cause clear: **discovery success is weaker than workflow readiness**. `SchemaExplorer.discover()` marks `PASS` when it downloads a nonempty OpenAPI dictionary. A wrong public/non-P2P URL can therefore pass discovery while lacking `/purchase-orders`, `/invoices`, P2P schemas, vendors, or a usable SKU. A real P2P API can produce the same downstream effect when its OpenAPI omits mutation schemas or no SKU is visible in schema examples/enums or safe GET data.

Likely failure sequence:

1. `/openapi.json` returned 200 and a document, so discovery was `PASS`.
2. `BaseAgent.setup()` could not select/create an active vendor, find a SKU, or find a request schema/operation.
3. `ExplorationAgent` could not establish the valid baseline.
4. Each adversarial scenario depends on that baseline and returned `INCONCLUSIVE` rather than fabricating identifiers or treating absence as protection.

The first place to investigate is `happy_path.resources.limitation`; then inspect `happy_path.steps` and `discovery.limitations`, `operations`, `data_sources`, `inspected_for_sku`, and `sku_candidates`. The source path is:

```text
SchemaExplorer.discover / _probe_safe_gets / _discover_skus
  -> BaseAgent._vendor / setup
  -> ExplorationAgent.run
  -> AdversarialAgent._inconclusive
```

Use this PowerShell diagnosis:

```powershell
$r = Get-Content reports\qa-report.json -Raw | ConvertFrom-Json
$r.discovery | ConvertTo-Json -Depth 20
$r.happy_path.resources | ConvertTo-Json -Depth 20
$r.happy_path.steps | Select-Object step_name,status_code,response_body,interpretation
$r.adversarial | Select-Object rule,status,explanation
```

The appropriate fix is not to rewrite `INCONCLUSIVE` as `PASS`. Choose the real P2P target, expose a valid SKU through OpenAPI or a safe GET endpoint, provide missing request schemas/state observation, or add a narrow alias/adapter backed by a regression test. The repository now includes `p2p_demo`, which supplies a known contract, seed vendors/items, and observable collections for a self-contained interview run.

One additional issue was found and fixed during the final live validation. After the demo gained `GET /purchase-orders`, the first updated run fetched the collection where PO detail was expected. The agent asked for the template `/purchase-orders/{id}`, while FastAPI advertised `/purchase-orders/{po_id}`; exact lookup missed, and the fuzzy fallback preferred the shorter collection path. The first visible symptom was `invoice_equals_observed_received_value = null`, followed by happy-path and receipt-dependent controls becoming `INCONCLUSIVE`. `BaseAgent._op()` now normalizes placeholder names before fuzzy lookup, so `{id}`, `{po_id}`, and similar names select the same route shape. The mock OpenAPI fixture now deliberately uses `{po_id}`, `{invoice_id}`, and `{vendor_id}` to preserve this regression case. The next live run completed with happy `PASS` and seven controls `HELD`.

### Related but different artifact

`reports/live-happy-report.json` records an older localhost-unavailable attempt: discovery `ERROR`, happy path `ERROR`, no SKU, and OpenAPI unavailable. It is not the exact all-inconclusive run and should not be cited as if it were.

## 11. Current evidence and tests

### Report artifacts inspected

- `reports/qa-report.json`: local secure run, discovery `PASS`, happy `PASS`, seven controls `HELD`, overall `PASS`, two SKU candidates.
- `reports/local-secure-report.json`: local secure run, discovery `PASS`, happy `PASS`, seven controls `HELD`, overall `PASS`.
- `reports/local-vulnerable-gl-report.json`: complete live run against the intentionally unbalanced GL profile. Discovery and happy path pass; six controls are `HELD`; `gl_balance` is `BREACHED`; overall is `FAIL`; the CLI exits `1`.
- `reports/local-vulnerable-overpayment-report.json`: complete live run against the intentionally vulnerable profile. Discovery and happy path pass; six controls are `HELD`; `overpayment_protection` is `BREACHED`; received value is `10.00`, invoice value is `10.01`, approval succeeds, and liability changes from `20.00` to `30.01` because the same run's happy-path invoice was already approved. Overall is `FAIL`, the risk list contains `overpayment_protection`, and the CLI exits `1`.
- `reports/sample-report.json`: mock-backed example with discovery/happy pass and seven held controls.
- `reports/live-happy-report.json`: unavailable API attempt and therefore `ERROR`, not a validation success.

The vulnerable report is especially useful in an interview because it proves the judge can fail a target instead of merely producing green output.

### Test inventory

The current tree collects 27 tests:

```text
tests/test_demo_api.py:             8
tests/test_judge.py:                7
tests/test_recovery_security.py:    5
tests/test_reporting.py:            3
tests/test_workflow.py:             4
```

The tests cover the requested happy path, all controls, vulnerable overpayment, unbalanced and unavailable GL, deterministic decimal calculation, schema recovery, two-retry exhaustion, secret redaction, URL escape prevention, report serialization, exit codes, negative/zero values, repeat behavior, decimal boundaries, duplicate PO SKU lines, and concurrent duplicate invoice creation.

Run the suite in the project virtual environment and report the output you personally observe:

```powershell
python -m pytest
```

At the time of this guide's final validation, the current managed environment reported Python `3.14.3`. The complete suite passed:

```text
...........................                                              [100%]
27 passed; process exit code 0
```

The original demo tests used Starlette's deprecated `TestClient` adapter, which stalled in this managed Windows environment. They now use HTTPX's maintained ASGI transport. The sandbox also had to permit the local event-loop socket pair; that was an environment permission, not a financial-test change. No assertion was removed or weakened. A clean Python 3.11 virtual environment remains the recommended interview setup because 3.11 is the project's stated minimum and avoids presenting on an unplanned interpreter version.

## 12. Known limitations and honest future improvements

### Current limitations

1. The action graph is fixed Python. The LLM plan is not an executable autonomous loop.
2. Only the first discovered SKU is used; alternate candidates are not tried.
3. Payload synthesis supports common schemas but not the full JSON Schema/OpenAPI vocabulary.
4. Examples/defaults/consts take precedence over contextual values and can be stale for some third-party contracts.
5. Most attack calls are not schema-repaired; a 4xx can reflect generic validation rather than the intended business rule. Before/after evidence mitigates mutation uncertainty but not semantic rejection-reason uncertainty.
6. State equality is exact; volatile timestamps or counters can make safe state look changed.
7. Required collection observability is not guaranteed by the original endpoint list, so inactive/duplicate checks can correctly remain inconclusive.
8. `find_first` traverses response insertion order; it does not enforce alias priority. Ambiguous payloads can select the wrong quantity field.
9. Path substitution uses one value for every placeholder and does not URL-encode the identifier.
10. GL equality does not require nonzero lines and only indirectly establishes invoice linkage.
11. There is no test-data cleanup, authentication mechanism, pagination support, or rate-limit-aware retry.
12. Action-limit exhaustion and some malformed schemas can escape without a finalized report.
13. OpenAPI success does not validate the required P2P capability set.
14. Safe GET discovery trusts parameter-free GET declarations and has no response-size cap.
15. `OPENAI_MODEL` can be recorded even when no key exists and local fallback was actually used.
16. The in-memory demo is not durable and is not intended to model production authorization, persistence, or distributed transactions.

### Recommended improvements, in priority order

1. Add a capability-readiness phase that explicitly reports required operation/schema/SKU/observability gaps after OpenAPI fetch.
2. Add a reason-code assertion for negative responses so unrelated validation 4xx cannot prove a business control.
3. Normalize state snapshots to ignore declared volatile fields while preserving financial fields.
4. Add authenticated client configuration with secret-safe header injection and least-privilege profiles.
5. Add cleanup/idempotency keys and a dedicated test-tenant policy.
6. Handle action-budget and malformed-schema exceptions by finalizing an `ERROR` report.
7. Try bounded alternative SKU/vendor/schema branches and log why each branch was selected.
8. Improve OpenAPI support for discriminators, nested unions, constraints, and response-schema validation.
9. Verify journal linkage, nonzero posting, currency, posting date, account policy, and line-level allocation.
10. Add pagination, rate-limit handling, correlation IDs, distributed traces, and encrypted artifact retention.
11. Add external integration tests against a disposable containerized API and a supported-version dependency matrix.
12. If greater autonomy is desired, implement a policy-constrained action loop whose tool calls are validated against discovered operations and deterministic safety gates. Preserve the deterministic judge.

### What changes for scheduled production monitoring

Use a dedicated tenant, service identity, least-privilege credentials, explicit target and endpoint allowlists, maintenance/mutation windows, idempotency keys, retention and cleanup, encryption, rate limits, alert deduplication, audit correlation IDs, and human approval for high-risk writes. Add a read-only/canary mode. Never run the current adversarial mutations casually against a shared production ledger.

## 13. Troubleshooting guide

### `No module named p2p_qa`

Activate the correct virtual environment and install the editable package:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -c "import p2p_qa; print(p2p_qa.__version__)"
```

### Connection refused / discovery `ERROR`

The API is not listening at the selected origin. In terminal 1:

```powershell
python -m p2p_demo --profile secure
```

Then verify:

```powershell
Invoke-RestMethod http://localhost:8000/health
Invoke-RestMethod http://localhost:8000/openapi.json | Select-Object openapi,info
```

### Port 8000 already in use

Start the server on another port and point the agent to the same port:

```powershell
python -m p2p_demo --profile secure --port 8001
python -m p2p_qa run --base-url http://localhost:8001 --mode all
```

### Discovery `PASS`, everything else `INCONCLUSIVE`

Inspect `operations`, `sku_candidates`, limitations, and the happy-path limitation. Confirm that the origin is a P2P API rather than merely any OpenAPI service. Do not weaken the verdict.

### `non-loopback targets require --allow-nonlocal`

This is a safety feature. Use a staging target and opt in explicitly:

```powershell
python -m p2p_qa discover --base-url https://your-staging-origin.example --allow-nonlocal
```

### Base URL rejected

Pass only the origin. This is valid:

```text
http://localhost:8000
```

These are intentionally invalid as base URLs:

```text
http://localhost:8000/docs
http://user:pass@localhost:8000
http://localhost:8000?x=1
```

### No SKU found

Expose a real item identifier through a safe GET endpoint, schema example, or enum. Review `discovery.inspected_for_sku`. Do not hard-code a made-up SKU.

### Control rejected but remains `INCONCLUSIVE`

The API probably lacks before/after observability. A 4xx proves only that one request was rejected, not that the server avoided partial mutation. Add a safe detail/list/audit endpoint or other immutable evidence.

### GL is `INCONCLUSIVE`

Return debit/credit lines in approval or expose a discoverable GET journal/ledger path. A status of `approved` alone cannot prove accounting balance.

### `maximum action count ... reached`

The shared budget includes discovery GETs and every scenario step. Increase it deliberately:

```powershell
python -m p2p_qa run --base-url http://localhost:8000 --mode all --max-actions 200
```

The current implementation may terminate before writing the final report when this exception escapes.

### LLM is not being used

Both variables are required:

```powershell
$env:OPENAI_API_KEY
$env:OPENAI_MODEL
```

If either is absent, deterministic fallback is intentional. Core workflows and verdicts still run.

### Full test suite stalls during demo API tests

The repository no longer uses Starlette `TestClient`; it uses HTTPX ASGI transport. Use the documented clean Python 3.11 virtual environment, reinstall declared dependencies, and inspect versions and local socket restrictions:

```powershell
python --version
python -m pip show fastapi starlette httpx anyio
python -m pytest -vv -s tests\test_demo_api.py
```

If it stalls while creating an event loop, verify that endpoint protection or a managed sandbox is not blocking local socket pairs. Do not “fix” this by deleting financial assertions. Treat it as a harness/environment issue and validate any dependency pin in the full suite.

### A vulnerable run exits nonzero

That is the expected demonstration. PowerShell may display the command as failed because exit code `1` means a proven breach. Inspect the report rather than suppressing the code.

## 14. Two-minute interview presentation

> I built an evidence-driven QA agent for Purchase-to-Pay APIs. The business risk is not simply whether an endpoint responds; it is whether the system can prevent paying more than received, block unmatched or duplicate invoices, reject inactive-vendor purchasing, disclose partial receipts, and post balanced accounting entries.
>
> The agent starts by fetching OpenAPI, resolving local schema references, hashing the contract, probing only parameter-free GET resources, and finding real vendor and SKU data. It then builds requests from the discovered schema rather than inventing fields. A bounded client enforces one origin, loopback by default, timeouts, a request budget, no redirects, secret redaction, and exact Decimal JSON.
>
> The happy path creates or selects an active vendor and executes PO creation, submit, receipt, invoice, match, and approval. Separate adversarial scenarios deliberately try each prohibited action. The important design choice is that the LLM is advisory: it can write a plan, help repair a schema-invalid payload, and summarize failure evidence, but fixed Python executes the actions and `InvariantJudge` makes every financial decision with Decimal and before/after state.
>
> A 4xx alone is not enough to call a control effective. Where observable, the agent verifies that state did not change. Missing data becomes INCONCLUSIVE, not PASS. The final Pydantic-validated JSON preserves every request, response, assertion, timing, and verdict, and CI receives a meaningful exit code.
>
> For the demo I included a disposable FastAPI P2P service. The secure profile produces a passing workflow with seven held controls. The vulnerable-overpayment profile lets a 10.01 invoice become payable after only 10.00 of goods were received; the agent observes exposure rise and reports a breach with exit code 1. That proves the framework detects a real control failure rather than only generating green reports.

## 15. Technical interview questions and sample answers

### 1. Why is this “agentic” if the action sequence is fixed?

It is agentic in a constrained sense: it discovers the live contract and seed data, synthesizes payloads, adapts after structured validation feedback, can consult an LLM, and carries state through a business workflow. The actual action graph remains fixed for auditability and safety. I would not claim that the LLM autonomously dispatches calls, because the current code does not do that.

### 2. Why use OpenAPI at runtime?

It reduces hard-coded coupling. The agent can learn request properties, required fields, examples, enums, and operations from the target it is testing. It also makes contract drift visible through the schema hash and discovery evidence.

### 3. What does the schema hash prove?

It fingerprints the discovered JSON document after stable serialization, so reports can be tied to a particular observed contract. It does not prove the implementation conforms to that contract or that the document is semantically complete.

### 4. How are `$ref` values handled?

`SchemaExplorer.resolve()` follows local JSON Pointer references beginning with `#/`, decodes `~0` and `~1`, prevents direct cycles with a `seen` set, and recursively resolves nested values. It also merges `allOf` properties and required fields.

### 5. What happens when OpenAPI is unavailable?

Discovery records the limitation and installs the documented endpoint table without mutation schemas. It probes conservative GET behavior, but the payload factory refuses to invent mutation fields. Dependent workflows therefore become inconclusive or error rather than guessing.

### 6. How does the agent choose a vendor?

It reads `/vendors`, normalizes common collection shapes, and selects the first record matching active/inactive aliases. If none exists, it attempts schema-backed creation with run-prefixed values. Missing active fields default to active, which is useful but also a limitation worth tightening.

### 7. How does it find a SKU without a documented list endpoint?

It recursively searches OpenAPI plus successful parameter-free GET responses for common item/SKU keys and relevant enum/example arrays. If nothing valid is found, it records exactly what was inspected and refuses to fabricate a SKU.

### 8. How are payloads built?

`PayloadFactory` recursively walks the request schema. It uses schema constants/examples/defaults/enums, required properties, arrays, primitive types, and semantic context aliases. It emits only declared properties.

### 9. What is the recovery policy?

A positive request gets one initial attempt plus at most two repairs. Deterministic repair runs first. If it cannot change the body, the optional reasoner can propose a dictionary whose top-level keys must be allowed by the request schema. Transport errors and 5xx responses are not blindly retried.

### 10. Why not retry server errors?

A write may have committed before a 5xx or timeout. Blind retry can create duplicates or compound state. Safe retries need idempotency keys, operation semantics, and state verification, none of which should be assumed.

### 11. Why use `Decimal` instead of float?

Binary floating point cannot exactly represent many decimal fractions, so boundary comparisons and ledger totals can be wrong. The judge converts through `Decimal(str(value))`, and the request encoder rejects floats.

### 12. How is received value calculated?

For each observable PO line, the judge multiplies received quantity by agreed unit price and sums the products with a zero `Decimal` accumulator. Missing quantity or price makes the calculation unavailable rather than guessed.

### 13. Why is a 4xx insufficient for `HELD`?

The server might validate after performing a partial side effect, or the rejection might be caused by an unrelated malformed field. The judge generally also requires an observable state snapshot to remain unchanged. A future enhancement should also assert the business-specific rejection reason.

### 14. What is the difference between `PASS` and `HELD`?

`PASS` describes successful discovery/happy-path or an individual positive step. `HELD` describes a tested control that prevented a prohibited outcome with sufficient evidence.

### 15. What creates a `BREACHED` verdict?

Examples are a prohibited action returning success, protected state changing, an over-value invoice becoming approved, a partial receipt being reported as a clean match, or debit and credit totals differing.

### 16. When is `INCONCLUSIVE` correct?

When the control cannot be proved or disproved: no SKU, missing request schema, unavailable before/after endpoint, no journal lines, generic partial-match rejection, or malformed monetary evidence. It is a first-class honest result.

### 17. How is GL balance verified?

The judge finds the first nonempty list with debit/credit fields, parses amounts with `Decimal`, treats a missing opposite side as zero, and compares totals. If approval lacks lines, the agent can call a discovered journal/ledger GET operation.

### 18. What does GL balance not prove?

It does not prove account correctness, currency, posting date, invoice linkage beyond the evidence route, completeness, or that a nonzero posting occurred. Balance is necessary but not sufficient accounting correctness.

### 19. How is duplicate protection scoped?

The repeated payload uses the same vendor and invoice number. The before/after invoice collection is filtered by both. Reusing a number for another vendor is not automatically treated as a duplicate.

### 20. How is partial receipt different from overpayment?

Partial disclosure asks whether matching communicates that the PO is not fully received. Overpayment asks whether payable value exceeds the monetary value actually received. They are related but separate controls.

### 21. What evidence is stored?

Each step stores UTC time, purpose, method, URL, sanitized body, status, sanitized response, duration, expected/observed outcome, interpretation, assertions, verdict, and attempt. Each control adds combined evidence and named assertions.

### 22. How are secrets protected?

Sensitive dictionary keys are replaced with `[REDACTED]`, and bearer/OpenAI-key-looking substrings in strings are masked. The OpenAI SDK reads credentials from the environment; the application does not add them to evidence.

### 23. How does URL safety work?

The base must be an origin. Loopback is required unless explicitly overridden. Each request must be root-relative, is joined to the origin, and is checked for equal scheme, hostname, and port. Redirects are not followed.

### 24. What happens without an OpenAI key?

`LocalReasoner` supplies a transparent fixed plan, no LLM repair, and a stock explanation. Discovery, HTTP workflows, all deterministic judges, reports, and exit codes continue to work.

### 25. What can the LLM change?

It can change report plan text, suggest a schema-constrained body when deterministic repair fails, and generate explanatory prose. It cannot choose the judge result, calculate money, access shell/network tools, or directly execute requests.

### 26. Why keep the model behind a protocol?

It prevents provider details from leaking into the HTTP client and judge. It also makes offline CI straightforward and allows another provider or deterministic test double without changing financial logic.

### 27. How does the action budget work?

One `ApiClient` counts every request in discovery and all selected workflows. Once the count reaches `max_actions`, it raises `ActionLimitExceeded`. A production improvement would catch it and finalize an error report.

### 28. How is the overall status chosen?

Failure or breach has highest priority, then runtime error, then inconclusive, then pass. Adversarial-only happy-path placeholders are ignored by summary logic.

### 29. Why use Pydantic report models?

They make status vocabularies and required evidence fields explicit, validate before writing, and let tests round-trip serialized JSON. This prevents ad hoc report shape drift.

### 30. How do mock tests differ from live tests?

MockTransport tests are deterministic and verify orchestration/judgment without a process or network. `p2p_demo` integration tests exercise real FastAPI routing and state. A live run additionally proves the packaged CLI and real HTTP boundary against a running target. Reports must label those contexts honestly.

### 31. Why include an intentionally vulnerable API profile?

Passing only against a secure target can hide an ineffective oracle. A vulnerable profile is a mutation-style demonstration: the agent must produce a breach and nonzero exit code when a real control is removed.

### 32. How is concurrency tested?

The demo wraps mutations in an `RLock`. Four concurrent same-vendor/same-number invoice requests are issued; exactly one should return 201 and three should return 409. The general agent does not yet perform concurrency attacks against arbitrary targets.

### 33. Is repeated approval idempotent?

In the demo, a second approval is rejected with 409, preventing a second journal. Strictly, that is duplicate-operation safety rather than classic idempotency returning the same response. I would describe it accurately.

### 34. What would you add for authentication?

I would add a credential-provider interface, allow only preapproved header names, keep values out of models/logs, support token refresh, and test that redirects cannot leak credentials. I would also separate read and mutation scopes.

### 35. What is the most important design trade-off?

The project favors bounded, explainable behavior over unconstrained autonomy. It adapts enough to tolerate common schema variation, but financial verdicts and safety boundaries stay deterministic and reviewable.

## 16. Scenario-based and difficult follow-up questions

### Scenario: The invoice POST returns 500 after the server committed it. Should the agent retry?

No, not automatically. First query an idempotency key or a vendor/invoice-number lookup. If the state is observable, reconcile it. Only retry a mutation when the contract guarantees idempotency and the same idempotency key is reused. The current code intentionally stops on 5xx.

### Scenario: Approval returns 409, but exposure increased.

That is a breach. The status code says rejection, but the state proves a side effect. `rejection_control` prioritizes observed mutation over the rejection.

### Scenario: Approval returns 200 but says `status: rejected`.

The current generic `.ok` handling would treat 2xx as operation success in several paths. I would improve it by validating the documented response schema and business status, then test both transport and domain outcomes.

### Scenario: The API supports three currencies.

Do not sum unlike currencies. Capture currency on PO, receipt valuation, invoice, and journal; assert currency agreement; apply configured rounding/scale; and group GL totals by currency. FX conversion needs an explicit rate source and date.

### Scenario: A PO line has quantity 0.1 and price 0.2.

`Decimal` computes exactly `0.02`; a binary float path might produce a neighboring representation. The existing unit test covers that boundary.

### Scenario: OpenAPI uses `oneOf` with discriminators.

The current factory takes the first branch and does not implement discriminators. I would parse the discriminator mapping, select a branch from seed/context, validate locally, and keep the number of attempted branches bounded.

### Scenario: Every GET collection is paginated.

Current `_records` sees only the returned page and has no pagination loop. I would discover pagination metadata/links, cap pages/items, include each request in the action budget, and record completeness before using absence as evidence.

### Scenario: A rejected duplicate increments an audit counter.

Exact whole-object equality can produce a false breach if volatile data is included. I would compare a schema-declared projection of protected financial state and separately record allowed audit-field changes.

### Scenario: Matching a partial receipt returns 422 with `detail: invalid state`.

The current judge returns inconclusive because it lacks explicit partial/mismatch disclosure. To prove the rule, require a domain reason code such as `PARTIAL_RECEIPT`, an invoice status readback, or an audit event.

### Scenario: The LLM proposes a field that OpenAPI does not contain.

`AdaptiveRequester` rejects a repair with unknown top-level keys. I would additionally validate nested values and the full body against the resolved JSON Schema before sending it.

### Scenario: Two agents use the same invoice number concurrently.

Use a shared test run ID plus a deliberate common duplicate key, fire synchronized requests, and then assert exactly one durable record and one liability. The demo API test covers the API-side race with a lock; the general agent needs a bounded concurrency scenario and correlation evidence.

### Scenario: An attacker places a secret in a response field not named like a secret.

Key-based redaction may miss it. Add configurable value-pattern detectors, response allowlisting/projection, maximum evidence size, and encrypted restricted artifact storage. Redaction should be tested with adversarial content.

### Difficult follow-up: Why does discovery `PASS` not guarantee readiness?

Because the status currently answers one narrow question: “Did I fetch a nonempty OpenAPI dictionary?” Capability readiness is separate. A future report should list mandatory operations, writable request schemas, usable identifiers, and observation paths, then produce a readiness status.

### Difficult follow-up: Could a validation 422 incorrectly produce `HELD`?

Yes. For rejection controls, a 4xx plus unchanged state proves the action did not mutate, but not necessarily that the intended business control was reached. The next improvement is to assert a documented error code/message or reproduce the request with only the prohibited condition changed.

### Difficult follow-up: Why not let the LLM decide whether a response means partial?

Financial controls need repeatable, auditable oracles. Model interpretation can help explain ambiguous output, but it should not transform missing evidence into a pass. A deterministic mapping to status fields/reason codes is testable and stable.

### Difficult follow-up: How do you know a journal belongs to this invoice?

Today the evidence comes from approval or a journal lookup using a returned journal ID or invoice ID, but the judge does not independently assert linkage. I would require invoice ID, journal ID, correlation ID, and immutable posting reference assertions before balance can be `HELD` in production.

### Difficult follow-up: What happens if the API redirects to another host?

The client uses `follow_redirects=False`, so it does not send the follow-up request or credentials elsewhere. The response is recorded as non-2xx evidence.

### Difficult follow-up: Does `--seed` make the run reproducible?

Not currently. It seeds `random`, but scenario choices do not consume it, and run suffixes use `secrets`. It is report metadata and a hook for future randomized selection, not full replay.

### Difficult follow-up: Why is the demo not proof of production compatibility?

It proves the agent can work end-to-end against one known contract and can detect injected flaws. A real target can differ in auth, schemas, state machines, pagination, error codes, data availability, and journal observability. That is why discovery evidence and inconclusive semantics matter.

## 17. Prioritized study list

Learn these in this order:

1. **The business controls.** Be able to explain received value, three-way match, duplicate scope, inactive vendors, partial receipt, AP exposure, and double-entry balance.
2. **The actual call graph.** Memorize CLI → runner → client/explorer → agents → judge → models/reporting.
3. **Verdict semantics.** Explain why 4xx alone is not enough and why missing evidence is inconclusive.
4. **`Decimal`.** Explain float risk, exact serialization, boundary testing, and currency scale.
5. **OpenAPI discovery.** Understand `$ref`, `allOf`, request schemas, safe GET data, SKU discovery, and the difference between discovery success and readiness.
6. **Agent boundaries.** Clearly state what the LLM does and does not do.
7. **Evidence and safety.** Origin restriction, redaction, timeouts, action budget, redirects, run IDs, and lack of cleanup/auth.
8. **The seven scenarios.** Know preconditions, calls, state checks, and every verdict branch.
9. **Reports and CI.** Know summary priority and exit codes 0/1/2/3.
10. **Testing strategy.** MockTransport, FastAPI demo tests, vulnerable profiles, concurrency, and the distinction between mock and live evidence.
11. **Limitations.** Be ready to discuss semantic 4xx validation, observability gaps, pagination/auth, volatile state, and GL linkage.
12. **Production evolution.** Test tenants, credentials, idempotency, cleanup, audit tracing, scheduling, and alerts.

## 18. Interview demonstration script

### 60-second version

1. Start `python -m p2p_demo --profile secure`.
2. Show `/docs` briefly and point out that OpenAPI is generated.
3. Run discovery and show the operations and SKU candidates.
4. Run `--mode all` and show happy `PASS`, seven `HELD`, and the report path.
5. Open one control in JSON and point to request, response, before/after evidence, and assertions.
6. Restart as `vulnerable-overpayment`.
7. Run adversarial mode, show `BREACHED`, exposure `0.00 → 10.01`, overall `FAIL`, and exit code `1`.

### Copy-and-paste demo commands

Terminal 1:

```powershell
cd "C:\Users\a_hos\Downloads\Interview Challenge QA"
.\.venv\Scripts\Activate.ps1
python -m p2p_demo --profile secure
```

Terminal 2:

```powershell
cd "C:\Users\a_hos\Downloads\Interview Challenge QA"
.\.venv\Scripts\Activate.ps1
python -m p2p_qa discover --base-url http://localhost:8000
python -m p2p_qa run --base-url http://localhost:8000 --mode all --report reports\interview-secure.json --max-actions 150
Get-Content reports\interview-secure.json
```

Then stop terminal 1, restart it with:

```powershell
python -m p2p_demo --profile vulnerable-overpayment
```

Run:

```powershell
python -m p2p_qa run --base-url http://localhost:8000 --mode adversarial --report reports\interview-breach.json --max-actions 150
$LASTEXITCODE
```

### What to say while the command runs

> Discovery learns the contract and finds a real SKU. The positive path establishes that the API can complete the business lifecycle. Each adversarial case gets its own state setup. Notice that the oracle is not “4xx means pass”: the report contains before and after state plus deterministic assertions. Now I switch off one API control. The same agent observes an invoice above received value becoming approved and vendor exposure increasing, so it returns a breach and a CI-failing exit code.

## 19. What works, what does not, and what to do next

### Works now

- Self-contained secure and intentionally vulnerable demo API.
- Runtime OpenAPI discovery and schema hashing.
- Safe GET seed/SKU discovery.
- Fixed valid workflow plus seven adversarial scenarios.
- Bounded schema repair and optional advisory LLM integration.
- Decimal-only financial judgment.
- Before/after evidence where the target exposes suitable reads.
- Pydantic-validated JSON reports and CI-oriented exit codes.
- Mock, judge, safety, reporting, demo, decimal, repeat, and concurrency tests.

### Does not yet work universally

- Arbitrary OpenAPI does not imply a compatible P2P API.
- Missing SKU, write schemas, collections, invoice detail, or journal data can legitimately prevent proof.
- The LLM does not dynamically choose actions.
- Authenticated APIs, cleanup, pagination, full JSON Schema, distributed state, and production scheduling are not implemented.
- Production auth, cleanup, pagination, full JSON Schema, distributed state, and scheduled monitoring still need implementation; these are not hidden by the passing demo suite.

### Next actions before the interview

1. Create a clean Python 3.11 virtual environment and reproduce the verified `27 passed` result with `python -m pytest`.
2. Run the secure all-mode demo and retain `reports/interview-secure.json`.
3. Run the vulnerable-overpayment demo and retain `reports/interview-breach.json`.
4. Practice the two-minute presentation and the explanation of one control's evidence.
5. Be explicit that the LLM plan is advisory and deterministic Python controls execution and verdicts.
6. Do not use an unrelated public OpenAPI URL for the full workflow.

## 20. Final interview checklist

- Start with the business risk, then explain the architecture; do not begin with the LLM.
- Say clearly that `p2p_qa` is the tester and `p2p_demo` is the disposable system under test.
- Trace CLI → runner → discovery/client → agents → deterministic judge → Pydantic report.
- Demonstrate both outcomes: secure `PASS`/seven `HELD`, then vulnerable overpayment `BREACHED`/exit `1`.
- Open one report control and show the request, response, before/after state, `Decimal` values, and assertions.
- Explain that a 4xx without non-mutation or business-specific evidence is not automatically `HELD`.
- Be honest that LLM planning is advisory and the present action graph is fixed Python.
- Close with the main production gaps: authentication, cleanup, idempotency, pagination, reason codes, journal linkage, and safe scheduling.
