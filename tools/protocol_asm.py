# SPDX-License-Identifier: Apache-2.0
"""Assembler and UART loader frame helpers for the v1 programmable protocol engine."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROG_DEPTH = 64

OPCODES: Dict[str, int] = {
    "NOP": 0x0,
    "OUT": 0x1,
    "OE": 0x2,
    "WAIT": 0x3,
    "SAMPLE": 0x4,
    "JZ": 0x5,
    "JMP": 0x6,
    "LOOP": 0x7,
    "SETCOUNT": 0x8,
    "MARK": 0x9,
    "HALT": 0xA,
    "OUT_SAMPLE": 0xB,
    "WAIT_PARAM": 0xC,
    "WAIT_EDGE": 0xD,
    "TX": 0xE,
    "LD_TX": 0xE,
    "LD_TX_OD": 0xE,
    "SHIFT_TX": 0xE,
}

UART_OP_PING_INFO = 0x10
UART_OP_BEGIN_LOAD = 0x11
UART_OP_WRITE_WORD = 0x12
UART_OP_COMMIT_LOAD = 0x13
UART_OP_START = 0x14
UART_OP_HALT = 0x15
UART_OP_READ_STATUS = 0x16
UART_OP_POP_EVENT = 0x17
UART_OP_READ_CYCLE_CNT = 0x18
UART_OP_PATCH_WORD = 0x19
UART_OP_CFG_SCAN = 0x1A
UART_OP_START_SCAN = 0x1B
UART_OP_READ_SCAN_STATUS = 0x1C
UART_OP_READ_SCAN_FAIL = 0x1D


def crc8_07(data: Sequence[int]) -> int:
    """Compute CRC-8 (poly 0x07, init 0x00, non-reflected)."""
    crc = 0
    for byte in data:
        crc ^= byte & 0xFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def program_checksums(words: Sequence[int]) -> Tuple[int, int]:
    """Return (crc8, sum8) over little-endian byte stream of 16-bit words."""
    raw: List[int] = []
    for w in words:
        raw.append(w & 0xFF)
        raw.append((w >> 8) & 0xFF)
    return crc8_07(raw), sum(raw) & 0xFF


def make_uart_request(
    opcode: int,
    req_id: int = 1,
    arg0: int = 0,
    arg1: int = 0,
    arg2: int = 0,
    arg3: int = 0,
    bad_crc: bool = False,
) -> List[int]:
    frame = [
        0xA5,
        opcode & 0xFF,
        req_id & 0xFF,
        arg0 & 0xFF,
        arg1 & 0xFF,
        arg2 & 0xFF,
        arg3 & 0xFF,
    ]
    frame.append(crc8_07(frame) ^ (1 if bad_crc else 0))
    return frame


def insn(op: str, imm: int) -> int:
    name = op.upper()
    code = OPCODES[name]
    if not (0 <= imm <= 0xFFF):
        raise ValueError(f"12-bit immediate out of range for {op}: {imm}")
    if name == "LD_TX_OD" and (imm & 0xC00) == 0:
        imm = imm | (1 << 9)
    return ((code & 0xF) << 12) | (imm & 0xFFF)


def _parse_int(token: str, labels: Dict[str, int]) -> int:
    if token in labels:
        return labels[token]
    return int(token, 0)


def assemble(lines: Iterable[str]) -> List[int]:
    """Assemble mnemonic lines with optional `label:` definitions into 16-bit words."""
    cleaned: List[str] = []
    labels: Dict[str, int] = {}

    for raw_line in lines:
        line = raw_line.split(";", 1)[0].split("#", 1)[0].strip()
        if not line:
            continue
        while ":" in line:
            lbl, rest = line.split(":", 1)
            lbl = lbl.strip()
            if not lbl:
                raise ValueError(f"Empty label in line: {raw_line!r}")
            if lbl in labels:
                raise ValueError(f"Duplicate label {lbl!r}")
            labels[lbl] = len(cleaned)
            line = rest.strip()
            if not line:
                break
        if line:
            cleaned.append(line)

    words: List[int] = []
    for pc, line in enumerate(cleaned):
        parts = [p.strip() for p in line.replace(",", " ").split() if p.strip()]
        mnem = parts[0].upper()
        args = parts[1:]

        if mnem == "NOP":
            words.append(insn("NOP", 0))
        elif mnem in ("OUT", "OE"):
            val = _parse_int(args[0], labels)
            words.append(insn(mnem, val & 0xFF))
        elif mnem == "WAIT":
            cycles_minus_1 = _parse_int(args[0], labels)
            words.append(insn("WAIT", cycles_minus_1))
        elif mnem == "WAIT_PARAM":
            offset = _parse_int(args[0], labels) if args else 0
            words.append(insn("WAIT_PARAM", offset & 0xFF))
        elif mnem == "WAIT_EDGE":
            pol = _parse_int(args[0], labels) & 0x1
            pin = _parse_int(args[1], labels) & 0x7
            max_wait = (_parse_int(args[2], labels) & 0xFF) if len(args) > 2 else 0
            words.append(insn("WAIT_EDGE", (pol << 11) | (pin << 8) | max_wait))
        elif mnem == "SAMPLE":
            pin = _parse_int(args[0], labels) & 0x7
            no_shift = (_parse_int(args[1], labels) & 0x1) if len(args) > 1 else 0
            words.append(insn("SAMPLE", (no_shift << 11) | pin))
        elif mnem == "OUT_SAMPLE":
            val = _parse_int(args[0], labels) & 0xFF
            pin = _parse_int(args[1], labels) & 0x7
            no_shift = (_parse_int(args[2], labels) & 0x1) if len(args) > 2 else 0
            words.append(insn("OUT_SAMPLE", (no_shift << 11) | (pin << 8) | val))
        elif mnem in ("JZ", "JMP"):
            addr = _parse_int(args[0], labels)
            if not (0 <= addr < PROG_DEPTH):
                raise ValueError(f"Jump target out of range at PC {pc}: {addr}")
            words.append(insn(mnem, addr))
        elif mnem == "LOOP":
            ctr = _parse_int(args[0], labels) & 0x1
            addr = _parse_int(args[1], labels)
            if not (0 <= addr < PROG_DEPTH):
                raise ValueError(f"Loop target out of range at PC {pc}: {addr}")
            words.append(insn("LOOP", (ctr << 8) | addr))
        elif mnem == "SETCOUNT":
            ctr = _parse_int(args[0], labels) & 0x1
            cnt = _parse_int(args[1], labels) & 0xFF
            words.append(insn("SETCOUNT", (ctr << 8) | cnt))
        elif mnem == "MARK":
            tag = _parse_int(args[0], labels) & 0xFF
            use_sample = (_parse_int(args[1], labels) & 0x1) if len(args) > 1 else 0
            words.append(insn("MARK", (use_sample << 8) | tag))
        elif mnem == "MARK_SAMPLE":
            words.append(insn("MARK", 0x100))
        elif mnem == "HALT":
            keep_oe = (_parse_int(args[0], labels) & 0x1) if args else 0
            words.append(insn("HALT", keep_oe))
        elif mnem in ("LD_TX", "LD_TX_OD"):
            val = _parse_int(args[0], labels) & 0xFF
            lsb_first = (_parse_int(args[1], labels) & 0x1) if len(args) > 1 else 0
            od_bit = 1 if mnem == "LD_TX_OD" else 0
            words.append(insn("TX", (od_bit << 9) | (lsb_first << 8) | val))
        elif mnem == "SHIFT_TX":
            tx_pin = _parse_int(args[0], labels) & 0x7
            rx_pin = (_parse_int(args[1], labels) & 0x7) if len(args) > 1 else tx_pin
            clk_pin = (_parse_int(args[2], labels) & 0x7) if len(args) > 2 else tx_pin
            clk_val = (_parse_int(args[3], labels) & 0x1) if len(args) > 3 else 0
            do_shift = (_parse_int(args[4], labels) & 0x1) if len(args) > 4 else 1
            imm = (
                (1 << 11)
                | (do_shift << 10)
                | (clk_val << 9)
                | (clk_pin << 6)
                | (rx_pin << 3)
                | tx_pin
            )
            words.append(insn("TX", imm))
        else:
            raise ValueError(f"Unknown mnemonic at PC {pc}: {mnem!r}")

    if len(words) > PROG_DEPTH:
        raise ValueError(f"Program length {len(words)} exceeds {PROG_DEPTH} words")
    return words


def build_uart_tx_program(byte_val: int, bit_cycles: int = 8, pin: int = 0) -> List[int]:
    """Build an 8N1 UART TX program on `uio[pin]`."""
    if bit_cycles < 2:
        raise ValueError("bit_cycles must be >= 2")
    mask = 1 << pin
    wait_n = bit_cycles - 2  # OUT (1) + WAIT (bit_cycles - 2 + 1) = bit_cycles
    lines = [
        f"OUT 0x{mask:02x}",
        f"OE  0x{mask:02x}",
        f"WAIT {wait_n}",
        "OUT 0x00",          # start bit
        f"WAIT {wait_n}",
    ]
    for bit in range(8):
        val = mask if ((byte_val >> bit) & 1) else 0
        lines.append(f"OUT 0x{val:02x}")
        lines.append(f"WAIT {wait_n}")
    lines += [
        f"OUT 0x{mask:02x}",  # stop bit
        f"WAIT {wait_n}",
        "HALT 0",
    ]
    return assemble(lines)


def build_uart_tx_multibyte_program(
    bytes_seq: Sequence[int],
    bit_cycles: int = 8,
    pin: int = 0,
    lsb_first: bool = True,
) -> Tuple[List[int], List[int]]:
    """Build a multi-byte 8N1 UART TX program using `LD_TX` + `SHIFT_TX` loops, returning (words, ld_tx_addrs)."""
    if not bytes_seq:
        raise ValueError("bytes_seq must be non-empty")
    if bit_cycles < 3:
        raise ValueError("bit_cycles must be >= 3 for looped UART TX")
    mask = 1 << pin
    wait_idle = bit_cycles - 2
    wait_loop = bit_cycles - 3  # 1 (OUT/SHIFT_TX) + (bit_cycles - 2) (WAIT) + 1 (SETCOUNT/LOOP) = bit_cycles
    lsb_flag = 1 if lsb_first else 0

    lines: List[str] = [
        f"OUT 0x{mask:02x}",
        f"OE  0x{mask:02x}",
        f"WAIT {wait_idle}",
    ]
    ld_tx_addrs: List[int] = []
    for idx, b in enumerate(bytes_seq):
        ld_tx_addrs.append(3 + idx * 9)
        lines += [
            f"LD_TX 0x{b & 0xFF:02x}, {lsb_flag}",
            "OUT 0x00",
            f"WAIT {wait_loop}",
            "SETCOUNT 0, 8",
            f"uart_mb_loop_{idx}:",
            f"SHIFT_TX {pin}, {pin}, {pin}, 0, 1",
            f"WAIT {wait_loop}",
            f"LOOP 0, uart_mb_loop_{idx}",
            f"OUT 0x{mask:02x}",
            f"WAIT {wait_idle}",
        ]
    lines.append("HALT 0")
    return assemble(lines), ld_tx_addrs


def build_spi_program(
    mosi_byte: int,
    mode: int = 0,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> List[int]:
    """Build an 8-bit SPI mode 0/1/2/3 program parameterized via `WAIT_PARAM` (38 or 39 words)."""
    if mode not in (0, 1, 2, 3):
        raise ValueError(f"SPI mode must be 0..3, got {mode}")
    cpol = (mode >> 1) & 1
    cpha = mode & 1
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask
    sclk_idle = sclk_mask if cpol else 0
    sclk_lead = 0 if cpol else sclk_mask

    lines = [
        f"OUT 0x{(cs_mask | sclk_idle):02x}",
        f"OE  0x{oe_mask:02x}",
    ]
    if cpha == 1:
        # Drop CS_N cleanly before the first leading SCLK shift edge.
        lines.append(f"OUT 0x{sclk_idle:02x}")
    for bit in range(7, -1, -1):
        m = mosi_mask if ((mosi_byte >> bit) & 1) else 0
        if cpha == 0:
            lines.append(f"OUT 0x{(sclk_idle | m):02x}")
            lines.append("WAIT_PARAM 0")
            lines.append(f"OUT_SAMPLE 0x{(sclk_lead | m):02x}, {miso_pin}, 0")
            lines.append("NOP")
        else:
            lines.append(f"OUT 0x{(sclk_lead | m):02x}")
            lines.append("WAIT_PARAM 0")
            lines.append(f"OUT_SAMPLE 0x{(sclk_idle | m):02x}, {miso_pin}, 0")
            lines.append("NOP")
    lines += [
        f"OUT 0x{sclk_idle:02x}",
        f"OUT 0x{(cs_mask | sclk_idle):02x}",
        "MARK_SAMPLE",
        "HALT 0",
    ]
    return assemble(lines)


def build_spi_mode0_program(
    mosi_byte: int,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> List[int]:
    """Build an 8-bit mode-0 SPI program parameterized via `WAIT_PARAM` (`L = param + 2`)."""
    return build_spi_program(
        mosi_byte,
        mode=0,
        cs_pin=cs_pin,
        sclk_pin=sclk_pin,
        mosi_pin=mosi_pin,
        miso_pin=miso_pin,
    )


def build_spi_multibyte_program(
    mosi_bytes: Sequence[int],
    mode: int = 0,
    lsb_first: bool = False,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> Tuple[List[int], List[int]]:
    """Build a multi-byte parameterized SPI Mode 0..3 program using `LD_TX` + `SHIFT_TX`, returning (words, ld_tx_addrs)."""
    if not mosi_bytes:
        raise ValueError("mosi_bytes must be non-empty")
    if mode not in (0, 1, 2, 3):
        raise ValueError(f"SPI mode must be 0..3, got {mode}")
    cpol = (mode >> 1) & 1
    cpha = mode & 1
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask
    sclk_idle_mask = sclk_mask if cpol else 0
    sclk_idle_bit = 1 if cpol else 0
    sclk_lead_bit = 0 if cpol else 1
    clk_setup = sclk_idle_bit if cpha == 0 else sclk_lead_bit
    clk_sample = sclk_lead_bit if cpha == 0 else sclk_idle_bit
    lsb_flag = 1 if lsb_first else 0

    lines: List[str] = [
        f"OUT 0x{(cs_mask | sclk_idle_mask):02x}",
        f"OE  0x{oe_mask:02x}",
        f"OUT 0x{sclk_idle_mask:02x}",
    ]
    ld_tx_addrs: List[int] = []
    for idx, b in enumerate(mosi_bytes):
        ld_tx_addrs.append(3 + idx * 7)
        lines += [
            f"LD_TX 0x{b & 0xFF:02x}, {lsb_flag}",
            "SETCOUNT 0, 8",
            f"spi_mb_loop_{idx}:",
            f"SHIFT_TX {mosi_pin}, {mosi_pin}, {sclk_pin}, {clk_setup}, 0",
            "WAIT_PARAM 0",
            f"SHIFT_TX {mosi_pin}, {miso_pin}, {sclk_pin}, {clk_sample}, 1",
            f"LOOP 0, spi_mb_loop_{idx}",
            "MARK_SAMPLE",
        ]
    lines += [
        f"OUT 0x{sclk_idle_mask:02x}",
        f"OUT 0x{(cs_mask | sclk_idle_mask):02x}",
        "HALT 0",
    ]
    return assemble(lines), ld_tx_addrs


def build_i2c_write_byte_program(
    byte_val: int,
    half_cycles: int = 4,
    scl_pin: int = 0,
    sda_pin: int = 1,
    ack_tag: int = 0xA0,
    nack_tag: int = 0xF0,
) -> List[int]:
    """Build an open-drain I2C START + 8-bit write + ACK/NACK branch + STOP program."""
    if half_cycles < 2:
        raise ValueError("half_cycles must be >= 2")
    scl_oe = 1 << scl_pin
    sda_oe = 1 << sda_pin
    both_oe = scl_oe | sda_oe
    w = half_cycles - 2
    lines = [
        "OUT 0x00",
        "OE  0x00",
        f"WAIT {w}",
        f"OE  0x{sda_oe:02x}",   # START: SDA low while SCL high
        f"WAIT {w}",
        f"OE  0x{both_oe:02x}",  # SCL low
        f"WAIT {w}",
    ]
    for bit in range(7, -1, -1):
        # Open-drain: 1 -> release SDA (OE bit=0), 0 -> drive SDA low (OE bit=1)
        oe_low = scl_oe if ((byte_val >> bit) & 1) else both_oe
        oe_high = 0 if ((byte_val >> bit) & 1) else sda_oe
        lines += [
            f"OE 0x{oe_low:02x}",
            f"WAIT {w}",
            f"OE 0x{oe_high:02x}",
            f"WAIT {w}",
            f"OE 0x{oe_low:02x}",
        ]
    lines += [
        f"OE 0x{scl_oe:02x}",      # 9th clock: release SDA
        f"WAIT {w}",
        "OE 0x00",                 # release SCL high
        f"WAIT {w}",
        f"SAMPLE {sda_pin}, 1",    # sample SDA without shifting sample_reg
        f"OE 0x{both_oe:02x}",     # SCL=0, SDA=0 for STOP prep
        f"WAIT {w}",
        "JZ ack_label",
        f"MARK 0x{nack_tag:02x}, 0",
        "JMP stop_label",
        "ack_label:",
        f"MARK 0x{ack_tag:02x}, 0",
        "stop_label:",
        f"OE 0x{sda_oe:02x}",      # STOP: release SCL high while SDA low
        f"WAIT {w}",
        "OE 0x00",                 # release SDA high
        "HALT 0",
    ]
    return assemble(lines)


def build_i2c_write_multibyte_program(
    bytes_seq: Sequence[int],
    half_cycles: Optional[int] = None,
    scl_pin: int = 0,
    sda_pin: int = 1,
    ack_tag_base: int = 0xA0,
    nack_tag_base: int = 0xF0,
) -> Tuple[List[int], List[int]]:
    """Build an open-drain multi-byte I2C START + N-byte write (with per-byte ACK/NACK branch) + STOP program.

    Uses `LD_TX_OD` + `SHIFT_TX` loops (`30` words for 1 byte, `49` words for 2 bytes vs `62` words for 1 unrolled byte).
    When `half_cycles` is `None`, emits `WAIT_PARAM 0` so `run_param` tunes bit rate at runtime (`62` -> compliant Fast-mode
    `384.615 kHz` @ `50 MHz`, `248` -> Standard-mode `99.602 kHz`); otherwise uses fixed `WAIT (half_cycles - 2)`.
    Returns `(words, ld_tx_addrs)`.
    """
    if not bytes_seq:
        raise ValueError("bytes_seq must be non-empty")
    if half_cycles is not None and half_cycles < 2:
        raise ValueError("half_cycles must be >= 2")
    scl_oe = 1 << scl_pin
    sda_oe = 1 << sda_pin
    both_oe = scl_oe | sda_oe
    wait_insn = "WAIT_PARAM 0" if half_cycles is None else f"WAIT {half_cycles - 2}"

    lines: List[str] = [
        "OUT 0x00",
        "OE  0x00",
        wait_insn,
        f"OE  0x{sda_oe:02x}",   # START: SDA low while SCL high
        wait_insn,
        f"OE  0x{both_oe:02x}",  # SCL low
        wait_insn,
    ]
    ld_tx_addrs: List[int] = []
    for idx, b in enumerate(bytes_seq):
        ld_tx_addrs.append(7 + idx * 19)
        lines += [
            f"LD_TX_OD 0x{b & 0xFF:02x}, 0",
            "SETCOUNT 0, 8",
            f"i2c_mb_loop_{idx}:",
            f"SHIFT_TX {sda_pin}, {sda_pin}, {scl_pin}, 0, 0",
            wait_insn,
            f"SHIFT_TX {sda_pin}, {sda_pin}, {scl_pin}, 1, 0",
            wait_insn,
            f"SHIFT_TX {sda_pin}, {sda_pin}, {scl_pin}, 0, 1",
            f"LOOP 0, i2c_mb_loop_{idx}",
            f"OE 0x{scl_oe:02x}",      # 9th clock: release SDA
            wait_insn,
            "OE 0x00",                 # release SCL high
            wait_insn,
            f"SAMPLE {sda_pin}, 1",    # sample ACK(0)/NACK(1)
            f"OE 0x{both_oe:02x}",     # SCL=0, SDA=0
            wait_insn,
            f"JZ i2c_mb_ack_{idx}",
            f"MARK 0x{(nack_tag_base + idx) & 0xFF:02x}, 0",
            "JMP i2c_mb_stop",
            f"i2c_mb_ack_{idx}:",
            f"MARK 0x{(ack_tag_base + idx) & 0xFF:02x}, 0",
        ]
    lines += [
        "i2c_mb_stop:",
        f"OE 0x{sda_oe:02x}",          # STOP: release SCL high while SDA low
        wait_insn,
        "OE 0x00",                     # release SDA high
        "HALT 0",
    ]
    return assemble(lines), ld_tx_addrs


def build_i2c_stretch_probe_program(
    scl_pin: int = 0,
    sda_pin: int = 1,
    half_cycles: int = 4,
    released_tag: int = 0x51,
    timeout_tag: int = 0xEE,
) -> List[int]:
    """Drive one open-drain SCL low pulse, release SCL, and timestamp slave clock-stretch release."""
    scl_oe = 1 << scl_pin
    w = max(0, half_cycles - 2)
    lines = [
        "OUT 0x00",
        f"OE 0x{scl_oe:02x}",       # pull SCL low
        f"WAIT {w}",
        "MARK 0x50, 0",             # release marker before freeing SCL
        "OE 0x00",                  # release SCL; slave may hold SCL low (stretch)
        f"WAIT_EDGE 1, {scl_pin}, 0",  # wait up to run_param cycles for SCL==1
        "JZ timeout_lbl",
        f"MARK 0x{released_tag:02x}, 0",
        "JMP done_lbl",
        "timeout_lbl:",
        f"MARK 0x{timeout_tag:02x}, 0",
        "done_lbl:",
        "HALT 0",
    ]
    return assemble(lines)


def build_spi_target_responder_program(
    miso_byte: int,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
    max_wait: int = 0,
) -> List[int]:
    """SPI mode-0 responder (target) polling CS_N and SCLK via WAIT_EDGE."""
    miso_oe = 1 << miso_pin
    lines = [
        "OUT 0x00",
        f"OE 0x{miso_oe:02x}",
        f"WAIT_EDGE 0, {cs_pin}, {max_wait}",
        "JZ cs_timeout",
    ]
    for bit in range(7, -1, -1):
        val = miso_oe if ((miso_byte >> bit) & 1) else 0
        lines += [
            f"OUT 0x{val:02x}",
            f"WAIT_EDGE 1, {sclk_pin}, {max_wait}",
            f"SAMPLE {mosi_pin}, 0",
            f"WAIT_EDGE 0, {sclk_pin}, {max_wait}",
        ]
    lines += [
        "MARK_SAMPLE",
        "JMP end_lbl",
        "cs_timeout:",
        "MARK 0xEE, 0",
        "end_lbl:",
        "HALT 0",
    ]
    return assemble(lines)


def bitrev8(val: int) -> int:
    """Reverse bits of an 8-bit integer (MSB-first <-> LSB-first conversion)."""
    return int(f"{val & 0xFF:08b}"[::-1], 2)


def build_jtag_idcode_program(
    ir_opcode: int = 0x1,
    dr_bytes: int = 4,
    tck_pin: int = 0,
    tms_pin: int = 1,
    tdi_pin: int = 2,
    tdo_pin: int = 3,
) -> List[int]:
    """Assemble an IEEE 1149.1 TAP reset + Shift-IR + parameterized Shift-DR IDCODE reader (<=64 words)."""
    if not (1 <= dr_bytes <= 8):
        raise ValueError(f"dr_bytes must be in 1..8 to fit MARK FIFO, got {dr_bytes}")
    tck = 1 << tck_pin
    tms = 1 << tms_pin
    tdi = 1 << tdi_pin
    oe_mask = tck | tms | tdi

    lines = [
        f"OUT 0x{tms:02x}",       # hold TMS=1, TCK=0
        f"OE  0x{oe_mask:02x}",
        "SETCOUNT 0, 5",          # 5 TCK edges with TMS=1 -> Test-Logic-Reset from any state
        "jtag_rst_loop:",
        f"OUT 0x{(tms | tck):02x}",
        f"OUT 0x{tms:02x}",
        "LOOP 0, jtag_rst_loop",
    ]

    def tick(tms_bit: int, tdi_bit: int = 0) -> None:
        base = (tms if tms_bit else 0) | (tdi if tdi_bit else 0)
        lines.append(f"OUT 0x{base:02x}")
        lines.append(f"OUT 0x{(base | tck):02x}")

    # TLR -> RTI (0) -> Sel-DR (1) -> Sel-IR (1) -> Cap-IR (0) -> Shift-IR (0)
    for t in (0, 1, 1, 0, 0):
        tick(t, 0)

    # Shift 4-bit IR instruction LSB-first; assert TMS=1 on MSB to enter Exit1-IR
    for bit in range(4):
        tick(1 if bit == 3 else 0, (ir_opcode >> bit) & 1)

    # Exit1-IR -> Update-IR (1) -> Sel-DR (1) -> Cap-DR (0) -> Shift-DR (0)
    for t in (1, 1, 0, 0):
        tick(t, 0)

    # Shift dr_bytes * 8 bits from DR (LSB-first on TDO), parameterizing low-half TCK via WAIT_PARAM
    lines += [
        f"SETCOUNT 1, {dr_bytes}",
        "jtag_byte_loop:",
        "SETCOUNT 0, 8",
        "jtag_bit_loop:",
        "OUT 0x00",               # TCK falling edge shifts next bit onto TDO
        "WAIT_PARAM 0",           # wait run_param + 1 extra cycles before sampling TDO
        f"SAMPLE {tdo_pin}, 0",   # shifts sampled bit into LSB of sample_reg
        f"OUT 0x{tck:02x}",       # TCK rising edge shifts TAP DR
        "WAIT 1",
        "LOOP 0, jtag_bit_loop",
        "MARK_SAMPLE",            # log bit-reversed captured byte & cycle_cnt
        "LOOP 1, jtag_byte_loop",
    ]

    # Shift-DR -> Exit1-DR (1) -> Update-DR (1) -> RTI (0), park TCK=0 and halt
    for t in (1, 1, 0):
        tick(t, 0)
    lines += [
        "OUT 0x00",
        "HALT 0",
    ]
    return assemble(lines)


def decode_jtag_idcode_events(events: Sequence[Dict[str, Any]]) -> int:
    """Reconstruct a little-endian integer (e.g. 32-bit IDCODE) from 8-bit MARK_SAMPLE events."""
    val = 0
    for idx, ev in enumerate(events):
        val |= bitrev8(int(ev["tag"])) << (8 * idx)
    return val


def build_spi_two_txn_sequence(
    cmd0: int,
    cmd1: int,
    bit_low_wait: int = 4,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> Tuple[List[int], Dict[str, int]]:
    """Build a parameterized 2-transaction SPI sequence returning word indices for patching."""
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask
    lines = [
        f"OUT 0x{cs_mask:02x}",
        f"OE 0x{oe_mask:02x}",
    ]
    # Note: keep compact loop over bits using explicit unrolled or loop pattern?
    # Loop fits well inside 64 words!
    # Put cmd0 bytes as unrolled fast path: 2 transactions in <= 52 words
    #Wait: 2 * 8 bits = 16 bits * 3 insns = 48 words if unrolled! Or use WAIT fixed and WAIT_PARAM only on inter-CS gap!
    # 16 bits * 3 words + overhead = 56 words <= 64, AND byte0/byte1 offsets are patchable!
    meta: Dict[str, int] = {"cmd0_word_start": 2, "cmd1_word_start": 0, "gap_word": 0}
    w = max(0, bit_low_wait)

    def emit_byte(byte_val: int) -> None:
        lines.append("OUT 0x00")
        lines.append("WAIT 0")
        for bit in range(7, -1, -1):
            m = mosi_mask if ((byte_val >> bit) & 1) else 0
            lines.append(f"OUT 0x{m:02x}")
            if w > 0:
                lines.append(f"WAIT {w - 1}")
            lines.append(f"OUT_SAMPLE 0x{(sclk_mask | m):02x}, {miso_pin}, 0")

    emit_byte(cmd0)
    lines.append("OUT 0x00")
    lines.append("MARK_SAMPLE")
    lines.append(f"OUT 0x{cs_mask:02x}")  # deassert CS_N
    meta["gap_word"] = len(lines)
    lines.append("WAIT_PARAM 0")           # swept inter-transaction CS_N high gap
    meta["cmd1_word_start"] = len(lines) + 2
    emit_byte(cmd1)
    lines.append("OUT 0x00")
    lines.append(f"OUT 0x{cs_mask:02x}")
    lines.append("MARK_SAMPLE")
    lines.append("HALT 0")
    words = assemble(lines)
    return words, meta


def build_spi_2d_program(
    cmd0: int,
    cmd1: int,
    sclk_low_cycles: int = 4,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> List[int]:
    """Assemble a 2-txn SPI program (59 words <= 64) with X=`sclk_low_cycles` (>=2) and Y=`WAIT_PARAM`."""
    if sclk_low_cycles < 2:
        raise ValueError("sclk_low_cycles must be >= 2")
    w = sclk_low_cycles - 2
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask

    lines = [
        f"OUT 0x{cs_mask:02x}",
        f"OE  0x{oe_mask:02x}",
    ]
    for idx, cmd in enumerate((cmd0, cmd1)):
        if idx > 0:
            lines.append("WAIT_PARAM 0")
        lines.append("OUT 0x00")
        for bit in range(7, -1, -1):
            m = mosi_mask if ((cmd >> bit) & 1) else 0
            lines.append(f"OUT 0x{m:02x}")
            lines.append(f"WAIT {w}")
            lines.append(f"OUT_SAMPLE 0x{(sclk_mask | m):02x}, {miso_pin}, 0")
        lines.append("OUT 0x00")
        lines.append(f"OUT 0x{cs_mask:02x}")
        if idx == 0:
            lines.append("MARK_SAMPLE")
    lines.append("HALT 0")
    return assemble(lines)


def predict_straight_line_cycles(words: Sequence[int], run_param: int) -> Dict[str, Any]:
    """Statically compute exact cycle_cnt and MARK offsets for branch/loop-free programs."""
    cycles = 0
    marks: List[int] = []
    for pc, w in enumerate(words):
        op = (w >> 12) & 0xF
        imm = w & 0xFFF
        if op in (0x5, 0x6, 0x7, 0xD):
            raise ValueError(f"Dynamic branch/edge op 0x{op:x} at PC {pc} requires dynamic trace")
        if op == 0xF or (op == 0xE and ((imm >> 11) & 1) == 0 and ((imm >> 10) & 1) == 1):
            raise ValueError(f"Illegal opcode 0x{w:04x} at PC {pc}")
        if op == 0x9:  # MARK records pre-increment cycle_cnt_o
            marks.append(cycles)
        cycles += 1
        if op == 0x3:  # WAIT adds imm extra cycles
            cycles += imm
        elif op == 0xC:  # WAIT_PARAM adds min(0xFFF, run_param + imm[7:0])
            cycles += min(0xFFF, run_param + (imm & 0xFF))
        elif op == 0xA:  # HALT stops incrementing after its decode cycle
            break
    return {"total_cycles": cycles, "mark_cycles": marks}


def _pad_program(words: Sequence[int]) -> List[int]:
    if len(words) > PROG_DEPTH:
        raise ValueError(f"Program length {len(words)} exceeds PROG_DEPTH={PROG_DEPTH}")
    return list(words) + [0] * (PROG_DEPTH - len(words))


def analyze_timing_bounds(
    words: Sequence[int],
    run_param: int,
    *,
    max_states: int = 20000,
) -> Dict[str, Any]:
    """Compute exact reachable cycle bounds across dynamic paths (JZ, JMP, LOOP, WAIT_EDGE)."""
    mem = _pad_program(words)
    start_state = (0, 0, 0, 0)  # (pc, loop_cnt0, loop_cnt1, last_sample)

    def _transitions(state: Tuple[int, int, int, int]) -> Tuple[str, List[Tuple[Optional[Tuple[int, int, int, int]], int, int]]]:
        pc, lc0, lc1, last_s = state
        w = mem[pc]
        op = (w >> 12) & 0xF
        imm = w & 0xFFF
        next_pc = (pc + 1) & (PROG_DEPTH - 1)

        if op in (0x0, 0x1, 0x2, 0x9):  # NOP, OUT, OE, MARK
            return ("step", [((next_pc, lc0, lc1, last_s), 1, 1)])
        if op == 0x3:  # WAIT
            c = 1 + imm
            return ("step", [((next_pc, lc0, lc1, last_s), c, c)])
        if op in (0x4, 0xB):  # SAMPLE, OUT_SAMPLE -> pin can read 0 or 1
            return (
                "step",
                [
                    ((next_pc, lc0, lc1, 0), 1, 1),
                    ((next_pc, lc0, lc1, 1), 1, 1),
                ],
            )
        if op == 0x5:  # JZ
            tgt = imm & (PROG_DEPTH - 1)
            dst = tgt if last_s == 0 else next_pc
            return ("step", [((dst, lc0, lc1, last_s), 1, 1)])
        if op == 0x6:  # JMP
            tgt = imm & (PROG_DEPTH - 1)
            return ("step", [((tgt, lc0, lc1, last_s), 1, 1)])
        if op == 0x7:  # LOOP
            ctr_sel = (imm >> 8) & 0x1
            tgt = imm & (PROG_DEPTH - 1)
            cur = lc1 if ctr_sel else lc0
            if cur > 1:
                nc = cur - 1
                nl0, nl1 = (lc0, nc) if ctr_sel else (nc, lc1)
                return ("step", [((tgt, nl0, nl1, last_s), 1, 1)])
            nl0, nl1 = (lc0, 0) if ctr_sel else (0, lc1)
            return ("step", [((next_pc, nl0, nl1, last_s), 1, 1)])
        if op == 0x8:  # SETCOUNT
            ctr_sel = (imm >> 8) & 0x1
            val = imm & 0xFF
            nl0, nl1 = (lc0, val) if ctr_sel else (val, lc1)
            return ("step", [((next_pc, nl0, nl1, last_s), 1, 1)])
        if op == 0xA:  # HALT
            return ("halt", [(None, 1, 1)])
        if op == 0xC:  # WAIT_PARAM
            extra = min(0xFFF, run_param + (imm & 0xFF))
            c = 1 + extra
            return ("step", [((next_pc, lc0, lc1, last_s), c, c)])
        if op == 0xD:  # WAIT_EDGE: hit in [0..lim] waits (1..1+lim, ls=1) or timeout (1+lim, ls=0)
            lim = run_param if (imm & 0xFF) == 0 else (imm & 0xFF)
            lim = min(0xFFF, max(0, lim))
            if lim == 0:
                return (
                    "step",
                    [
                        ((next_pc, lc0, lc1, 1), 1, 1),
                        ((next_pc, lc0, lc1, 0), 1, 1),
                    ],
                )
            return (
                "step",
                [
                    ((next_pc, lc0, lc1, 1), 1, 1 + lim),
                    ((next_pc, lc0, lc1, 0), 1 + lim, 1 + lim),
                ],
            )
        if op == 0xE:  # OP_TX: LD_TX / LD_TX_OD (imm[11]==0, imm[10]==0) or SHIFT_TX (imm[11]==1)
            if ((imm >> 11) & 1) == 0:
                if ((imm >> 10) & 1) == 0:
                    return ("step", [((next_pc, lc0, lc1, last_s), 1, 1)])
                return ("fault", [(None, 1, 1)])
            do_shift = (imm >> 10) & 1
            rx_pin = (imm >> 3) & 7
            tx_pin = imm & 7
            if do_shift and (rx_pin != tx_pin):
                return (
                    "step",
                    [
                        ((next_pc, lc0, lc1, 0), 1, 1),
                        ((next_pc, lc0, lc1, 1), 1, 1),
                    ],
                )
            return ("step", [((next_pc, lc0, lc1, last_s), 1, 1)])
        return ("fault", [(None, 1, 1)])

    # Iterative DFS topological sort + cycle check
    color: Dict[Tuple[int, int, int, int], int] = {}  # 1=gray, 2=black
    topo_rev: List[Tuple[int, int, int, int]] = []
    unbounded_pcs: List[int] = []
    truncated = False

    stack: List[Tuple[Tuple[int, int, int, int], int]] = [(start_state, 0)]
    edges_cache: Dict[
        Tuple[int, int, int, int],
        Tuple[str, List[Tuple[Optional[Tuple[int, int, int, int]], int, int]]],
    ] = {}
    color[start_state] = 1

    while stack:
        st, idx = stack[-1]
        if st not in edges_cache:
            if len(edges_cache) >= max_states:
                truncated = True
                stack.pop()
                color[st] = 2
                continue
            edges_cache[st] = _transitions(st)
        kind, outs = edges_cache[st]
        if idx < len(outs):
            stack[-1] = (st, idx + 1)
            nxt = outs[idx][0]
            if nxt is not None:
                c = color.get(nxt, 0)
                if c == 1:
                    unbounded_pcs.append(nxt[0])
                elif c == 0:
                    color[nxt] = 1
                    stack.append((nxt, 0))
        else:
            stack.pop()
            color[st] = 2
            topo_rev.append(st)

    if unbounded_pcs or truncated:
        return {
            "bounded": False,
            "truncated": truncated,
            "unbounded_pcs": sorted(set(unbounded_pcs)),
            "states_explored": len(edges_cache),
            "min_cycles": None,
            "max_cycles": None,
            "fault_reachable": False,
            "mark_bounds": [],
        }

    topo = list(reversed(topo_rev))
    state_bounds: Dict[Tuple[int, int, int, int], List[int]] = {start_state: [0, 0]}
    halt_min: Optional[int] = None
    halt_max: Optional[int] = None
    fault_min: Optional[int] = None
    fault_max: Optional[int] = None
    mark_by_pc: Dict[int, Dict[str, Any]] = {}

    for st in topo:
        if st not in state_bounds:
            continue
        cur_min, cur_max = state_bounds[st]
        pc = st[0]
        w = mem[pc]
        op = (w >> 12) & 0xF
        imm = w & 0xFFF
        if op == 0x9:
            use_sample = bool((imm >> 8) & 0x1)
            tag = None if use_sample else (imm & 0xFF)
            entry = mark_by_pc.get(pc)
            if entry is None:
                mark_by_pc[pc] = {
                    "pc": pc,
                    "use_sample": use_sample,
                    "tag": tag,
                    "min_ts": cur_min,
                    "max_ts": cur_max,
                }
            else:
                entry["min_ts"] = min(entry["min_ts"], cur_min)
                entry["max_ts"] = max(entry["max_ts"], cur_max)

        kind, outs = edges_cache[st]
        for nxt, dmin, dmax in outs:
            nmin = cur_min + dmin
            nmax = cur_max + dmax
            if kind == "halt":
                halt_min = nmin if halt_min is None else min(halt_min, nmin)
                halt_max = nmax if halt_max is None else max(halt_max, nmax)
            elif kind == "fault":
                fault_min = nmin if fault_min is None else min(fault_min, nmin)
                fault_max = nmax if fault_max is None else max(fault_max, nmax)
            elif nxt is not None:
                prev = state_bounds.get(nxt)
                if prev is None:
                    state_bounds[nxt] = [nmin, nmax]
                else:
                    prev[0] = min(prev[0], nmin)
                    prev[1] = max(prev[1], nmax)

    return {
        "bounded": True,
        "truncated": False,
        "unbounded_pcs": [],
        "states_explored": len(edges_cache),
        "min_cycles": halt_min,
        "max_cycles": halt_max,
        "halt_reachable": halt_min is not None,
        "fault_reachable": fault_min is not None,
        "fault_min_cycles": fault_min,
        "fault_max_cycles": fault_max,
        "mark_bounds": [mark_by_pc[k] for k in sorted(mark_by_pc)],
    }


def predict_dynamic_contract(
    words: Sequence[int],
    run_param: int,
    *,
    sample_bits: Sequence[int] = (),
    edge_waits: Sequence[Optional[int]] = (),
    max_cycles: int = 100000,
) -> Dict[str, Any]:
    """Cycle-accurate concrete path predictor supporting JZ, JMP, LOOP, SETCOUNT, and WAIT_EDGE."""
    mem = _pad_program(words)
    pc = 0
    lc0 = 0
    lc1 = 0
    last_sample = 0
    sample_reg = 0
    cycles = 0
    s_idx = 0
    e_idx = 0
    marks: List[Dict[str, Any]] = []
    final_state = "ENG_HALTED"

    while True:
        if cycles > max_cycles:
            raise ValueError(f"Dynamic contract exceeded max_cycles={max_cycles}")
        w = mem[pc]
        op = (w >> 12) & 0xF
        imm = w & 0xFFF
        next_pc = (pc + 1) & (PROG_DEPTH - 1)

        if op == 0x9:  # OP_MARK records pre-increment cycle_cnt_o
            tag = sample_reg if ((imm >> 8) & 0x1) else (imm & 0xFF)
            marks.append({"pc": pc, "tag": tag, "ts_cycles": cycles})
            cycles += 1
            pc = next_pc
        elif op in (0x0, 0x1, 0x2):  # NOP, OUT, OE
            cycles += 1
            pc = next_pc
        elif op == 0x3:  # OP_WAIT
            cycles += 1 + imm
            pc = next_pc
        elif op in (0x4, 0xB):  # OP_SAMPLE, OP_OUT_SAMPLE
            if s_idx >= len(sample_bits):
                raise ValueError(f"Missing sample_bits entry #{s_idx} at PC {pc}")
            bit = int(sample_bits[s_idx]) & 0x1
            s_idx += 1
            last_sample = bit
            if not ((imm >> 11) & 0x1):
                sample_reg = ((sample_reg << 1) | bit) & 0xFF
            cycles += 1
            pc = next_pc
        elif op == 0x5:  # OP_JZ
            cycles += 1
            pc = (imm & (PROG_DEPTH - 1)) if last_sample == 0 else next_pc
        elif op == 0x6:  # OP_JMP
            cycles += 1
            pc = imm & (PROG_DEPTH - 1)
        elif op == 0x7:  # OP_LOOP
            ctr_sel = (imm >> 8) & 0x1
            cur = lc1 if ctr_sel else lc0
            if cur > 1:
                if ctr_sel:
                    lc1 -= 1
                else:
                    lc0 -= 1
                pc = imm & (PROG_DEPTH - 1)
            else:
                if ctr_sel:
                    lc1 = 0
                else:
                    lc0 = 0
                pc = next_pc
            cycles += 1
        elif op == 0x8:  # OP_SETCOUNT
            ctr_sel = (imm >> 8) & 0x1
            if ctr_sel:
                lc1 = imm & 0xFF
            else:
                lc0 = imm & 0xFF
            cycles += 1
            pc = next_pc
        elif op == 0xA:  # OP_HALT
            cycles += 1
            final_state = "ENG_HALTED"
            break
        elif op == 0xC:  # OP_WAIT_PARAM
            cycles += 1 + min(0xFFF, run_param + (imm & 0xFF))
            pc = next_pc
        elif op == 0xD:  # OP_WAIT_EDGE
            if e_idx >= len(edge_waits):
                raise ValueError(f"Missing edge_waits entry #{e_idx} at PC {pc}")
            wait_spec = edge_waits[e_idx]
            e_idx += 1
            lim = run_param if (imm & 0xFF) == 0 else (imm & 0xFF)
            lim = min(0xFFF, max(0, lim))
            if wait_spec is None or wait_spec > lim:
                cycles += 1 + lim
                last_sample = 0
            else:
                if wait_spec < 0:
                    raise ValueError(f"Negative WAIT_EDGE delay at PC {pc}: {wait_spec}")
                cycles += 1 + int(wait_spec)
                last_sample = 1
            pc = next_pc
        elif op == 0xE:  # OP_TX (LD_TX / LD_TX_OD / SHIFT_TX)
            if ((imm >> 11) & 1) == 0:
                if ((imm >> 10) & 1) != 0:
                    cycles += 1
                    final_state = "ENG_FAULT"
                    break
                cycles += 1
                pc = next_pc
            else:
                do_shift = (imm >> 10) & 1
                rx_pin = (imm >> 3) & 7
                tx_pin = imm & 7
                if do_shift and (rx_pin != tx_pin):
                    if s_idx >= len(sample_bits):
                        raise ValueError(f"Missing sample_bits entry #{s_idx} at PC {pc}")
                    bit = int(sample_bits[s_idx]) & 0x1
                    s_idx += 1
                    last_sample = bit
                    sample_reg = ((sample_reg << 1) | bit) & 0xFF
                cycles += 1
                pc = next_pc
        else:
            cycles += 1
            final_state = "ENG_FAULT"
            break

    return {
        "total_cycles": cycles,
        "mark_cycles": [m["ts_cycles"] for m in marks],
        "mark_events": marks,
        "final_state": final_state,
        "final_pc": pc,
        "sample_reg": sample_reg,
        "last_sample": last_sample,
        "samples_consumed": s_idx,
        "edge_waits_consumed": e_idx,
    }


def check_timing_contract(
    words: Sequence[int],
    run_param: int,
    *,
    observed_cycles: int,
    observed_events: Sequence[Dict[str, Any]],
    eng_state: str = "ENG_HALTED",
    sample_bits: Optional[Sequence[int]] = None,
    edge_waits: Optional[Sequence[Optional[int]]] = None,
) -> Dict[str, Any]:
    """Verify RTL-observed cycle counts and MARK timestamps against static bounds and exact trace."""
    bounds = analyze_timing_bounds(words, run_param)
    errors: List[str] = []

    if not bounds["bounded"]:
        errors.append(f"Program path timing is unbounded (cycles at PCs {bounds['unbounded_pcs']})")
    else:
        if eng_state == "ENG_HALTED":
            if not bounds["halt_reachable"]:
                errors.append("Observed ENG_HALTED but HALT is statically unreachable")
            elif not (bounds["min_cycles"] <= observed_cycles <= bounds["max_cycles"]):
                errors.append(
                    f"Observed cycle_cnt={observed_cycles} outside static HALT bounds "
                    f"[{bounds['min_cycles']}, {bounds['max_cycles']}]"
                )
        elif eng_state == "ENG_FAULT":
            if not bounds["fault_reachable"]:
                errors.append("Observed ENG_FAULT but fault is statically unreachable")
            elif not (bounds["fault_min_cycles"] <= observed_cycles <= bounds["fault_max_cycles"]):
                errors.append(
                    f"Observed fault cycle_cnt={observed_cycles} outside bounds "
                    f"[{bounds['fault_min_cycles']}, {bounds['fault_max_cycles']}]"
                )
        else:
            errors.append(f"Non-terminal eng_state {eng_state!r}")

        mark_bounds = bounds["mark_bounds"]
        for ev in observed_events:
            tag = int(ev["tag"])
            ts = int(ev["ts_cycles"])
            matching = [
                mb
                for mb in mark_bounds
                if (mb["use_sample"] or mb["tag"] == tag) and (mb["min_ts"] <= ts <= mb["max_ts"])
            ]
            if not matching:
                errors.append(f"Observed MARK (tag=0x{tag:02x}, ts={ts}) outside static MARK bounds")

    exact_trace: Optional[Dict[str, Any]] = None
    if sample_bits is not None or edge_waits is not None:
        exact_trace = predict_dynamic_contract(
            words,
            run_param,
            sample_bits=sample_bits or (),
            edge_waits=edge_waits or (),
        )
        if observed_cycles != exact_trace["total_cycles"]:
            errors.append(
                f"Observed cycle_cnt={observed_cycles} != predicted={exact_trace['total_cycles']}"
            )
        if eng_state != exact_trace["final_state"]:
            errors.append(
                f"Observed eng_state={eng_state} != predicted={exact_trace['final_state']}"
            )
        obs_pairs = [(int(e["tag"]), int(e["ts_cycles"])) for e in observed_events]
        exp_pairs = [(int(m["tag"]), int(m["ts_cycles"])) for m in exact_trace["mark_events"]]
        if sample_bits is None:
            # MARK_SAMPLE value depends on pin data if sample_bits wasn't supplied; still enforce exact timestamps + fixed tags.
            if len(obs_pairs) != len(exp_pairs):
                errors.append(
                    f"Observed event count {len(obs_pairs)} != predicted {len(exp_pairs)}"
                )
            else:
                for idx, ((ot, ots), (et, ets)) in enumerate(zip(obs_pairs, exp_pairs)):
                    if ots != ets:
                        errors.append(f"Event {idx} ts_cycles={ots} != predicted={ets}")
        elif obs_pairs != exp_pairs:
            errors.append(f"Observed MARK events {obs_pairs} != predicted {exp_pairs}")

    return {
        "contract_ok": len(errors) == 0,
        "errors": errors,
        "bounds": bounds,
        "exact_trace": exact_trace,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v1 Protocol Engine assembler helper")
    parser.add_argument(
        "--example",
        choices=[
            "uart",
            "uart_multibyte",
            "spi",
            "spi_multibyte",
            "spi_mode1",
            "spi_mode2",
            "spi_mode3",
            "i2c",
            "i2c_multibyte",
            "i2c_100k",
            "i2c_400k_nominal",
            "i2c_fast_compliant",
            "i2c_stretch",
            "spi_target",
            "jtag_idcode",
        ],
        required=True,
    )
    args = parser.parse_args()
    if args.example == "uart":
        prog = build_uart_tx_program(0xA3, bit_cycles=8)
    elif args.example == "uart_multibyte":
        prog, _ = build_uart_tx_multibyte_program([0xA3, 0x5C], bit_cycles=8)
    elif args.example == "spi":
        prog = build_spi_mode0_program(0x55)
    elif args.example == "spi_multibyte":
        prog, _ = build_spi_multibyte_program([0xA5, 0x3C], lsb_first=False)
    elif args.example == "spi_mode1":
        prog = build_spi_program(0x55, mode=1)
    elif args.example == "spi_mode2":
        prog = build_spi_program(0x55, mode=2)
    elif args.example == "spi_mode3":
        prog = build_spi_program(0x55, mode=3)
    elif args.example == "i2c":
        prog = build_i2c_write_byte_program(0xA0)
    elif args.example == "i2c_multibyte":
        prog, _ = build_i2c_write_multibyte_program([0xA0, 0x55], half_cycles=None)
    elif args.example == "i2c_100k":
        prog = build_i2c_write_byte_program(0xA0, half_cycles=250)
    elif args.example == "i2c_400k_nominal":
        prog = build_i2c_write_byte_program(0xA0, half_cycles=62)
    elif args.example == "i2c_fast_compliant":
        prog = build_i2c_write_byte_program(0xA0, half_cycles=64)
    elif args.example == "i2c_stretch":
        prog = build_i2c_stretch_probe_program()
    elif args.example == "spi_target":
        prog = build_spi_target_responder_program(0x5A)
    else:
        prog = build_jtag_idcode_program()
    crc, s8 = program_checksums(prog)
    print(json.dumps({"example": args.example, "length": len(prog), "crc8": crc, "sum8": s8, "words": [f"0x{w:04x}" for w in prog]}, indent=2))
