# SPDX-License-Identifier: Apache-2.0
"""Assembler and UART loader frame helpers for the v1 programmable protocol engine."""

from __future__ import annotations

import argparse
import json
from typing import Dict, Iterable, List, Sequence, Tuple

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
    code = OPCODES[op.upper()]
    if not (0 <= imm <= 0xFFF):
        raise ValueError(f"12-bit immediate out of range for {op}: {imm}")
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


def build_spi_mode0_program(
    mosi_byte: int,
    cs_pin: int = 0,
    sclk_pin: int = 1,
    mosi_pin: int = 2,
    miso_pin: int = 3,
) -> List[int]:
    """Build an 8-bit mode-0 SPI program parameterized via `WAIT_PARAM` (`L = param + 2`)."""
    cs_mask = 1 << cs_pin
    sclk_mask = 1 << sclk_pin
    mosi_mask = 1 << mosi_pin
    oe_mask = cs_mask | sclk_mask | mosi_mask
    lines = [
        f"OUT 0x{cs_mask:02x}",
        f"OE  0x{oe_mask:02x}",
    ]
    for bit in range(7, -1, -1):
        m = mosi_mask if ((mosi_byte >> bit) & 1) else 0
        lines.append(f"OUT 0x{m:02x}")
        lines.append("WAIT_PARAM 0")
        lines.append(f"OUT_SAMPLE 0x{(sclk_mask | m):02x}, {miso_pin}, 0")
        lines.append("NOP")
    lines += [
        "OUT 0x00",
        f"OUT 0x{cs_mask:02x}",
        "MARK_SAMPLE",
        "HALT 0",
    ]
    return assemble(lines)


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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="v1 Protocol Engine assembler helper")
    parser.add_argument("--example", choices=["uart", "spi", "i2c"], required=True)
    args = parser.parse_args()
    if args.example == "uart":
        prog = build_uart_tx_program(0xA3, bit_cycles=8)
    elif args.example == "spi":
        prog = build_spi_mode0_program(0x55)
    else:
        prog = build_i2c_write_byte_program(0xA0)
    crc, s8 = program_checksums(prog)
    print(json.dumps({"example": args.example, "length": len(prog), "crc8": crc, "sum8": s8, "words": [f"0x{w:04x}" for w in prog]}, indent=2))
