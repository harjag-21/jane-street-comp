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
    PROG_DEPTH,
    UART_OP_BEGIN_LOAD,
    UART_OP_CFG_SCAN,
    UART_OP_COMMIT_LOAD,
    UART_OP_HALT,
    UART_OP_PATCH_WORD,
    UART_OP_PING_INFO,
    UART_OP_POP_EVENT,
    UART_OP_READ_CYCLE_CNT,
    UART_OP_READ_SCAN_FAIL,
    UART_OP_READ_SCAN_STATUS,
    UART_OP_READ_STATUS,
    UART_OP_START,
    UART_OP_START_SCAN,
    UART_OP_WRITE_WORD,
    assemble,
    bitrev8,
    build_i2c_write_byte_program,
    build_i2c_write_multibyte_program,
    build_spi_mode0_program,
    build_spi_multibyte_program,
    build_spi_program,
    build_spi_target_responder_program,
    build_uart_tx_multibyte_program,
    build_uart_tx_program,
    crc8_07,
    insn,
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
    resp = await load_program(dut, [0x20FF, 0xF000])  # OE 0xFF, illegal 0xF
    assert resp[2] == 0
    resp = await send_cmd(dut, UART_OP_START, req_id=9)
    assert resp[2] == 0
    state = await wait_halted_or_fault(dut)
    assert state == 3
    assert int(dut.uio_oe.value) == 0

    # 6b. OP_PATCH_WORD (0x19) mutates word 1 to HALT (0xA000) in 1 UART command
    resp = await send_cmd(dut, 0x19, req_id=10, arg0=1, arg1=0x00, arg2=0xA0)
    assert resp[2] == 0
    resp = await send_cmd(dut, UART_OP_START, req_id=11)
    assert resp[2] == 0
    state = await wait_halted_or_fault(dut)
    assert state == 2

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


@cocotb.test()
async def test_autonomous_scan_controller(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    # Bad scan state/arg checks before program load
    r = await send_cmd(dut, UART_OP_START_SCAN, req_id=1)
    assert r[2] == 0x04  # ST_BAD_STATE (prog_valid == 0 / scan_num_steps == 0)

    r = await send_cmd(dut, UART_OP_CFG_SCAN, req_id=2, arg0=1, arg1=1, arg2=0, arg3=0xAA)
    assert r[2] == 0x03  # ST_BAD_ARG (num_steps == 0)
    r = await send_cmd(dut, UART_OP_CFG_SCAN, req_id=3, arg0=1, arg1=1, arg2=33, arg3=0xAA)
    assert r[2] == 0x03  # ST_BAD_ARG (num_steps > 32)

    prog = build_spi_mode0_program(0x55)
    assert (await load_program(dut, prog))[2] == 0

    # Still bad state to START_SCAN before valid CFG_SCAN
    r = await send_cmd(dut, UART_OP_START_SCAN, req_id=4)
    assert r[2] == 0x04

    # Configure 6-point sweep run_param = 0..5 (low_cycles = 2..7), pass_mode=0 (sample_reg == 0xAA)
    r = await send_cmd(dut, UART_OP_CFG_SCAN, req_id=5, arg0=0, arg1=1, arg2=6, arg3=0xAA)
    assert r[2] == 0

    scan_stop = False

    async def spi_target():
        miso_bits = [(0xAA >> b) & 1 for b in range(7, -1, -1)]
        prev_cs = 1
        prev_sclk = 0
        bit_idx = 0
        dut.uio_in.value = 0xF7 | (miso_bits[0] << 3)
        while not scan_stop:
            await ClockCycles(dut.clk, 1)
            oe = int(dut.uio_oe.value)
            out = int(dut.uio_out.value)
            cs = (out & 1) if (oe & 1) else 1
            sclk = ((out >> 1) & 1) if (oe & 2) else 0
            if cs == 1:
                bit_idx = 0
                dut.uio_in.value = 0xF7 | (miso_bits[0] << 3)
            else:
                if prev_cs == 1 and cs == 0:
                    bit_idx = 0
                    dut.uio_in.value = 0xF7 | (miso_bits[0] << 3)
                if prev_sclk == 1 and sclk == 0:
                    bit_idx += 1
                    if bit_idx < 8:
                        async def update_miso(val):
                            await Timer(23, unit="ns")
                            if not scan_stop:
                                dut.uio_in.value = 0xF7 | (val << 3)
                        cocotb.start_soon(update_miso(miso_bits[bit_idx]))
            prev_cs = cs
            prev_sclk = sclk
        dut.uio_in.value = 0xFF

    tgt_task = cocotb.start_soon(spi_target())

    r = await send_cmd(dut, UART_OP_START_SCAN, req_id=6)
    assert r[2] == 0
    assert ((int(dut.uo_out.value) >> 6) & 0x3) != 0  # scan_active or already scan_done

    if ((int(dut.uo_out.value) >> 7) & 1) == 0:
        for _ in range(25000):
            await ClockCycles(dut.clk, 1)
            if ((int(dut.uo_out.value) >> 7) & 1) == 1:
                break
        else:
            raise AssertionError("Autonomous scan did not complete (uo_out[7] never went high)")

    scan_stop = True
    await tgt_task

    assert ((int(dut.uo_out.value) >> 6) & 1) == 0  # scan_active cleared

    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=8, arg0=0)
    st1 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=9, arg0=1)
    assert st0[2] == 0 and st1[2] == 0
    assert st0[3] == 0x02  # scan_done=1, scan_active=0
    assert st0[4] == 6     # steps_done
    assert st0[5] == 4     # pass_count (run_param = 2,3,4,5)
    assert st0[6] == 2     # fail_count (run_param = 0,1)
    bitmap = st1[3] | (st1[4] << 8) | (st1[5] << 16) | (st1[6] << 24)
    assert bitmap == 0b111100, f"unexpected bitmap 0b{bitmap:b}"

    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=10, arg0=0)
    f1 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=11, arg0=1)
    assert f0[2] == 0 and f1[2] == 0
    assert (f0[3] >> 7) == 1        # first_fail_valid
    assert (f0[3] & 0x3F) == 0       # first_fail_idx = 0
    assert f0[4] == 0                # first_fail_param = 0
    assert (f0[5] >> 6) == 2         # ENG_HALTED
    assert f0[6] != 0xAA             # failed sample_reg != 0xAA
    fail_cycles = f1[3] | (f1[4] << 8)
    assert fail_cycles > 0
    assert f1[5] == f0[6]            # last_mark_tag matched sample_reg for MODE0 MARK

    # Verify pass_mode=1 (MARK tag mode) on all-pass range run_param=2..5 (4 steps)
    # Note: OP_CFG_SCAN clears prior scan_done (uo_out[7]) before START_SCAN.
    scan_stop = False
    tgt_task = cocotb.start_soon(spi_target())
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=12, arg0=2, arg1=1, arg2=(1 << 6) | 4, arg3=0xAA))[2] == 0
    assert ((int(dut.uo_out.value) >> 7) & 1) == 0  # scan_done cleared by CFG_SCAN
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=13))[2] == 0
    for _ in range(25000):
        await ClockCycles(dut.clk, 1)
        if ((int(dut.uo_out.value) >> 7) & 1) == 1:
            break
    scan_stop = True
    await tgt_task
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=14, arg0=0)
    st1 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=15, arg0=1)
    assert st0[4] == 4 and st0[5] == 4 and st0[6] == 0
    assert (st1[3] & 0xF) == 0b1111
    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=16, arg0=0)
    assert (f0[3] >> 7) == 0  # No failure

    # Verify busy-guard (ST_BAD_STATE) for all prohibited commands & OP_HALT abort
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=17, arg0=20, arg1=4, arg2=32, arg3=0xAA))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=18))[2] == 0
    assert ((int(dut.uo_out.value) >> 6) & 1) == 1
    for rid, op in (
        (19, UART_OP_BEGIN_LOAD),
        (20, UART_OP_WRITE_WORD),
        (21, UART_OP_COMMIT_LOAD),
        (22, UART_OP_START),
        (23, UART_OP_PATCH_WORD),
        (24, UART_OP_CFG_SCAN),
        (25, UART_OP_START_SCAN),
    ):
        busy = await send_cmd(dut, op, req_id=rid, arg0=1, arg1=1, arg2=1)
        assert busy[2] == 0x04, f"op 0x{op:02x} allowed during scan_active"
    assert (await send_cmd(dut, UART_OP_HALT, req_id=26))[2] == 0
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=27, arg0=0)
    assert (st0[3] & 0x3) == 0x0  # scan_active=0, scan_done=0
    assert st0[4] < 32

    # Verify 12-bit param_start[11:8], param_step > 1, and pass_mode=1 failure on empty FIFO
    no_mark_prog = assemble(["WAIT_PARAM 0", "HALT 0"])
    assert (await load_program(dut, no_mark_prog))[2] == 0
    # param_start = 0x102 (arg0=0x02 in CFG, arg0=0x01 in START_SCAN), param_step = 3, 2 steps -> 0x102, 0x105
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=28, arg0=0x02, arg1=3, arg2=(1 << 6) | 2, arg3=0x00))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=29, arg0=0x01))[2] == 0
    for _ in range(10000):
        await ClockCycles(dut.clk, 1)
        if ((int(dut.uo_out.value) >> 7) & 1) == 1:
            break
    else:
        raise AssertionError("12-bit stepped scan did not complete")
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=30, arg0=0)
    assert st0[4] == 2 and st0[5] == 0 and st0[6] == 2  # event_count == 0 fails even when expected == 0x00
    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=31, arg0=0)
    f1 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=32, arg0=1)
    assert (f0[3] >> 7) == 1 and (f0[3] & 0x3F) == 0
    assert (f0[4] | ((f0[5] & 0xF) << 8)) == 0x102  # 12-bit first_fail_param preserved
    assert (f1[6] & 0xF) == 0  # first_fail_evcnt == 0

    # Verify maximum 32-step scan completion, full 0xFFFFFFFF pass bitmap, and uio_oe == 0
    max_prog = assemble(["OE 0x00", "SAMPLE 3, 0", "MARK 0xA5, 0", "HALT 0"])
    assert (await load_program(dut, max_prog))[2] == 0
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=33, arg0=0, arg1=1, arg2=(1 << 6) | 32, arg3=0xA5))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=34, arg0=0))[2] == 0
    for _ in range(10000):
        await ClockCycles(dut.clk, 1)
        if ((int(dut.uo_out.value) >> 7) & 1) == 1:
            break
    else:
        raise AssertionError("32-step scan did not complete")
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=35, arg0=0)
    st1 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=36, arg0=1)
    assert st0[3] == 0x02 and st0[4] == 32 and st0[5] == 32 and st0[6] == 0
    bitmap32 = st1[3] | (st1[4] << 8) | (st1[5] << 16) | (st1[6] << 24)
    assert bitmap32 == 0xFFFFFFFF
    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=37, arg0=0)
    assert (f0[3] >> 7) == 0
    assert int(dut.uio_oe.value) == 0

    # Verify 12-bit modular parameter wraparound: 0xFFE + 3 -> 0x001 on step 1
    wrap_prog = assemble(["WAIT_PARAM 0", "SAMPLE 3, 0", "MARK_SAMPLE", "HALT 0"])
    assert (await load_program(dut, wrap_prog))[2] == 0
    dut.uio_in.value = 0xFF  # pin 3 = 1 -> step 0 (0xFFE) samples 0x01 and passes
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=38, arg0=0xFE, arg1=3, arg2=2, arg3=0x01))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=39, arg0=0x0F))[2] == 0
    # Wait for trial 0 (0xFFE cycles) to finish via trial_active falling edge, then drop pin 3 = 0 for trial 1 (0x001)
    await wait_falling_bit(dut.uo_out, 1)
    dut.uio_in.value = 0xF7
    for _ in range(5000):
        await ClockCycles(dut.clk, 1)
        if ((int(dut.uo_out.value) >> 7) & 1) == 1:
            break
    else:
        raise AssertionError("Wraparound scan did not complete")
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=40, arg0=0)
    st1 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=41, arg0=1)
    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=42, arg0=0)
    assert st0[4] == 2 and st0[5] == 1 and st0[6] == 1
    assert (st1[3] & 0x3) == 0b01
    assert (f0[3] >> 7) == 1 and (f0[3] & 0x3F) == 1
    assert (f0[4] | ((f0[5] & 0xF) << 8)) == 0x001

    # Verify ena=0 deselection resets scan_active and scan_done cleanly
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=43, arg0=0x40, arg1=8, arg2=16, arg3=0x00))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=44, arg0=0x02))[2] == 0
    assert ((int(dut.uo_out.value) >> 6) & 1) == 1
    dut.ena.value = 0
    await ClockCycles(dut.clk, 4)
    assert ((int(dut.uo_out.value) >> 6) & 0x3) == 0
    dut.ena.value = 1
    await ClockCycles(dut.clk, 4)
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=45, arg0=0)
    assert st0[3] == 0 and st0[4] == 0 and st0[5] == 0 and st0[6] == 0

    # Verify mid-scan synchronous reset (rst_n=0) clears active scan, prog_valid, and rejects START_SCAN
    assert (await load_program(dut, no_mark_prog))[2] == 0
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=46, arg0=0x20, arg1=2, arg2=16, arg3=0x00))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=47, arg0=0x00))[2] == 0
    assert ((int(dut.uo_out.value) >> 6) & 1) == 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 4)
    assert ((int(dut.uo_out.value) >> 6) & 0x3) == 0
    assert int(dut.uio_oe.value) == 0
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 4)
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=48, arg0=0)
    assert st0[3] == 0 and st0[4] == 0 and st0[5] == 0 and st0[6] == 0
    bad = await send_cmd(dut, UART_OP_START_SCAN, req_id=49, arg0=0x00)
    assert bad[2] == 0x04

    # Verify ENG_FAULT fails cleanly inside scan even if sample_reg == expected_val == 0x00
    assert (await load_program(dut, [0xF000]))[2] == 0
    assert (await send_cmd(dut, UART_OP_CFG_SCAN, req_id=50, arg0=0, arg1=1, arg2=2, arg3=0x00))[2] == 0
    assert (await send_cmd(dut, UART_OP_START_SCAN, req_id=51, arg0=0))[2] == 0
    for _ in range(1000):
        await ClockCycles(dut.clk, 1)
        if ((int(dut.uo_out.value) >> 7) & 1) == 1:
            break
    else:
        raise AssertionError("ENG_FAULT scan did not complete")
    st0 = await send_cmd(dut, UART_OP_READ_SCAN_STATUS, req_id=52, arg0=0)
    f0 = await send_cmd(dut, UART_OP_READ_SCAN_FAIL, req_id=53, arg0=0)
    assert st0[4] == 2 and st0[5] == 0 and st0[6] == 2
    assert (f0[3] >> 7) == 1 and (f0[3] & 0x3F) == 0
    assert (f0[5] >> 6) == 3  # ENG_FAULT recorded despite sample_reg == 0x00


@cocotb.test()
async def test_spi_modes_1_to_3_programs(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    for mode in (1, 2, 3):
        cpol = (mode >> 1) & 1
        cpha = mode & 1
        prog = build_spi_program(0x55, mode=mode)
        assert len(prog) <= PROG_DEPTH
        assert (await load_program(dut, prog))[2] == 0

        async def spi_target_mode_delayed():
            mosi_bits = 0
            miso_bits = [(0xAA >> b) & 1 for b in range(7, -1, -1)]
            dut.uio_in.value = 0 if cpha else (miso_bits[0] << 3)
            await wait_falling_bit(dut.uio_out, 0)  # CS_N low
            for bit_idx in range(8):
                # Wait for leading SCLK transition
                if cpol == 0:
                    await wait_rising_bit(dut.uio_out, 1)
                else:
                    await wait_falling_bit(dut.uio_out, 1)

                if cpha == 0:
                    mosi_bits = (mosi_bits << 1) | ((int(dut.uio_out.value) >> 2) & 1)
                else:
                    async def update_miso_lead(val):
                        await Timer(23, unit="ns")
                        dut.uio_in.value = val << 3
                    cocotb.start_soon(update_miso_lead(miso_bits[bit_idx]))

                # Wait for trailing SCLK transition
                if cpol == 0:
                    await wait_falling_bit(dut.uio_out, 1)
                else:
                    await wait_rising_bit(dut.uio_out, 1)

                if cpha == 1:
                    mosi_bits = (mosi_bits << 1) | ((int(dut.uio_out.value) >> 2) & 1)
                elif bit_idx < 7:
                    async def update_miso_trail(val):
                        await Timer(23, unit="ns")
                        dut.uio_in.value = val << 3
                    cocotb.start_soon(update_miso_trail(miso_bits[bit_idx + 1]))
            return mosi_bits

        results = {}
        for low_cycles in (2, 3, 4):
            target = cocotb.start_soon(spi_target_mode_delayed())
            resp = await send_cmd(dut, UART_OP_START, req_id=60 + mode * 10 + low_cycles, arg0=low_cycles - 2)
            assert resp[2] == 0
            assert await wait_halted_or_fault(dut) == 2
            assert await target == 0x55
            st = await send_cmd(dut, UART_OP_READ_STATUS, req_id=70 + mode * 10 + low_cycles)
            assert st[2] == 0
            results[low_cycles] = st[5]

        assert results[2] != 0xAA, f"SPI mode {mode} L=2 should fail 23ns boundary"
        assert results[3] == 0xAA, f"SPI mode {mode} L=3 should pass 23ns boundary, got 0x{results[3]:02x}"
        assert results[4] == 0xAA, f"SPI mode {mode} L=4 should pass, got 0x{results[4]:02x}"


@cocotb.test()
async def test_spi_target_responder_end_to_end(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset_dut(dut)

    prog = build_spi_target_responder_program(0x5A, max_wait=0)
    assert len(prog) <= PROG_DEPTH
    assert (await load_program(dut, prog))[2] == 0

    async def external_spi_master(mosi_byte: int, half_clks: int = 6):
        dut.uio_in.value = 0xF9  # CS_N=1, SCLK=0, MOSI=0
        for _ in range(2500):
            await ClockCycles(dut.clk, 1)
            if (int(dut.uio_oe.value) >> 3) & 1:
                break
        else:
            raise AssertionError("Responder never asserted MISO OE")

        await ClockCycles(dut.clk, 2)
        miso_rx = 0
        mosi_bit = (mosi_byte >> 7) & 1
        dut.uio_in.value = 0xF8 | (mosi_bit << 2)  # CS_N=0, SCLK=0
        await ClockCycles(dut.clk, half_clks)
        for bit_idx in range(8):
            cur_mosi = (mosi_byte >> (7 - bit_idx)) & 1
            dut.uio_in.value = 0xF8 | (cur_mosi << 2) | 0x2  # SCLK=1
            miso_rx = (miso_rx << 1) | ((int(dut.uio_out.value) >> 3) & 1)
            await ClockCycles(dut.clk, half_clks)
            next_mosi = (mosi_byte >> (6 - bit_idx)) & 1 if bit_idx < 7 else 0
            dut.uio_in.value = 0xF8 | (next_mosi << 2)  # SCLK=0
            await ClockCycles(dut.clk, half_clks)
        dut.uio_in.value = 0xF9  # CS_N=1
        return miso_rx

    ext_task = cocotb.start_soon(external_spi_master(0xA5, half_clks=6))
    assert (await send_cmd(dut, UART_OP_START, req_id=101, arg0=120))[2] == 0
    assert await wait_halted_or_fault(dut) == 2
    ext_got_miso = await ext_task
    assert ext_got_miso == 0x5A, f"External controller expected MISO 0x5A, got 0x{ext_got_miso:02x}"
    st = await send_cmd(dut, UART_OP_READ_STATUS, req_id=102)
    assert st[5] == 0xA5
    ev = await send_cmd(dut, UART_OP_POP_EVENT, req_id=103)
    assert ev[3] == 1 and ev[4] == 0xA5

    # Timeout when external controller keeps CS_N=1 beyond run_param=16
    dut.uio_in.value = 0xFF
    assert (await send_cmd(dut, UART_OP_START, req_id=104, arg0=16))[2] == 0
    assert await wait_halted_or_fault(dut) == 2
    ev_to = await send_cmd(dut, UART_OP_POP_EVENT, req_id=105)
    assert ev_to[3] == 1 and ev_to[4] == 0xEE


@cocotb.test()
async def test_i2c_100khz_and_400khz_at_50mhz(dut):
    # Nominal 50 MHz ASIC clock (T = 20 ns per info.yaml clock_hz=50000000)
    cocotb.start_soon(Clock(dut.clk, 20, unit="ns").start())
    await reset_dut(dut)

    async def run_rate_check(half_cycles: int, expected_period_cycles: int):
        prog = build_i2c_write_byte_program(0xA0, half_cycles=half_cycles, ack_tag=0xAC, nack_tag=0xDE)
        assert len(prog) <= PROG_DEPTH
        assert (await load_program(dut, prog))[2] == 0

        target_drive_sda_low = False
        done = False
        scl_low_lens = []
        scl_high_lens = []

        def pin_state(idx):
            oe = (int(dut.uio_oe.value) >> idx) & 1
            out = (int(dut.uio_out.value) >> idx) & 1
            if idx == 1 and target_drive_sda_low:
                return 0
            return out if oe else 1

        async def open_drain_and_scl_monitor():
            started = False
            prev_scl = 1
            run_len = 0
            while not done:
                oe = int(dut.uio_oe.value) & 0x3
                out = int(dut.uio_out.value) & 0x3
                assert (oe & out) == 0, f"I2C drove pin high! oe={oe:02x} out={out:02x}"
                scl = pin_state(0)
                sda = pin_state(1)
                dut.uio_in.value = (sda << 1) | scl | 0xFC
                if not started and scl == 0:
                    started = True
                    prev_scl = 0
                    run_len = 1
                elif started:
                    if scl == prev_scl:
                        run_len += 1
                    else:
                        if prev_scl == 0:
                            scl_low_lens.append(run_len)
                        else:
                            scl_high_lens.append(run_len)
                        prev_scl = scl
                        run_len = 1
                await ClockCycles(dut.clk, 1)

        async def i2c_target():
            nonlocal target_drive_sda_low
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
            target_drive_sda_low = True
            while pin_state(0) == 0:
                await ClockCycles(dut.clk, 1)
            while pin_state(0) == 1:
                await ClockCycles(dut.clk, 1)
            target_drive_sda_low = False
            return rx

        mon = cocotb.start_soon(open_drain_and_scl_monitor())
        tgt = cocotb.start_soon(i2c_target())
        assert (await send_cmd(dut, UART_OP_START, req_id=110 + half_cycles))[2] == 0
        assert await wait_halted_or_fault(dut, max_cycles=10000) == 2
        done = True
        await mon
        rx = await tgt
        assert rx == 0xA0
        ev = await send_cmd(dut, UART_OP_POP_EVENT, req_id=120 + half_cycles)
        assert ev[3] == 1 and ev[4] == 0xAC
        # Steady-state data bits 1..7 must match exact period = half_cycles*2 + 1 cycles
        steady_periods = [scl_low_lens[i] + scl_high_lens[i] for i in range(1, 8)]
        assert all(p == expected_period_cycles for p in steady_periods), f"half={half_cycles} periods={steady_periods}"

    # Nominal 400.000 kHz at 50 MHz: 125 cycles/bit (63 low = 1.26 us < 1.30 us Fast-mode min, 62 high)
    await run_rate_check(half_cycles=62, expected_period_cycles=125)
    # Fast-mode compliant 387.597 kHz at 50 MHz: 129 cycles/bit (65 low = 1.30 us >= 1.30 us, 64 high = 1.28 us)
    await run_rate_check(half_cycles=64, expected_period_cycles=129)
    # 99.800 kHz Standard-mode <= 100 kHz at 50 MHz: 501 cycles/bit (251 low + 250 high)
    await run_rate_check(half_cycles=250, expected_period_cycles=501)


@cocotb.test()
async def test_tx_shifter_multibyte_uart_spi_and_patch(dut):
    """Test 9: Transmit shifter (LD_TX / SHIFT_TX) multi-byte UART & SPI (MSB/LSB) + OP_PATCH_WORD + reserved 0xE fault."""
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # 1) 2-byte UART TX via LD_TX + SHIFT_TX (22 words vs 44 unrolled) in LSB-first and MSB-first orders
    uart_lsb_prog, uart_ld_addrs = build_uart_tx_multibyte_program([0xA3, 0x5C], bit_cycles=8, pin=0, lsb_first=True)
    uart_msb_prog, _ = build_uart_tx_multibyte_program([0xA3, 0x5C], bit_cycles=8, pin=0, lsb_first=False)
    assert len(uart_lsb_prog) == 22 and len(uart_lsb_prog) < 44
    assert uart_ld_addrs == [3, 12]
    assert (await load_program(dut, uart_lsb_prog))[2] == 0

    async def capture_uart_bytes(count: int, lsb_first: bool):
        out_bytes = []
        for _ in range(count):
            while (int(dut.uio_oe.value) & 1) == 0 or (int(dut.uio_out.value) & 1) == 1:
                await ClockCycles(dut.clk, 1)
            await ClockCycles(dut.clk, 4)
            assert (int(dut.uio_out.value) & 1) == 0
            val = 0
            for b in range(8):
                await ClockCycles(dut.clk, 8)
                bit = int(dut.uio_out.value) & 1
                if lsb_first:
                    val |= bit << b
                else:
                    val = (val << 1) | bit
            await ClockCycles(dut.clk, 8)
            assert (int(dut.uio_out.value) & 1) == 1
            out_bytes.append(val)
        return out_bytes

    cap = cocotb.start_soon(capture_uart_bytes(2, lsb_first=True))
    assert (await send_cmd(dut, UART_OP_START, req_id=140))[2] == 0
    rx_bytes = await cap
    assert await wait_halted_or_fault(dut) == 2
    assert rx_bytes == [0xA3, 0x5C]

    # Patch second byte's LD_TX word (addr 12) from 0x5C to 0x96 via OP_PATCH_WORD (1 command, no reload)
    new_ld_word = insn("LD_TX", (1 << 8) | 0x96)
    p_rsp = await send_cmd(
        dut,
        UART_OP_PATCH_WORD,
        req_id=141,
        arg0=uart_ld_addrs[1],
        arg1=new_ld_word & 0xFF,
        arg2=(new_ld_word >> 8) & 0xFF,
    )
    assert p_rsp[2] == 0
    cap_p = cocotb.start_soon(capture_uart_bytes(2, lsb_first=True))
    assert (await send_cmd(dut, UART_OP_START, req_id=142))[2] == 0
    assert (await cap_p) == [0xA3, 0x96]
    assert await wait_halted_or_fault(dut) == 2

    # MSB-first 2-byte UART TX
    assert (await load_program(dut, uart_msb_prog))[2] == 0
    cap_m = cocotb.start_soon(capture_uart_bytes(2, lsb_first=False))
    assert (await send_cmd(dut, UART_OP_START, req_id=143))[2] == 0
    assert (await cap_m) == [0xA3, 0x5C]
    assert await wait_halted_or_fault(dut) == 2

    # 2) 2-byte parameterized SPI via LD_TX + SHIFT_TX (20 words vs 70 unrolled) in MSB-first and LSB-first orders
    spi_msb_prog, spi_ld_addrs = build_spi_multibyte_program([0xA5, 0x3C], lsb_first=False)
    spi_lsb_prog, _ = build_spi_multibyte_program([0xA5, 0x3C], lsb_first=True)
    assert len(spi_msb_prog) == 20 and len(spi_msb_prog) < 70
    assert spi_ld_addrs == [3, 10]
    assert (await load_program(dut, spi_msb_prog))[2] == 0

    async def spi_2byte_target(miso_bytes, lsb_first: bool):
        miso_bits = []
        for mb in miso_bytes:
            for b in (range(8) if lsb_first else range(7, -1, -1)):
                miso_bits.append((mb >> b) & 1)
        mosi_bits = []
        prev_sclk = 0
        bit_idx = 0
        dut.uio_in.value = (miso_bits[0] << 3) | 0xF7
        while True:
            await ValueChange(dut.uio_out)
            val = int(dut.uio_out.value)
            cs_n = val & 1
            sclk = (val >> 1) & 1
            mosi = (val >> 2) & 1
            if cs_n == 0:
                if prev_sclk == 0 and sclk == 1:
                    mosi_bits.append(mosi)
                    if len(mosi_bits) == 16:
                        prev_sclk = sclk
                        break
                elif prev_sclk == 1 and sclk == 0 and bit_idx + 1 < len(miso_bits):
                    bit_idx += 1
                    dut.uio_in.value = (miso_bits[bit_idx] << 3) | 0xF7
            prev_sclk = sclk
        out = []
        for by in range(2):
            v = 0
            for b in range(8):
                bit = mosi_bits[by * 8 + b]
                if lsb_first:
                    v |= bit << b
                else:
                    v = (v << 1) | bit
            out.append(v)
        return out

    tgt_msb = cocotb.start_soon(spi_2byte_target([0x5A, 0xC3], lsb_first=False))
    assert (await send_cmd(dut, UART_OP_START, req_id=144, arg0=0x02, arg1=0x00))[2] == 0
    assert (await tgt_msb) == [0xA5, 0x3C]
    assert await wait_halted_or_fault(dut) == 2
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=145)
    ev1 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=146)
    assert ev0[3] == 2 and ev0[4] == 0x5A
    assert ev1[3] == 1 and ev1[4] == 0xC3

    # Patch byte 0 (addr 3) from 0xA5 to 0xF0 via OP_PATCH_WORD and re-run
    patch_spi = insn("LD_TX", 0xF0)
    assert (
        await send_cmd(
            dut,
            UART_OP_PATCH_WORD,
            req_id=147,
            arg0=spi_ld_addrs[0],
            arg1=patch_spi & 0xFF,
            arg2=(patch_spi >> 8) & 0xFF,
        )
    )[2] == 0
    tgt_patch = cocotb.start_soon(spi_2byte_target([0x11, 0x22], lsb_first=False))
    assert (await send_cmd(dut, UART_OP_START, req_id=148, arg0=0x02, arg1=0x00))[2] == 0
    assert (await tgt_patch) == [0xF0, 0x3C]
    assert await wait_halted_or_fault(dut) == 2

    # LSB-first 2-byte SPI
    assert (await load_program(dut, spi_lsb_prog))[2] == 0
    tgt_lsb = cocotb.start_soon(spi_2byte_target([0x5A, 0xC3], lsb_first=True))
    assert (await send_cmd(dut, UART_OP_START, req_id=149, arg0=0x02, arg1=0x00))[2] == 0
    assert (await tgt_lsb) == [0xA5, 0x3C]
    assert await wait_halted_or_fault(dut) == 2

    # 3) Reserved OP_TX encoding (0xE400) must trap to ENG_FAULT (3) and drop uio_oe to 0x00
    fault_prog = [insn("OE", 0xFF), 0xE400]
    assert (await load_program(dut, fault_prog))[2] == 0
    assert (await send_cmd(dut, UART_OP_START, req_id=150))[2] == 0
    assert await wait_halted_or_fault(dut) == 3
    assert int(dut.uio_oe.value) == 0x00


@cocotb.test()
async def test_i2c_multibyte_open_drain_100k_and_fast_compliant(dut):
    """Test 10: Open-drain LD_TX_OD + SHIFT_TX multi-byte I2C at 100k & compliant Fast-mode with ACK/NACK & cycle-level OD check."""
    clock = Clock(dut.clk, 20, unit="ns")  # exact 50 MHz (20 ns/cycle)
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # 1-byte looped open-drain takes 30/64 words (vs 62 unrolled); 2-byte parameterized I2C write takes 49/64 words
    prog1, ld_addrs1 = build_i2c_write_multibyte_program([0xA0], half_cycles=None)
    assert len(prog1) == 30 and ld_addrs1 == [7]

    prog2, ld_addrs2 = build_i2c_write_multibyte_program([0xA0, 0x55], half_cycles=None)
    assert len(prog2) == 49 and len(prog2) <= PROG_DEPTH
    assert ld_addrs2 == [7, 26]

    async def run_i2c_mb(expected_bytes, ack_mask, run_param: int, expected_steady_period: int):
        target_drive_sda_low = False
        done = False
        scl_low_lens = []
        scl_high_lens = []

        def pin_state(idx):
            oe = (int(dut.uio_oe.value) >> idx) & 1
            out = (int(dut.uio_out.value) >> idx) & 1
            if idx == 1 and target_drive_sda_low:
                return 0
            return out if oe else 1

        async def od_and_scl_monitor():
            started = False
            prev_scl = 1
            run_len = 0
            while not done:
                oe = int(dut.uio_oe.value) & 0x3
                out = int(dut.uio_out.value) & 0x3
                assert (oe & out) == 0, f"Open-drain violation! oe={oe:02x} out={out:02x}"
                scl = pin_state(0)
                sda = pin_state(1)
                dut.uio_in.value = (sda << 1) | scl | 0xFC
                if not started and scl == 0:
                    started = True
                    prev_scl = 0
                    run_len = 1
                elif started:
                    if scl == prev_scl:
                        run_len += 1
                    else:
                        if prev_scl == 0:
                            scl_low_lens.append(run_len)
                        else:
                            scl_high_lens.append(run_len)
                        prev_scl = scl
                        run_len = 1
                await ClockCycles(dut.clk, 1)

        async def i2c_target(n_expected: int):
            nonlocal target_drive_sda_low
            while not (pin_state(0) == 1 and pin_state(1) == 0):
                await ClockCycles(dut.clk, 1)
            while pin_state(0) == 1:
                await ClockCycles(dut.clk, 1)
            rx_bytes = []
            for idx in range(n_expected):
                rx = 0
                for _ in range(8):
                    while pin_state(0) == 0:
                        await ClockCycles(dut.clk, 1)
                    rx = (rx << 1) | pin_state(1)
                    while pin_state(0) == 1:
                        await ClockCycles(dut.clk, 1)
                rx_bytes.append(rx)
                do_ack = bool(ack_mask[idx])
                target_drive_sda_low = do_ack
                while pin_state(0) == 0:
                    await ClockCycles(dut.clk, 1)
                while pin_state(0) == 1:
                    await ClockCycles(dut.clk, 1)
                target_drive_sda_low = False
                if not do_ack:
                    break
            return rx_bytes

        mon = cocotb.start_soon(od_and_scl_monitor())
        tgt = cocotb.start_soon(i2c_target(len(expected_bytes)))
        assert (
            await send_cmd(
                dut,
                UART_OP_START,
                req_id=160 + run_param,
                arg0=run_param & 0xFF,
                arg1=(run_param >> 8) & 0xF,
            )
        )[2] == 0
        assert await wait_halted_or_fault(dut, max_cycles=40000) == 2
        done = True
        await mon
        rx = await tgt
        assert rx == list(expected_bytes), f"rx={rx} expected={expected_bytes}"
        # Steady bits 1..7 of byte 0 must have exact period and min t_LOW
        steady_periods = [scl_low_lens[i] + scl_high_lens[i] for i in range(1, 8)]
        assert all(p == expected_steady_period for p in steady_periods), f"periods={steady_periods}"
        return scl_low_lens, scl_high_lens

    # Load 45-word 2-byte I2C program once and test BOTH Fast-mode compliant (384.6 kHz) & 100 kHz (99.6 kHz)
    assert (await load_program(dut, prog2))[2] == 0

    # Fast-mode compliant (run_param=62 -> t_LOW=66 cyc=1320 ns >= 1300 ns, t_HIGH=64 cyc=1280 ns, period=130 cyc -> 384.615 kHz)
    lows, highs = await run_i2c_mb([0xA0, 0x55], [True, True], run_param=62, expected_steady_period=130)
    assert min(lows) >= 65 and min(highs) >= 64
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=170)
    ev1 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=171)
    assert ev0[3] == 2 and ev0[4] == 0xA0 and ev1[3] == 1 and ev1[4] == 0xA1

    # NACK on byte 1 after ACK on byte 0 -> events [0xA0, 0xF1]
    await run_i2c_mb([0xA0, 0x55], [True, False], run_param=62, expected_steady_period=130)
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=172)
    ev1 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=173)
    assert ev0[4] == 0xA0 and ev1[4] == 0xF1

    # Standard-mode 100 kHz using the SAME loaded 45-word program (run_param=248 -> period=502 cyc -> 99.602 kHz)
    lows100, highs100 = await run_i2c_mb([0xA0, 0x55], [True, True], run_param=248, expected_steady_period=502)
    assert min(lows100) >= 252 and min(highs100) >= 250
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=174)
    ev1 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=175)
    assert ev0[4] == 0xA0 and ev1[4] == 0xA1

    # NACK on byte 0 at 100 kHz -> aborts cleanly with STOP and [0xF0]
    await run_i2c_mb([0xA0], [False], run_param=248, expected_steady_period=502)
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=176)
    assert ev0[3] == 1 and ev0[4] == 0xF0

    # OP_PATCH_WORD updates byte 1 in 1 UART command from 0x55 to 0x3C
    patch_od = insn("LD_TX_OD", 0x3C)
    assert (
        await send_cmd(
            dut,
            UART_OP_PATCH_WORD,
            req_id=177,
            arg0=ld_addrs2[1],
            arg1=patch_od & 0xFF,
            arg2=(patch_od >> 8) & 0xFF,
        )
    )[2] == 0
    await run_i2c_mb([0xA0, 0x3C], [True, True], run_param=62, expected_steady_period=130)
    ev0 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=178)
    ev1 = await send_cmd(dut, UART_OP_POP_EVENT, req_id=179)
    assert ev0[4] == 0xA0 and ev1[4] == 0xA1


