from __future__ import annotations

import argparse
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from rich.console import Console

from dros.settings import load_settings

console = Console()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DROS daemon.")
    parser.add_argument("--settings", type=Path, default=Path("/etc/dros/settings.yaml"))
    parser.add_argument("--once", action="store_true", help="Load settings and exit.")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    settings = load_settings(args.settings)
    console.print(
        f"[green]dros-daemon[/green] skeleton running with socket "
        f"{settings.daemon.socket_path}"
    )

    if args.once:
        return 0

    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    stop.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
