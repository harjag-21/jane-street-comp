# SPDX-License-Identifier: Apache-2.0
"""End-to-end RTL checks for the v1 UART loader and programmable protocol engine."""

import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer, ValueChange

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tools.protocol_asm import (
    UART_OP_BEGIN_LOAD,
    UART_OP_COMMIT_LOAD,
    UART_OP_HALT,
    UART_OP_PING_INFO,
    UART_OP_POP_EVENT,
    UART_OP_READ_STATUS,
    UART_OP_START,
    UART_OP_WRITE_WORD,
    build_i2c_write_byte_program,
    build_spi_mode0_program,
    build_uart_tx_program,
    crc8_07,
    make_uart_request,
    program_checksums,
)

CLKS_PER_BIT = 8


async def reset_dut(dut):
    dut.ena.value = 1
    dut.ui_in.value = 1
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 6)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 4)


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
    previous = (int(vector.value) >> bit) & 1
    while True:
        await ValueChange(vector)
        current = (int(vector.value) >> bit) & 1
        if previous and not current:
            return
        previous = current


async def wait_rising_bit(vector, bit):
    previous = (int(vector.value) >> bit) & 1
    while True:
        await ValueChange(vector)
        current = (int(vector.value) >> bit) & 1
        if not previous and current:
            return
        previous = current


async def uart_read_byte(dut, vector, bit=0):
    await wait_falling_bit(vector, bit)
    await ClockCycles(dut.clk, CLKS_PER_BIT + CLKS_PER_BIT // 2)
    value = 0
    for idx in range(8):
        value |= ((int(vector.value) >> bit) & 1) << idx
        await ClockCycles(dut.clk, CLKS_PER_BIT)
    return value


async def read_response(dut):
    resp = [await uart_read_byte(dut, dut.uo_out, 0) for _ in range(8)]
    assert resp[0] == 0x5A, f"bad response sync: {resp}"
    assert resp[7] == crc8_07(resp[:7]), f"bad response crc: {resp}"
    return resp


async def send_cmd(dut, opcode, req_id=1, arg0=0, arg1=0, arg2=0, arg3=0, bad_crc=False):
    frame = make_uart_request(opcode, req_id, arg0, arg1, arg2, arg3, bad_crc=bad_crc)
    reader = cocotb.start_soon(read_response(dut))
    await uart_send_frame(dut, frame)
    resp = await reader
    assert resp[1] == (req_id & 0xFF)
    return resp


async def load_program(dut, words, bad_checksum=False):
    crc, s8 = program_checksums(words)
    resp = await send_cmd(
        dut,
        UART_OP_BEGIN_LOAD,
        req_id=0x10,
        arg0=len(words),
        arg1=crc ^ (0x55 if bad_checksum else 0),
        arg2=s8,
    )
    assert resp[2] == 0
    for addr, word in enumerate(words):
        resp = await send_cmd(
            dut,
            UART_OP_WRITE_WORD,
            req_id=0x20 + addr,
            arg0=addr,
            arg1=word & 0xFF,
            arg2=(word >> 8) & 0xFF,
        )
        assert resp[2] == 0
    return await send_cmd(dut, UART_OP_COMMIT_LOAD, req_id=0x7F)


async def wait_halted_or_fault(dut, max_cycles=4000):
    for _ in range(max_cycles):
        await ClockCycles(dut.clk, 1)
        state = (int(dut.uo_out.value) >> 3) & 0x3
        if state in (2, 3):
            return state
    raise AssertionError("Engine did not halt/fault within watchdog")


@cocotb.test()
async def test_loader_validation_recovery_and_deselect(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    # 1. PING_INFO
    resp = await send_cmd(dut, UART_OP_PING_INFO, req_id=1)
    assert resp[2] == 0
    assert resp[3:6] == [1, 64, 8]

    # 2. START rejected when no valid program loaded
    resp = await send_cmd(dut, UART_OP_START, req_id=2)
    assert resp[2] == 4  # BAD_STATE

    # 3. Rejected commands: BAD_CRC, BAD_OP, BAD_ARG
    assert (await send_cmd(dut, UART_OP_PING_INFO, req_id=3, bad_crc=True))[2] == 1
    assert (await send_cmd(dut, 0x7E, req_id=4))[2] == 2
    assert (await send_cmd(dut, UART_OP_BEGIN_LOAD, req_id=5, arg0=0))[2] == 3
    assert (await send_cmd(dut, UART_OP_BEGIN_LOAD, req_id=6, arg0=65))[2] == 3

    # 4. Partial truncated UART frame recovers after timeout (> 64 * CLKS_PER_BIT)
    await uart_send_frame(dut, [0xA5, UART_OP_PING_INFO, 0x07])
    await ClockCycles(dut.clk, 64 * CLKS_PER_BIT + 32)
    resp = await send_cmd(dut, UART_OP_PING_INFO, req_id=8)
    assert resp[2] == 0

    # 5. Checksum mismatch keeps prog_valid=0
    resp = await load_program(dut, [0xA000], bad_checksum=True)
    assert resp[2] == 5
    assert ((int(dut.uo_out.value) >> 2) & 1) == 0

    # 6. Illegal opcode traps into ENG_FAULT and clears uio_oe
    resp = await load_program(dut, [0x20FF, 0xD000])  # OE 0xFF, illegal 0xD
    assert resp[2] == 0
    resp = await send_cmd(dut, UART_OP_START, req_id=9)
    assert resp[2] == 0
    state = await wait_halted_or_fault(dut)
    assert state == 3
    assert int(dut.uio_oe.value) == 0

    # 7. Deselection (ena=0) clears prog_valid and forces reload
    assert ((int(dut.uo_out.value) >> 2) & 1) == 1
    dut.ena.value = 0
    await ClockCycles(dut.clk, 4)
    dut.ena.value = 1
    await ClockCycles(dut.clk, 4)
    assert ((int(dut.uo_out.value) >> 2) & 1) == 0
    assert (await send_cmd(dut, UART_OP_START, req_id=10))[2] == 4


@cocotb.test()
async def test_uart_tx_loaded_program(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    prog = build_uart_tx_program(0xA3, bit_cycles=8, pin=0)
    resp = await load_program(dut, prog)
    assert resp[2] == 0

    rx_task = cocotb.start_soon(uart_read_byte(dut, dut.uio_out, 0))
    resp = await send_cmd(dut, UART_OP_START, req_id=11)
    assert resp[2] == 0

    got = await rx_task
    assert got == 0xA3, f"expected 0xA3, got 0x{got:02x}"
    state = await wait_halted_or_fault(dut)
    assert state == 2
    assert int(dut.uio_oe.value) == 0


@cocotb.test()
async def test_spi_mode0_timing_sweep_over_uart(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    prog = build_spi_mode0_program(0x55)  # CS=0, SCLK=1, MOSI=2, MISO=3
    resp = await load_program(dut, prog)
    assert resp[2] == 0

    async def spi_target_delayed():
        mosi_bits = 0
        miso_bits = [(0xAA >> b) & 1 for b in range(7, -1, -1)]
        dut.uio_in.value = (miso_bits[0] << 3)
        await wait_falling_bit(dut.uio_out, 0)  # CS_N low
        for bit_idx in range(8):
            await wait_rising_bit(dut.uio_out, 1)
            mosi_bits = (mosi_bits << 1) | ((int(dut.uio_out.value) >> 2) & 1)
            await wait_falling_bit(dut.uio_out, 1)
            if bit_idx < 7:
                async def update_miso(val):
                    await Timer(23, unit="ns")
                    dut.uio_in.value = (val << 3)
                cocotb.start_soon(update_miso(miso_bits[bit_idx + 1]))
        return mosi_bits

    results = {}
    for low_cycles in (2, 3, 4):
        target = cocotb.start_soon(spi_target_delayed())
        resp = await send_cmd(dut, UART_OP_START, req_id=20 + low_cycles, arg0=low_cycles - 2)
        assert resp[2] == 0
        state = await wait_halted_or_fault(dut)
        assert state == 2
        mosi_seen = await target
        assert mosi_seen == 0x55
        st = await send_cmd(dut, UART_OP_READ_STATUS, req_id=30 + low_cycles)
        assert st[2] == 0
        results[low_cycles] = st[5]  # sample_reg
        ev = await send_cmd(dut, UART_OP_POP_EVENT, req_id=40 + low_cycles)
        assert ev[3] == 1 and ev[4] == st[5]

    assert results[2] != 0xAA, f"L=2 should fail 23ns boundary, got 0x{results[2]:02x}"
    assert results[3] == 0xAA, f"L=3 should pass 23ns+2ns boundary, got 0x{results[3]:02x}"
    assert results[4] == 0xAA, f"L=4 should pass, got 0x{results[4]:02x}"


@cocotb.test()
async def test_i2c_open_drain_ack_and_nack(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    async def drive_i2c_run(addr_byte, do_ack):
        prog = build_i2c_write_byte_program(addr_byte, half_cycles=4)
        assert (await load_program(dut, prog))[2] == 0

        target_drive_sda_low = False
        done = False

        def pin_state(idx):
            oe = (int(dut.uio_oe.value) >> idx) & 1
            out = (int(dut.uio_out.value) >> idx) & 1
            if idx == 1 and target_drive_sda_low:
                return 0
            return out if oe else 1

        async def open_drain_pullups():
            while not done:
                # Open-drain check: controller must never drive output high
                oe = int(dut.uio_oe.value) & 0x3
                out = int(dut.uio_out.value) & 0x3
                assert (oe & out) == 0, f"I2C drove pin high! oe={oe:02x} out={out:02x}"
                sda = pin_state(1)
                scl = pin_state(0)
                dut.uio_in.value = (sda << 1) | scl | 0xFC
                await ClockCycles(dut.clk, 1)

        async def i2c_target():
            nonlocal target_drive_sda_low
            # Wait START: SDA falls while SCL high
            while not (pin_state(0) == 1 and pin_state(1) == 0):
                await ClockCycles(dut.clk, 1)
            while pin_state(0) == 1:
                await ClockCycles(dut.clk, 1)
            rx = 0
            for _ in range(8):
                while pin_state(0) == 0:
                    await ClockCycles(dut.clk, 1)
                rx = (rx << 1) | pin_state(1)
                while pin_state(0) == 1:
                    await ClockCycles(dut.clk, 1)
            if do_ack:
                target_drive_sda_low = True
            while pin_state(0) == 0:
                await ClockCycles(dut.clk, 1)
            while pin_state(0) == 1:
                await ClockCycles(dut.clk, 1)
            target_drive_sda_low = False
            return rx

        pull_task = cocotb.start_soon(open_drain_pullups())
        tgt_task = cocotb.start_soon(i2c_target())
        resp = await send_cmd(dut, UART_OP_START, req_id=50)
        assert resp[2] == 0
        state = await wait_halted_or_fault(dut)
        done = True
        assert state == 2
        rx_byte = await tgt_task
        await pull_task
        assert rx_byte == addr_byte
        ev = await send_cmd(dut, UART_OP_POP_EVENT, req_id=51)
        assert ev[3] == 1
        return ev[4]

    assert await drive_i2c_run(0xA0, True) == 0xA0
    assert await drive_i2c_run(0xA2, False) == 0xF0
