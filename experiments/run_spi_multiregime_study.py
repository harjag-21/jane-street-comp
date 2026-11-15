"""Run a predeclared equal-budget SPI search comparison over six regimes."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from spi_timing_model import Target, run_equal_budget_study


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "evidence" / "spi-multiregime-equal-budget.json"
SEEDS_PER_REGIME = 1_000

# These regimes vary target delay and jitter width while keeping the oracle
# boundary interior (4 or 5 cycles), so both methods follow the full five-probe
# binary path and spend exactly 800 transactions. All values are fixed before
# running the comparison; they are not selected from observed outcomes.
REGIMES = [
    {"id": "delay18_jitter15", "delay_fs": 18_000_000,
     "jitter_fs": 15_176_495},
    {"id": "delay23_jitter6", "delay_fs": 23_000_000,
     "jitter_fs": 6_000_000},
    {"id": "delay23_jitter10", "delay_fs": 23_000_000,
     "jitter_fs": 10_000_000},
    {"id": "delay23_jitter15176495", "delay_fs": 23_000_000,
     "jitter_fs": 15_176_495},
    {"id": "delay23_jitter20", "delay_fs": 23_000_000,
     "jitter_fs": 20_000_000},
    {"id": "delay28_jitter15176495", "delay_fs": 28_000_000,
     "jitter_fs": 15_176_495},
]


def main() -> None:
    results = []
    with TemporaryDirectory() as temporary:
        temp = Path(temporary)
        for index, regime in enumerate(REGIMES):
            seed_start = 20_270_000 + index * SEEDS_PER_REGIME
            bootstrap_seed = 20_280_000 + index
            study_path = temp / f"{regime['id']}.json"
            target = Target(output_delay_fs=regime["delay_fs"])
            study = run_equal_budget_study(
                study_path,
                target=target,
                seeds=range(seed_start, seed_start + SEEDS_PER_REGIME),
                jitter_fs=regime["jitter_fs"],
                bootstrap_seed=bootstrap_seed,
            )
            assert study["oracle_boundary_cycles"] in (4, 5)
            assert all(run["fixed_trials"] == 800 for run in study["runs"])
            assert all(run["binary_trials"] == 800 for run in study["runs"])
            results.append({"regime": regime, "study": study})
            summary = study["summary"]
            print(
                f"{regime['id']}: boundary={study['oracle_boundary_cycles']}, "
                f"fixed={summary['fixed_correct']}/{SEEDS_PER_REGIME}, "
                f"binary={summary['binary_correct']}/{SEEDS_PER_REGIME}, "
                f"paired_delta={summary['paired_binary_minus_fixed_accuracy']}, "
                f"paired_CI={summary['paired_accuracy_difference_bootstrap_95']}"
            )

    output = {
        "evidence_class": "multi-regime paired software simulation",
        "design": {
            "regimes_predeclared": True,
            "regimes_per_selected_condition": SEEDS_PER_REGIME,
            "success_threshold": 0.95,
            "candidate_low_cycles": list(range(1, 9)),
            "fixed_repeats_per_setting": 100,
            "binary_repeats_per_probe": 160,
            "transactions_per_method_per_seed": 800,
            "confidence_intervals": "per-regime Wilson 95% for recovery; paired percentile seed bootstrap 95% for difference",
            "selection_rationale": (
                "Vary target delay and jitter width; retain only interior oracle "
                "boundaries 4 or 5 so binary search uses five probes and the "
                "fixed and binary methods consume equal transaction budgets."
            ),
        },
        "regimes": results,
        "limit": (
            "Synthetic event-model evidence only. The selected matrix does not "
            "cover boundaries outside 4-5 cycles, analog behavior, competition "
            "RTL, physical timing, board behavior, or silicon."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(output, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
