# SPDX-License-Identifier: Apache-2.0
"""RTL-level checks for the v0.1 UART-to-SPI bring-up contract."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ValueChange


CLKS_PER_BIT = 8


def crc8(frame):
    crc = 0
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def uart_send_byte(dut, byte):
    dut.ui_in.value = 0
    await ClockCycles(dut.clk, CLKS_PER_BIT)
    for bit in range(8):
        dut.ui_in.value = (byte >> bit) & 1
        await ClockCycles(dut.clk, CLKS_PER_BIT)
    dut.ui_in.value = 1
    await ClockCycles(dut.clk, CLKS_PER_BIT)


async def uart_send_frame(dut, frame):
    for byte in frame:
        await uart_send_byte(dut, byte)


async def wait_falling_bit(vector, bit):
    """Cocotb 2 permits value-change triggers on a packed vector, not a bit."""
    previous = (int(vector.value) >> bit) & 1
    while True:
        await ValueChange(vector)
        current = (int(vector.value) >> bit) & 1
        if previous and not current:
            return
        previous = current


async def uart_read_byte(dut):
    await wait_falling_bit(dut.uo_out, 0)
    await ClockCycles(dut.clk, CLKS_PER_BIT + CLKS_PER_BIT // 2)
    value = 0
    for bit in range(8):
        value |= ((int(dut.uo_out.value) >> 0) & 1) << bit
        await ClockCycles(dut.clk, CLKS_PER_BIT)
    return value


async def spi_target_aa(dut):
    """Mode-0 target that presents 0xAA before / after falling clock edges."""
    bits = [(0xAA >> bit) & 1 for bit in range(7, -1, -1)]
    await wait_falling_bit(dut.uo_out, 2)  # CS_N asserted
    dut.uio_in.value = bits[0]
    for index in range(1, 8):
        await wait_falling_bit(dut.uo_out, 1)
        dut.uio_in.value = bits[index]


async def read_response(dut):
    return [await uart_read_byte(dut) for _ in range(7)]


def request(opcode=0x01, request_id=0x31, low_cycles=3, mosi=0x55, bad_crc=False):
    frame = [0xA5, opcode, request_id, low_cycles & 0xFF, low_cycles >> 8, mosi]
    frame.append(crc8(frame) ^ (1 if bad_crc else 0))
    return frame


@cocotb.test()
async def valid_command_returns_target_byte_and_crc(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    target = cocotb.start_soon(spi_target_aa(dut))
    await uart_send_frame(dut, request(request_id=0x42, low_cycles=3, mosi=0x55))
    response = await read_response(dut)
    await target

    assert response[:6] == [0x5A, 0x42, 0, 3, 0, 0xAA]
    assert response[6] == crc8(response[:6])


@cocotb.test()
async def invalid_commands_return_status_without_spi_transaction(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)

    for expected_status, frame in (
        (1, request(bad_crc=True)),
        (2, request(opcode=0x7E)),
        (3, request(low_cycles=0)),
    ):
        await uart_send_frame(dut, frame)
        response = await read_response(dut)
        assert response[0] == 0x5A
        assert response[2] == expected_status
        assert response[5] == 0
        assert response[6] == crc8(response[:6])
        assert (int(dut.uo_out.value) >> 2) & 1 == 1, "CS_N must remain inactive for rejected commands"
