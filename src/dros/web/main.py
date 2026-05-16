from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from dros.settings import load_settings
from dros.web.app import create_app


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DROS Web server.")
    parser.add_argument("--settings", type=Path, default=Path("/etc/dros/settings.yaml"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    settings = load_settings(args.settings)
    uvicorn.run(
        create_app(settings),
        host=args.host or settings.web.host,
        port=args.port or settings.web.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
