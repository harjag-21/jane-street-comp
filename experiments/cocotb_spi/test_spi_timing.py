"""Cocotb event-scheduler cross-check for the software SPI timing oracle."""

from dataclasses import dataclass
from pathlib import Path
import random
import sys

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, Timer
from cocotb.utils import get_sim_time

# Make the software oracle importable both from the repo root and when Make
# launches Cocotb with this directory as its working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spi_timing_model import Target, run_trial


@dataclass
class ObservedTransaction:
    received: int
    setup_valid: bool
    transition_ages_ns: list[float]


async def run_transaction(
    dut,
    low_cycles: int,
    delay_ns: float,
    transition_jitter_fs: tuple[int, ...] | None = None,
    target: Target | None = None,
) -> ObservedTransaction:
    target = target or Target()
    period_ns = target.clock_fs / 1_000_000
    high_cycles = target.high_cycles
    setup_ns = target.setup_fs / 1_000_000
    response = target.response
    response_bits = [(response >> bit) & 1 for bit in range(7, -1, -1)]
    transition_time_ps = {"value": int(get_sim_time(unit="ps"))}
    next_bit = {"index": 1}
    if transition_jitter_fs is not None and len(transition_jitter_fs) != 7:
        raise ValueError("one jitter value is required for each of seven transitions")

    dut.cs_n.value = 1
    dut.sclk.value = 0
    dut.mosi.value = 0
    dut.miso.value = response_bits[0]  # preload before the first rising sample
    await Timer(1, unit="ns")
    dut.cs_n.value = 0

    async def target():
        while True:
            await FallingEdge(dut.sclk)
            index = next_bit["index"]
            if index >= len(response_bits):
                continue
            edge_delay_ns = delay_ns
            if transition_jitter_fs is not None:
                edge_delay_ns += transition_jitter_fs[index - 1] / 1_000_000
            if edge_delay_ns < 0:
                raise ValueError("jittered target delay must be non-negative")
            if edge_delay_ns >= (low_cycles + high_cycles) * period_ns:
                raise ValueError(
                    "this serialized target driver requires each update to arrive "
                    "before the next falling edge"
                )
            edge_delay_ps = round(edge_delay_ns * 1_000)
            transition_time_ps["value"] = int(get_sim_time(unit="ps")) + edge_delay_ps
            await Timer(edge_delay_ns, unit="ns")
            dut.miso.value = response_bits[index]
            next_bit["index"] = index + 1

    target_task = cocotb.start_soon(target())
    sampled = []
    ages = []
    setup_valid = True
    try:
        for index in range(8):
            await Timer(low_cycles * period_ns, unit="ns")
            dut.sclk.value = 1
            await ReadOnly()  # sample after signal updates without advancing time
            sample_time_ps = int(get_sim_time(unit="ps"))
            age_ns = (sample_time_ps - transition_time_ps["value"]) / 1_000
            if index:
                ages.append(age_ns)
                setup_valid &= age_ns >= setup_ns
            sampled.append(int(dut.miso.value))
            await Timer(high_cycles * period_ns, unit="ns")
            dut.sclk.value = 0
    finally:
        target_task.cancel()
        dut.cs_n.value = 1

    received = 0
    for bit in sampled:
        received = (received << 1) | bit
    return ObservedTransaction(received, setup_valid, ages)


@cocotb.test()
async def test_spi_mode0_timing_boundary(dut):
    # 23 ns delay + 2 ns setup at T=10 ns gives a 3-cycle boundary.
    for low_cycles in range(1, 9):
        observed = await run_transaction(dut, low_cycles=low_cycles, delay_ns=23)
        predicted_pass = low_cycles >= 3
        actual_pass = observed.received == 0xAA and observed.setup_valid
        assert actual_pass == predicted_pass, (
            f"L={low_cycles}: received=0x{observed.received:02X}, "
            f"setup_valid={observed.setup_valid}, predicted={predicted_pass}"
        )
        # The first response bit is preloaded and does not depend on L.
        assert (observed.received >> 7) & 1 == 1

    # Equality case: 28 ns delay + 2 ns setup exactly fills a 3-cycle low phase.
    equality = await run_transaction(dut, low_cycles=3, delay_ns=28)
    assert equality.received == 0xAA
    assert equality.setup_valid
    assert all(age == 2 for age in equality.transition_ages_ns)

    dut._log.info(
        "Cocotb 1..8-cycle sweep matched oracle; equality byte=0x%02X "
        "with transition ages %s ns",
        equality.received,
        equality.transition_ages_ns,
    )


@cocotb.test()
async def test_seeded_transition_jitter_matches_python_oracle(dut):
    target = Target()
    jitter_step_fs = 1_000_000  # 1 ns, exactly representable at this timescale
    jitter_limit_fs = 5 * jitter_step_fs
    seed = 20261116
    rng = random.Random(seed)
    profiles = [
        tuple(rng.randint(-5, 5) * jitter_step_fs for _ in range(7))
        for _ in range(24)
    ]
    # Force both sample-late edges and the exact setup-equality edge case.
    profiles.extend([
        (jitter_limit_fs,) * 7,
        (6_000_000,) * 7,
        tuple(jitter_limit_fs if i % 2 else -jitter_limit_fs for i in range(7)),
    ])

    compared = 0
    late_edges = 0
    for low_cycles in range(1, 5):
        for profile_index, profile in enumerate(profiles):
            oracle = run_trial(
                low_cycles,
                target,
                transition_jitter_fs=profile,
            )
            observed = await run_transaction(
                dut,
                low_cycles=low_cycles,
                delay_ns=target.output_delay_fs / 1_000_000,
                transition_jitter_fs=profile,
                target=target,
            )
            expected_ages_ns = [
                (slack + target.setup_fs) / 1_000_000
                for slack in oracle.transition_slack_fs
            ]
            low_fs = low_cycles * target.clock_fs
            high_fs = target.high_cycles * target.clock_fs
            sample_period_fs = low_fs + high_fs
            event_trace_fs = []
            for edge_index, jitter_fs in enumerate(profile):
                intended_sample_fs = low_fs + (edge_index + 1) * sample_period_fs
                falling_edge_fs = low_fs + edge_index * sample_period_fs + high_fs
                arrival_fs = falling_edge_fs + target.output_delay_fs + jitter_fs
                event_trace_fs.append({
                    "fall_fs": falling_edge_fs,
                    "arrival_fs": arrival_fs,
                    "sample_fs": intended_sample_fs,
                    "setup_slack_fs": oracle.transition_slack_fs[edge_index],
                })
            context = (
                f"seed={seed}, profile={profile_index}, L={low_cycles}, "
                f"jitter_fs={profile}, expected=0x{oracle.observed:02X}, "
                f"actual=0x{observed.received:02X}, "
                f"event_trace_fs={event_trace_fs}, "
                f"expected_ages_ns={expected_ages_ns}, "
                f"actual_ages_ns={observed.transition_ages_ns}"
            )
            assert observed.received == oracle.observed, context
            assert observed.setup_valid == all(
                slack >= 0 for slack in oracle.transition_slack_fs
            ), context
            assert observed.transition_ages_ns == expected_ages_ns, context
            if profile_index == 24 and low_cycles == 3:
                assert observed.received == target.response
                assert observed.setup_valid  # exact 2 ns setup equality
            if profile_index == 25 and low_cycles == 3:
                assert observed.received == target.response
                assert not observed.setup_valid  # data arrives, but too near sample
            late_edges += sum(age < 0 for age in observed.transition_ages_ns)
            compared += 1

    assert compared == 4 * len(profiles)
    assert late_edges > 0, "the seeded cases must include transitions after sampling"
    dut._log.info(
        "Seeded jitter cross-check matched Python oracle for %d transactions "
        "(seed=%d, profiles=%d, late edges=%d)",
        compared,
        seed,
        len(profiles),
        late_edges,
    )
