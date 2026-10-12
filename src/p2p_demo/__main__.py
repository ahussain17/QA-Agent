from __future__ import annotations

import argparse
import os

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local P2P demonstration API.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--profile",
        choices=["secure", "vulnerable-overpayment", "vulnerable-gl", "vulnerable-all"],
        default=os.getenv("P2P_DEMO_PROFILE", "secure"),
        help="Select secure behavior or an intentional vulnerability for demonstrations.",
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        create_app(args.profile),
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()

