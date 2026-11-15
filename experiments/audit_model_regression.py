"""Regenerate stored model studies and check for data-level regressions."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from spi_timing_model import (
    run_equal_budget_study,
    run_paired_jitter_study,
    run_reference_study,
)


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    with TemporaryDirectory() as temporary:
        regenerated = Path(temporary)
        run_reference_study(regenerated / "spi-reference-study.json")
        run_paired_jitter_study(regenerated / "spi-paired-jitter-study.json")
        run_equal_budget_study(regenerated / "spi-equal-budget-study.json")

        for name in (
            "spi-reference-study.json",
            "spi-paired-jitter-study.json",
            "spi-equal-budget-study.json",
        ):
            checked_in = ROOT / "docs" / "evidence" / name
            actual = regenerated / name
            assert load_json(actual) == load_json(checked_in), name
            print(f"PASS: regenerated {name}; JSON data match exactly")


if __name__ == "__main__":
    main()
