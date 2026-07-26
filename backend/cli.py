"""Headless entrypoint: run a scenario and print the savings.

The fastest way to verify the loop without the API or the dashboard, and the
form used in CI.

    python backend/cli.py --scenario summer_week --controller baseline
    python backend/cli.py --scenario summer_week --controller llm --compare 1

Implemented in Milestone 1.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Command-line surface: scenario, controller, simulator, horizon, compare."""
    raise NotImplementedError("Milestone 1")


def main() -> int:
    """Run the requested scenario and print kWh, savings and comfort violations."""
    raise NotImplementedError("Milestone 1")


if __name__ == "__main__":
    raise SystemExit(main())
