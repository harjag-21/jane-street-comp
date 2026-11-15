"""Cocotb event-scheduler cross-check for the software SPI timing oracle."""

from dataclasses import dataclass

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, Timer
from cocotb.utils import get_sim_time


@dataclass
class ObservedTransaction:
    received: int
    setup_valid: bool
    transition_ages_ns: list[float]


async def run_transaction(dut, low_cycles: int, delay_ns: float) -> ObservedTransaction:
    period_ns = 10
    high_cycles = 2
    setup_ns = 2
    response = 0xAA
    response_bits = [(response >> bit) & 1 for bit in range(7, -1, -1)]
    transition_time_ns = {"value": float(get_sim_time(unit="ns"))}
    next_bit = {"index": 1}

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
            transition_time_ns["value"] = float(get_sim_time(unit="ns")) + delay_ns
            await Timer(delay_ns, unit="ns")
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
            sample_time = float(get_sim_time(unit="ns"))
            age = sample_time - transition_time_ns["value"]
            if index:
                ages.append(age)
                setup_valid &= age >= setup_ns
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
