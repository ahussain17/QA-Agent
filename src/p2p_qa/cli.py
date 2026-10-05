from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console

from .client import ApiClient
from .reporting import ReportWriter, exit_code
from .runner import execute
from .schema import SchemaExplorer

app = typer.Typer(no_args_is_help=True, help="Autonomous QA agent for Purchase-to-Pay APIs.")
console = Console()


@app.command()
def discover(
    base_url: Annotated[str, typer.Option(help="P2P API origin.")] = "http://localhost:8000",
    timeout: Annotated[float, typer.Option(min=0.1)] = 10.0,
    max_actions: Annotated[int, typer.Option(min=1)] = 100,
    allow_nonlocal: Annotated[bool, typer.Option()] = False,
    verbose: Annotated[bool, typer.Option()] = False,
) -> None:
    """Discover OpenAPI operations and safe seed data without mutating the API."""
    load_dotenv()
    try:
        with ApiClient(base_url, timeout=timeout, max_actions=max_actions, allow_nonlocal=allow_nonlocal) as client:
            explorer = SchemaExplorer(client)
            report = explorer.discover()
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--base-url") from exc
    payload = report.model_dump(mode="json")
    if not verbose:
        payload.pop("inspected_for_sku", None)
    console.print_json(json.dumps(payload))


@app.command("run")
def run_command(
    base_url: Annotated[str, typer.Option(help="P2P API origin.")] = "http://localhost:8000",
    mode: Annotated[str, typer.Option(help="happy, adversarial, or all")] = "all",
    report: Annotated[Path, typer.Option(help="JSON report path.")] = Path("reports/qa-report.json"),
    timeout: Annotated[float, typer.Option(min=0.1)] = 10.0,
    max_actions: Annotated[int, typer.Option(min=1)] = 100,
    seed: Annotated[int | None, typer.Option()] = None,
    fail_on_inconclusive: Annotated[bool, typer.Option()] = False,
    allow_nonlocal: Annotated[bool, typer.Option()] = False,
    verbose: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run the happy path, adversarial controls, or both."""
    load_dotenv()
    if mode not in {"happy", "adversarial", "all"}:
        raise typer.BadParameter("must be happy, adversarial, or all", param_hint="--mode")
    try:
        result = execute(
            base_url,
            mode=mode,  # type: ignore[arg-type]
            timeout=timeout,
            max_actions=max_actions,
            seed=seed,
            allow_nonlocal=allow_nonlocal,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--base-url") from exc
    target = ReportWriter().write(result, report)
    ReportWriter().terminal_summary(result, console)
    console.print(f"Report: {target.resolve()}")
    if verbose:
        console.print_json(result.model_dump_json())
    raise typer.Exit(exit_code(result, fail_on_inconclusive))
