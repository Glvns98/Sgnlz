"""Railway 24/7 production entrypoint for the Deriv Squeeze 15m Trade Management Engine."""

from __future__ import annotations

import os

from bot.app.main import cli_main


def main() -> int:
    mode = os.environ.get("ENGINE_MODE", "paper").strip().lower()
    if mode not in ("analysis", "paper", "live"):
        mode = "paper"
    return cli_main(["run", "--mode", mode, "--no-dashboard"])


if __name__ == "__main__":
    raise SystemExit(main())
