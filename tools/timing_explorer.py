# SPDX-License-Identifier: Apache-2.0
"""Host driver and Timing Boundary Explorer CLI for the v1 ASIC protocol engine."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from experiments.spi_timing_model import Target  # noqa: E402
from tools.protocol_asm import (  # noqa: E402
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
    build_i2c_stretch_probe_program,
    build_i2c_write_byte_program,
    build_i2c_write_multibyte_program,
    build_jtag_idcode_program,
    build_spi_mode0_program,
    build_spi_multibyte_program,
    build_spi_program,
    build_spi_target_responder_program,
    build_uart_tx_multibyte_program,
    build_uart_tx_program,
    check_timing_contract,
    crc8_07,
    decode_jtag_idcode_events,
    make_uart_request,
    predict_straight_line_cycles,
    program_checksums,
)

STATUS_NAMES = {
    0x00: "ST_OK",
    0x01: "ST_BAD_CRC",
    0x02: "ST_BAD_OP",
    0x03: "ST_BAD_ARG",
    0x04: "ST_BAD_STATE",
    0x05: "ST_CHECKSUM",
}

ENG_STATE_NAMES = {
    0: "ENG_IDLE",
    1: "ENG_RUN",
    2: "ENG_HALTED",
    3: "ENG_FAULT",
}


class ProtocolError(RuntimeError):
    """Raised when UART framing, CRC, or command status fails."""


@dataclass(frozen=True)
class SimObservations:
    """Target-side observations captured only inside the RTL simulation bridge."""

    uo_out: int
    spi_mosi_rx: int
    uart_mon_byte: int
    i2c_rx_byte: int
    i2c_od_fault: bool
    i2c_seen: bool
    uart_mon_seen: bool


class ProtocolTransport:
    """Abstract 8-byte request/response transport to `tt_um_example`."""

    evidence_class: str = "unknown"

    def exchange(self, frame: Sequence[int]) -> List[int]:
        raise NotImplementedError

    def reset(self, ena: int = 1) -> None:
        raise NotImplementedError

    def configure_target(
        self, mode: int, delay_ns: int = 23, spi_resp: int = 0xAA, i2c_ack: int = 1
    ) -> None:
        pass

    def wait_execution(self, max_cycles: int = 4000) -> Optional[SimObservations]:
        return None

    def wait_scan_completion(self, max_cycles: int = 200000) -> Optional[Tuple[int, int]]:
        return None

    def close(self) -> None:
        pass

    def __enter__(self) -> "ProtocolTransport":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class SerialTransport(ProtocolTransport):
    """Hardware UART transport using pyserial for an FPGA/board or ASIC carrier."""

    evidence_class = "hardware_uart"

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0) -> None:
        try:
            import serial  # type: ignore
        except ImportError as exc:
            raise ProtocolError(
                "pyserial is required for --port hardware UART transport (`pip install pyserial`)."
            ) from exc
        self._ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)

    def reset(self, ena: int = 1) -> None:
        # Hardware reset is board-specific (RTS/DTR or TT carrier control); flush buffers here.
        self._ser.reset_input_buffer()
        self._ser.reset_output_buffer()

    def exchange(self, frame: Sequence[int]) -> List[int]:
        self._ser.write(bytes(b & 0xFF for b in frame))
        self._ser.flush()
        raw = self._ser.read(8)
        if len(raw) != 8:
            raise ProtocolError(f"UART response timeout: expected 8 bytes, got {len(raw)}")
        return list(raw)

    def close(self) -> None:
        if hasattr(self, "_ser") and self._ser.is_open:
            self._ser.close()


class IcarusSimTransport(ProtocolTransport):
    """Live RTL simulation transport compiling `src/project.v` via Icarus Verilog."""

    evidence_class = "rtl_simulation"

    def __init__(self, root_dir: Path = ROOT_DIR) -> None:
        self._root = root_dir
        self._tmpdir = Path(tempfile.mkdtemp(prefix="tt_explorer_"))
        self._vvp_path = self._tmpdir / "explorer_sim.vvp"
        sources = [
            self._root / "src" / "project.v",
            self._root / "src" / "protocol_sequencer.v",
            self._root / "test" / "jtag_tap_model.v",
            self._root / "tools" / "sim_explorer_tb.v",
        ]
        cmd = ["iverilog", "-g2012", "-o", str(self._vvp_path), *(str(s) for s in sources)]
        comp = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if comp.returncode != 0:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            raise ProtocolError(f"iverilog compile failed:\n{comp.stderr}")

        self._proc = subprocess.Popen(
            ["vvp", str(self._vvp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def _send_line(self, line: str) -> str:
        if self._proc.stdin is None or self._proc.stdout is None:
            raise ProtocolError("Simulator pipe unavailable")
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()
        while True:
            out = self._proc.stdout.readline()
            if not out:
                err = self._proc.stderr.read() if self._proc.stderr else ""
                raise ProtocolError(f"Simulator exited unexpectedly: {err}")
            stripped = out.strip()
            if stripped.startswith(("OK", "RESP ", "STAT ", "TIMEOUT")):
                return stripped

    def reset(self, ena: int = 1) -> None:
        resp = self._send_line(f"r {ena & 1:x}")
        if resp != "OK":
            raise ProtocolError(f"Unexpected reset response: {resp}")

    def configure_target(
        self, mode: int, delay_ns: int = 23, spi_resp: int = 0xAA, i2c_ack: int = 1
    ) -> None:
        resp = self._send_line(f"c {mode} {int(delay_ns)} {spi_resp & 0xFF:02x} {int(i2c_ack)}")
        if resp != "OK":
            raise ProtocolError(f"Unexpected configure response: {resp}")

    def set_baud_sel(self, sel: int) -> None:
        resp = self._send_line(f"b {sel & 3}")
        if resp != "OK":
            raise ProtocolError(f"Unexpected baud-sel response: {resp}")

    def set_seed(self, seed: int) -> None:
        resp = self._send_line(f"s {seed & 0xFFFFFFFF:08x}")
        if resp != "OK":
            raise ProtocolError(f"Unexpected seed response: {resp}")

    def configure_param_slot(
        self, slot: int, op0: int, op1: int, cs_lo: int, cs_hi: int, err_byte: int
    ) -> None:
        resp = self._send_line(
            f"p {slot & 3} {op0 & 0xFF:02x} {op1 & 0xFF:02x} {int(cs_lo)} {int(cs_hi)} {err_byte & 0xFF:02x}"
        )
        if resp != "OK":
            raise ProtocolError(f"Unexpected param-slot response: {resp}")

    def configure_param_normals(self, norm_ops: Sequence[int]) -> None:
        padded = list(norm_ops[:4]) + [0] * max(0, 4 - len(norm_ops))
        hx = " ".join(f"{b & 0xFF:02x}" for b in padded)
        resp = self._send_line(f"n {hx}")
        if resp != "OK":
            raise ProtocolError(f"Unexpected param-normal response: {resp}")

    def exchange(self, frame: Sequence[int]) -> List[int]:
        if len(frame) != 8:
            raise ValueError("UART request frame must be 8 bytes")
        hex_bytes = " ".join(f"{b & 0xFF:02x}" for b in frame)
        line = self._send_line(f"f {hex_bytes}")
        if line == "TIMEOUT":
            raise ProtocolError("Simulator UART response timeout")
        parts = line.split()
        if len(parts) != 9 or parts[0] != "RESP":
            raise ProtocolError(f"Malformed RESP line: {line}")
        return [int(tok, 16) for tok in parts[1:]]

    def wait_execution(self, max_cycles: int = 4000) -> SimObservations:
        line = self._send_line(f"w {int(max_cycles)}")
        parts = line.split()
        if len(parts) != 7 or parts[0] != "STAT":
            raise ProtocolError(f"Malformed STAT line: {line}")
        flags = int(parts[5], 16)
        return SimObservations(
            uo_out=int(parts[1], 16),
            spi_mosi_rx=int(parts[2], 16),
            uart_mon_byte=int(parts[3], 16),
            i2c_rx_byte=int(parts[4], 16),
            uart_mon_seen=bool(flags & 0x01),
            i2c_seen=bool(flags & 0x02),
            i2c_od_fault=bool(flags & 0x04),
        )

    def wait_scan_completion(self, max_cycles: int = 200000) -> Tuple[int, int]:
        line = self._send_line(f"x {int(max_cycles)}")
        parts = line.split()
        if len(parts) != 7 or parts[0] != "STAT":
            raise ProtocolError(f"Malformed STAT line: {line}")
        return int(parts[1], 16), int(parts[6])

    def close(self) -> None:
        try:
            if self._proc.poll() is None and self._proc.stdin is not None:
                self._proc.stdin.write("q\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
        except Exception:
            self._proc.kill()
        finally:
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    stream.close()
            shutil.rmtree(self._tmpdir, ignore_errors=True)


class ProtocolClient:
    """High-level v1 UART loader and sequencer control client."""

    def __init__(self, transport: ProtocolTransport) -> None:
        self.transport = transport
        self._req_id = 1
        self._cached_words: Optional[List[int]] = None
        self.uart_frames_sent = 0

    def invalidate_cache(self) -> None:
        self._cached_words = None

    def _next_id(self) -> int:
        rid = self._req_id & 0xFF
        self._req_id = (self._req_id + 1) & 0xFF
        if self._req_id == 0:
            self._req_id = 1
        return rid

    def command(
        self, opcode: int, arg0: int = 0, arg1: int = 0, arg2: int = 0, arg3: int = 0
    ) -> Tuple[int, List[int]]:
        rid = self._next_id()
        req = make_uart_request(opcode, rid, arg0, arg1, arg2, arg3)
        self.uart_frames_sent += 1
        resp = self.transport.exchange(req)
        if resp[0] != 0x5A:
            raise ProtocolError(f"Bad response sync byte: 0x{resp[0]:02x}")
        if resp[1] != rid:
            raise ProtocolError(f"Response req_id mismatch: expected {rid}, got {resp[1]}")
        exp_crc = crc8_07(resp[:7])
        if resp[7] != exp_crc:
            raise ProtocolError(
                f"Response CRC mismatch: expected 0x{exp_crc:02x}, got 0x{resp[7]:02x}"
            )
        status = resp[2]
        return status, resp[3:7]

    def require_ok(
        self, opcode: int, arg0: int = 0, arg1: int = 0, arg2: int = 0, arg3: int = 0
    ) -> List[int]:
        status, data = self.command(opcode, arg0, arg1, arg2, arg3)
        if status != 0:
            name = STATUS_NAMES.get(status, f"0x{status:02x}")
            raise ProtocolError(f"Command 0x{opcode:02x} failed with status {name}")
        return data

    def ping(self) -> Dict[str, Any]:
        d = self.require_ok(UART_OP_PING_INFO)
        return {
            "version": d[0],
            "prog_depth": d[1],
            "event_depth": d[2],
            "eng_state": d[3] & 0x3,
            "eng_state_name": ENG_STATE_NAMES.get(d[3] & 0x3, "UNKNOWN"),
            "event_overflow": bool((d[3] >> 2) & 1),
            "prog_valid": bool((d[3] >> 3) & 1),
        }

    def load_program(self, words: Sequence[int]) -> Dict[str, int]:
        crc, sum8 = program_checksums(words)
        self.require_ok(UART_OP_BEGIN_LOAD, len(words), crc, sum8)
        for addr, word in enumerate(words):
            self.require_ok(UART_OP_WRITE_WORD, addr, word & 0xFF, (word >> 8) & 0xFF)
        d = self.require_ok(UART_OP_COMMIT_LOAD)
        self._cached_words = [int(w) & 0xFFFF for w in words]
        return {"words": d[0], "crc8": d[1], "sum8": d[2], "prog_valid": d[3]}

    def patch_word(self, addr: int, word: int) -> None:
        w = int(word) & 0xFFFF
        self.require_ok(UART_OP_PATCH_WORD, addr, w & 0xFF, (w >> 8) & 0xFF)
        if self._cached_words is not None and 0 <= addr < len(self._cached_words):
            self._cached_words[addr] = w

    def sync_program(self, words: Sequence[int]) -> Dict[str, Any]:
        """Update instruction memory using minimal OP_PATCH_WORD writes when possible."""
        target = [int(w) & 0xFFFF for w in words]
        if self._cached_words is None or len(self._cached_words) != len(target):
            res = self.load_program(target)
            return {"mode": "full_load", "writes": len(target) + 2, **res}
        diffs = [(i, w) for i, (old, w) in enumerate(zip(self._cached_words, target)) if old != w]
        if len(diffs) >= len(target) + 2:
            res = self.load_program(target)
            return {"mode": "full_load", "writes": len(target) + 2, **res}
        for addr, w in diffs:
            self.patch_word(addr, w)
        return {"mode": "patch", "writes": len(diffs)}

    def start(self, run_param: int) -> None:
        if not (0 <= run_param <= 0xFFF):
            raise ValueError(f"run_param must fit in 12 bits, got {run_param}")
        self.require_ok(UART_OP_START, run_param & 0xFF, (run_param >> 8) & 0xF)

    def halt(self) -> None:
        self.require_ok(UART_OP_HALT)

    def read_status(self) -> Dict[str, Any]:
        d = self.require_ok(UART_OP_READ_STATUS)
        flags = d[0]
        return {
            "eng_state": flags & 0x3,
            "eng_state_name": ENG_STATE_NAMES.get(flags & 0x3, "UNKNOWN"),
            "event_overflow": bool((flags >> 2) & 1),
            "prog_valid": bool((flags >> 3) & 1),
            "pc": d[1],
            "sample_reg": d[2],
            "last_sample": (d[3] >> 7) & 1,
            "event_count": d[3] & 0xF,
        }

    def read_cycle_cnt(self) -> int:
        d = self.require_ok(UART_OP_READ_CYCLE_CNT)
        return d[0] | (d[1] << 8)

    def pop_all_events(self) -> List[Dict[str, int]]:
        events: List[Dict[str, int]] = []
        for _ in range(16):
            d = self.require_ok(UART_OP_POP_EVENT)
            count_before = d[0]
            if count_before == 0:
                break
            events.append({"tag": d[1], "ts_cycles": d[2] | (d[3] << 8)})
            if count_before == 1:
                break
        return events

    def run_trial(self, run_param: int, max_wait_cycles: int = 20000) -> Dict[str, Any]:
        self.start(run_param)
        sim_obs = self.transport.wait_execution(max_wait_cycles)
        status: Optional[Dict[str, Any]] = None
        for _ in range(200):
            st = self.read_status()
            if st["eng_state"] in (2, 3):  # ENG_HALTED or ENG_FAULT
                status = st
                break
            time.sleep(0.001)
        if status is None:
            raise ProtocolError(f"Sequencer failed to halt for run_param={run_param}")
        cycle_cnt = self.read_cycle_cnt()
        events = self.pop_all_events()
        record: Dict[str, Any] = {
            "run_param": run_param,
            "sample_reg": status["sample_reg"],
            "last_sample": status["last_sample"],
            "eng_state": status["eng_state_name"],
            "pc": status["pc"],
            "cycle_cnt": cycle_cnt,
            "event_overflow": status["event_overflow"],
            "events": events,
        }
        if sim_obs is not None:
            record["sim_observations"] = asdict(sim_obs)
        return record

    def configure_scan(
        self,
        param_start: int,
        param_step: int,
        num_steps: int,
        expected_val: int,
        pass_mode: int = 0,
    ) -> Dict[str, int]:
        if not (0 <= param_start <= 0xFFF):
            raise ValueError(f"param_start must fit in 12 bits, got {param_start}")
        if not (0 <= param_step <= 0xFF):
            raise ValueError(f"param_step must fit in 8 bits, got {param_step}")
        if not (1 <= num_steps <= 32):
            raise ValueError(f"num_steps must be in 1..32, got {num_steps}")
        if pass_mode not in (0, 1):
            raise ValueError(f"pass_mode must be 0 or 1, got {pass_mode}")
        cfg_byte = ((pass_mode & 1) << 6) | (num_steps & 0x3F)
        d = self.require_ok(
            UART_OP_CFG_SCAN,
            param_start & 0xFF,
            param_step & 0xFF,
            cfg_byte,
            expected_val & 0xFF,
        )
        return {
            "param_start_lsb": d[0],
            "param_step": d[1],
            "num_steps": d[2] & 0x3F,
            "pass_mode": (d[2] >> 6) & 1,
            "expected_val": d[3],
        }

    def start_scan(self, param_start_msb: int = 0) -> Dict[str, int]:
        d = self.require_ok(UART_OP_START_SCAN, param_start_msb & 0x0F)
        return {
            "param_start": d[0] | ((d[1] & 0x0F) << 8),
            "num_steps": d[2],
            "pass_mode": (d[3] >> 7) & 1,
        }

    def read_scan_status(self) -> Dict[str, Any]:
        d0 = self.require_ok(UART_OP_READ_SCAN_STATUS, 0)
        d1 = self.require_ok(UART_OP_READ_SCAN_STATUS, 1)
        steps_done = d0[1]
        bitmap_raw = d1[0] | (d1[1] << 8) | (d1[2] << 16) | (d1[3] << 24)
        return {
            "scan_active": bool(d0[0] & 0x01),
            "scan_done": bool((d0[0] >> 1) & 0x01),
            "steps_done": steps_done,
            "pass_count": d0[2],
            "fail_count": d0[3],
            "pass_bitmap": bitmap_raw,
            "points": [bool((bitmap_raw >> idx) & 1) for idx in range(steps_done)],
        }

    def read_scan_fail(self) -> Dict[str, Any]:
        d0 = self.require_ok(UART_OP_READ_SCAN_FAIL, 0)
        d1 = self.require_ok(UART_OP_READ_SCAN_FAIL, 1)
        valid = bool((d0[0] >> 7) & 1)
        state_code = (d0[2] >> 6) & 0x03
        return {
            "valid": valid,
            "fail_idx": d0[0] & 0x3F,
            "fail_param": d0[1] | ((d0[2] & 0x0F) << 8),
            "eng_state": state_code,
            "eng_state_name": ENG_STATE_NAMES.get(state_code, "UNKNOWN"),
            "sample_reg": d0[3],
            "cycle_cnt": d1[0] | (d1[1] << 8),
            "last_mark_tag": d1[2],
            "event_count": d1[3] & 0x0F,
        }

    @staticmethod
    def compute_scan_wait_cycles(
        param_start: int,
        param_step: int,
        num_steps: int,
        max_wait_cycles: int = 200000,
    ) -> int:
        """Return a simulation wait budget that covers high-parameter multi-step scans."""
        peak_param = min(0xFFF, max(0, param_start + max(0, num_steps) * max(0, param_step)))
        per_step_budget = max(4000, peak_param * 20 + 1000)
        return max(int(max_wait_cycles), max(0, int(num_steps)) * per_step_budget)

    def run_autonomous_scan(
        self,
        param_start: int,
        param_step: int,
        num_steps: int,
        expected_val: int,
        pass_mode: int = 0,
        max_wait_cycles: int = 200000,
    ) -> Dict[str, Any]:
        self.configure_scan(
            param_start=param_start,
            param_step=param_step,
            num_steps=num_steps,
            expected_val=expected_val,
            pass_mode=pass_mode,
        )
        self.start_scan((param_start >> 8) & 0x0F)
        effective_wait = self.compute_scan_wait_cycles(
            param_start=param_start,
            param_step=param_step,
            num_steps=num_steps,
            max_wait_cycles=max_wait_cycles,
        )
        sim_scan = self.transport.wait_scan_completion(effective_wait)
        status: Optional[Dict[str, Any]] = None
        for _ in range(400):
            st = self.read_scan_status()
            if st["scan_done"] and not st["scan_active"]:
                status = st
                break
            time.sleep(0.001)
        if status is None:
            raise ProtocolError("Autonomous scan failed to complete within poll limit")
        first_fail = self.read_scan_fail() if status["fail_count"] > 0 else {"valid": False}
        points = [
            {
                "step_idx": idx,
                "param": (param_start + idx * param_step) & 0xFFF,
                "passed": status["points"][idx],
            }
            for idx in range(status["steps_done"])
        ]
        res: Dict[str, Any] = {
            **status,
            "param_start": param_start,
            "param_step": param_step,
            "expected_val": expected_val,
            "pass_mode": pass_mode,
            "first_fail": first_fail,
            "results": points,
        }
        if sim_scan is not None:
            res["sim_scan_cycles"] = sim_scan[1]
            res["sim_uo_out"] = sim_scan[0]
        return res


def extract_contiguous_regions(observations: Dict[int, bool], target_state: bool = True) -> List[List[int]]:
    """Return inclusive [start, end] intervals of contiguous parameters matching `target_state`."""
    ordered = sorted(observations.items())
    regions: List[List[int]] = []
    cur_start: Optional[int] = None
    cur_end: Optional[int] = None
    for x, ok in ordered:
        if bool(ok) == target_state:
            if cur_start is None:
                cur_start = x
                cur_end = x
            elif cur_end is not None and x == cur_end + 1:
                cur_end = x
            else:
                regions.append([cur_start, cur_end])
                cur_start = x
                cur_end = x
        else:
            if cur_start is not None and cur_end is not None:
                regions.append([cur_start, cur_end])
                cur_start = None
                cur_end = None
    if cur_start is not None and cur_end is not None:
        regions.append([cur_start, cur_end])
    return regions


def summarize_repeated_trials(param_outcomes: Dict[int, Sequence[bool]]) -> Dict[str, Any]:
    """Summarize repeated binary trials per parameter with Wilson 95% intervals without forcing a hard boundary."""
    if not param_outcomes:
        return {"status": "empty", "boundary_low_cycles": None, "per_param": [], "uncertain_params": []}
    z = 1.96
    per_param: List[Dict[str, Any]] = []
    uncertain: List[int] = []
    for x in sorted(param_outcomes):
        vals = [bool(v) for v in param_outcomes[x]]
        n = len(vals)
        k = sum(vals)
        p = k / n if n else 0.0
        denom = 1.0 + (z * z) / n if n else 1.0
        center = (p + (z * z) / (2.0 * n)) / denom if n else 0.0
        margin = (z * ((p * (1.0 - p) + (z * z) / (4.0 * n)) / n) ** 0.5) / denom if n else 0.0
        lo = max(0.0, center - margin)
        hi = min(1.0, center + margin)
        if 0 < k < n:
            uncertain.append(x)
        per_param.append(
            {
                "param": x,
                "passes": k,
                "trials": n,
                "pass_rate": round(p, 4),
                "wilson_lo": round(lo, 4),
                "wilson_hi": round(hi, 4),
            }
        )
    if uncertain:
        return {
            "status": "probabilistic_transition",
            "boundary_low_cycles": None,
            "uncertain_params": uncertain,
            "transition_region": [uncertain[0], uncertain[-1]],
            "per_param": per_param,
            "note": f"Observed stochastic pass/fail disagreement at {uncertain}; cannot report a single deterministic boundary.",
        }
    deterministic = {row["param"]: (row["passes"] == row["trials"]) for row in per_param}
    det = classify_boundary(deterministic)
    det["per_param"] = per_param
    det["uncertain_params"] = []
    return det


def classify_boundary(observations: Dict[int, bool]) -> Dict[str, Any]:
    """Classify boundary strictly: censored or non-monotonic outcomes are never collapsed into fake bounds."""
    if not observations:
        return {"status": "empty", "boundary_low_cycles": None, "note": "No observations."}
    ordered = sorted(observations.items())
    first_x, first_ok = ordered[0]
    last_x, last_ok = ordered[-1]
    pass_regions = extract_contiguous_regions(observations, True)
    fail_regions = extract_contiguous_regions(observations, False)

    # Check monotonicity across evaluated points
    seen_pass = False
    monotone = True
    for _, ok in ordered:
        if ok:
            seen_pass = True
        elif seen_pass:
            monotone = False
            break

    if all(ok for _, ok in ordered):
        return {
            "status": "censored_all_pass",
            "boundary_low_cycles": None,
            "pass_regions": pass_regions,
            "fail_regions": fail_regions,
            "note": f"Lowest tested low_cycles={first_x} already passed; true boundary <= {first_x}.",
        }
    if all(not ok for _, ok in ordered):
        return {
            "status": "all_fail_unbracketed",
            "boundary_low_cycles": None,
            "pass_regions": pass_regions,
            "fail_regions": fail_regions,
            "note": f"All tested values up to low_cycles={last_x} failed; true boundary > {last_x}.",
        }
    if not monotone:
        return {
            "status": "non_monotone",
            "boundary_low_cycles": None,
            "pass_regions": pass_regions,
            "fail_regions": fail_regions,
            "note": f"Observed non-monotonic response across pass regions {pass_regions} and fail regions {fail_regions}.",
        }

    # Find exact interior fail -> pass transition among contiguous or sampled points
    for idx in range(1, len(ordered)):
        prev_x, prev_ok = ordered[idx - 1]
        cur_x, cur_ok = ordered[idx]
        if (not prev_ok) and cur_ok:
            if cur_x == prev_x + 1:
                return {
                    "status": "bracketed",
                    "boundary_low_cycles": cur_x,
                    "highest_fail_low_cycles": prev_x,
                    "lowest_pass_low_cycles": cur_x,
                    "pass_regions": pass_regions,
                    "fail_regions": fail_regions,
                    "note": f"Exact unit-step transition: fail at {prev_x}, pass at {cur_x}.",
                }
            return {
                "status": "bracketed_coarse",
                "boundary_low_cycles": None,
                "highest_fail_low_cycles": prev_x,
                "lowest_pass_low_cycles": cur_x,
                "pass_regions": pass_regions,
                "fail_regions": fail_regions,
                "note": f"Unrefined gap between fail at {prev_x} and pass at {cur_x}.",
            }
    return {
        "status": "unknown",
        "boundary_low_cycles": None,
        "pass_regions": pass_regions,
        "fail_regions": fail_regions,
        "note": "Unclassified.",
    }


def _git_revision() -> Optional[str]:
    res = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode == 0:
        return res.stdout.strip()
    return None


def run_spi_boundary_exploration(
    client: ProtocolClient,
    *,
    mosi_byte: int = 0x55,
    expected_miso: int = 0xAA,
    min_low_cycles: int = 2,
    max_low_cycles: int = 8,
    method: str = "sweep",
    delay_ns: Optional[int] = 23,
    clock_ns: Optional[int] = 10,
) -> List[Dict[str, Any]]:
    """Run an end-to-end SPI mode-0 timing sweep or binary search through the ASIC."""
    if min_low_cycles < 2:
        raise ValueError("build_spi_mode0_program enforces low_cycles = run_param + 2 >= 2")
    if max_low_cycles < min_low_cycles:
        raise ValueError("max_low_cycles must be >= min_low_cycles")

    info = client.ping()
    words = build_spi_mode0_program(mosi_byte)
    crc, sum8 = program_checksums(words)
    load_info = client.load_program(words)

    records: List[Dict[str, Any]] = []
    target_oracle: Optional[Dict[str, Any]] = None
    if delay_ns is not None and clock_ns is not None:
        tgt = Target(
            clock_fs=clock_ns * 1_000_000,
            output_delay_fs=delay_ns * 1_000_000,
            setup_fs=0,  # Zero-delay RTL sampling edge (digital clock boundary model)
            high_cycles=2,
            response=expected_miso,
        )
        target_oracle = {
            "clock_ns": clock_ns,
            "target_delay_ns": delay_ns,
            "rtl_zero_setup_min_low_cycles": tgt.minimum_passing_low_cycles,
        }

    records.append(
        {
            "type": "metadata",
            "schema_version": 1,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": _git_revision(),
            "evidence_class": client.transport.evidence_class,
            "protocol": "spi_mode0",
            "param_mapping": "low_cycles = run_param + 2; high_cycles = 2",
            "device_info": info,
            "mosi_byte": mosi_byte,
            "expected_miso": expected_miso,
            "method": method,
            "min_low_cycles": min_low_cycles,
            "max_low_cycles": max_low_cycles,
            "oracle": target_oracle,
        }
    )
    records.append(
        {
            "type": "program",
            "length": len(words),
            "crc8": crc,
            "sum8": sum8,
            "commit_response": load_info,
            "words_hex": [f"0x{w:04x}" for w in words],
        }
    )

    observed: Dict[int, bool] = {}

    def eval_low(low_cycles: int) -> Dict[str, Any]:
        run_param = low_cycles - 2
        res = client.run_trial(run_param)
        contract = predict_straight_line_cycles(words, run_param)
        obs_marks = [e["ts_cycles"] for e in res["events"]]
        contract_ok = (
            res["cycle_cnt"] == contract["total_cycles"]
            and obs_marks == contract["mark_cycles"]
        )
        passed = (
            res["sample_reg"] == expected_miso
            and res["eng_state"] == "ENG_HALTED"
            and contract_ok
        )
        if "sim_observations" in res:
            passed = passed and (res["sim_observations"]["spi_mosi_rx"] == mosi_byte)
        observed[low_cycles] = passed
        res["type"] = "trial"
        res["low_cycles"] = low_cycles
        res["expected_miso"] = expected_miso
        res["contract_cycles"] = contract["total_cycles"]
        res["contract_ok"] = contract_ok
        res["passed"] = passed
        records.append(res)
        return res

    if method == "sweep":
        for lc in range(min_low_cycles, max_low_cycles + 1):
            eval_low(lc)
    elif method == "binary":
        eval_low(min_low_cycles)
        if not observed[min_low_cycles]:
            eval_low(max_low_cycles)
            if observed[max_low_cycles]:
                lo = min_low_cycles
                hi = max_low_cycles
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    eval_low(mid)
                    if observed[mid]:
                        hi = mid
                    else:
                        lo = mid
    else:
        raise ValueError(f"Unsupported search method: {method!r}")

    boundary = classify_boundary(observed)
    records.append(
        {
            "type": "summary",
            "method": method,
            "trials_run": len(observed),
            "results_by_low_cycles": {str(k): v for k, v in sorted(observed.items())},
            "boundary": boundary,
        }
    )
    return records


def replay_records(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify checksums and recompute boundary classification from a saved trace."""
    meta = next((r for r in records if r.get("type") == "metadata"), None)
    prog = next((r for r in records if r.get("type") == "program"), None)
    trials = [r for r in records if r.get("type") == "trial"]
    stored_summary = next((r for r in records if r.get("type") == "summary"), None)
    if meta is None or prog is None or not trials:
        raise ProtocolError("Trace missing required metadata, program, or trial records")

    words = [int(w, 16) for w in prog["words_hex"]]
    crc, sum8 = program_checksums(words)
    if crc != prog["crc8"] or sum8 != prog["sum8"]:
        raise ProtocolError("Stored program checksum mismatch during replay verification")

    expected_miso = meta["expected_miso"]
    obs: Dict[int, bool] = {}
    for tr in trials:
        recomputed_pass = (tr["sample_reg"] == expected_miso) and (tr["eng_state"] == "ENG_HALTED")
        if "sim_observations" in tr:
            recomputed_pass = recomputed_pass and (
                tr["sim_observations"]["spi_mosi_rx"] == meta["mosi_byte"]
            )
        if recomputed_pass != tr["passed"]:
            raise ProtocolError(
                f"Trial pass mismatch on low_cycles={tr['low_cycles']}: trace={tr['passed']} vs recomputed={recomputed_pass}"
            )
        obs[int(tr["low_cycles"])] = recomputed_pass

    boundary = classify_boundary(obs)
    matches_stored = (
        stored_summary is not None and stored_summary.get("boundary") == boundary
    )
    return {
        "metadata": meta,
        "program_words": len(words),
        "program_crc8": crc,
        "trials": trials,
        "boundary": boundary,
        "matches_stored_summary": matches_stored,
    }


def execute_replay(
    records: Sequence[Dict[str, Any]],
    client: ProtocolClient,
    *,
    repeats: int = 5,
    only_failures: bool = True,
) -> Dict[str, Any]:
    """Experiment 2: reload saved program onto the ASIC and re-execute recorded cases."""
    verified = replay_records(records)
    meta = verified["metadata"]
    prog_rec = next(r for r in records if r.get("type") == "program")
    words = [int(w, 16) for w in prog_rec["words_hex"]]
    if isinstance(client.transport, IcarusSimTransport) and meta.get("oracle"):
        client.transport.configure_target(
            0,
            delay_ns=meta["oracle"]["target_delay_ns"],
            spi_resp=meta["expected_miso"],
        )
    client.load_program(words)

    selected = [t for t in verified["trials"] if (not only_failures or not t["passed"])]
    if not selected:
        selected = verified["trials"][:1]

    case_reports: List[Dict[str, Any]] = []
    for orig in selected:
        matches = 0
        trials_detail: List[Dict[str, Any]] = []
        for _ in range(repeats):
            rerun = client.run_trial(orig["run_param"])
            exact = (
                rerun["sample_reg"] == orig["sample_reg"]
                and rerun["cycle_cnt"] == orig["cycle_cnt"]
                and rerun["eng_state"] == orig["eng_state"]
                and rerun["events"] == orig["events"]
            )
            if exact:
                matches += 1
            trials_detail.append(rerun)
        case_reports.append(
            {
                "run_param": orig["run_param"],
                "low_cycles": orig.get("low_cycles"),
                "expected_sample_reg": orig["sample_reg"],
                "expected_cycle_cnt": orig["cycle_cnt"],
                "repeats": repeats,
                "exact_matches": matches,
                "replay_rate": matches / repeats if repeats else 0.0,
            }
        )
    return {
        "protocol": meta["protocol"],
        "cases": case_reports,
        "all_deterministic": all(c["exact_matches"] == repeats for c in case_reports),
    }


def run_i2c_stretch_sweep(
    client: ProtocolClient,
    stretch_cycles: int,
    max_wait_values: Sequence[int],
) -> Dict[str, Any]:
    """Experiment 1 (I2C): measure slave clock-stretch release latency using WAIT_EDGE."""
    words = build_i2c_stretch_probe_program(scl_pin=0, sda_pin=1, half_cycles=4)
    if isinstance(client.transport, IcarusSimTransport):
        client.transport.configure_target(4, delay_ns=stretch_cycles)
    client.load_program(words)
    obs: Dict[int, bool] = {}
    trials: List[Dict[str, Any]] = []
    for mw in max_wait_values:
        res = client.run_trial(mw)
        tags = [e["tag"] for e in res["events"]]
        tss = [e["ts_cycles"] for e in res["events"]]
        passed = (len(tags) >= 2) and (tags[1] == 0x51)
        obs[mw] = passed
        stretch_measured = (tss[1] - tss[0]) if len(tss) >= 2 else None
        edge_waits: Sequence[Optional[int]] = (
            [stretch_measured - 4] if (passed and stretch_measured is not None) else [None]
        )
        contract = check_timing_contract(
            words,
            mw,
            observed_cycles=res["cycle_cnt"],
            observed_events=res["events"],
            eng_state=res["eng_state"],
            edge_waits=edge_waits,
        )
        trials.append(
            {
                "max_wait": mw,
                "passed": passed,
                "tags": tags,
                "timestamps": tss,
                "measured_release_delta_cycles": stretch_measured,
                "cycle_cnt": res["cycle_cnt"],
                "contract_ok": contract["contract_ok"],
                "contract_errors": contract["errors"],
                "contract_bounds": [
                    contract["bounds"]["min_cycles"],
                    contract["bounds"]["max_cycles"],
                ],
            }
        )
    return {
        "target_stretch_cycles": stretch_cycles,
        "boundary": classify_boundary(obs),
        "trials": trials,
    }


def run_uart_period_sweep(
    client: ProtocolClient,
    byte_val: int = 0xA3,
    bit_cycles_list: Sequence[int] = (5, 6, 7, 8, 9, 10, 11),
) -> Dict[str, Any]:
    """Experiment 1 (UART): sweep TX bit_cycles against a fixed CLKS_PER_BIT=8 receiver."""
    trials: List[Dict[str, Any]] = []
    for bc in bit_cycles_list:
        words = build_uart_tx_program(byte_val, bit_cycles=bc, pin=0)
        if isinstance(client.transport, IcarusSimTransport):
            client.transport.configure_target(1)
        client.load_program(words)
        res = client.run_trial(0)
        sim_obs = res.get("sim_observations", {})
        passed = bool(sim_obs.get("uart_mon_seen")) and (sim_obs.get("uart_mon_byte") == byte_val)
        trials.append(
            {
                "bit_cycles": bc,
                "passed": passed,
                "rx_seen": sim_obs.get("uart_mon_seen"),
                "rx_byte": sim_obs.get("uart_mon_byte"),
                "cycle_cnt": res["cycle_cnt"],
            }
        )
    passing = [t["bit_cycles"] for t in trials if t["passed"]]
    return {"expected_byte": byte_val, "passing_bit_cycles": passing, "trials": trials}


def format_report_markdown(replayed: Dict[str, Any]) -> str:
    meta = replayed["metadata"]
    boundary = replayed["boundary"]
    lines = [
        "# Timing Boundary Explorer — Run Report",
        "",
        f"- **Timestamp (UTC):** {meta['timestamp_utc']}",
        f"- **Git commit:** `{meta.get('git_commit') or 'unknown'}`",
        f"- **Evidence class:** `{meta['evidence_class']}`",
        f"- **Method:** `{meta['method']}` ({len(replayed['trials'])} trials executed)",
        f"- **Program:** `spi_mode0` ({replayed['program_words']} words, `crc8=0x{replayed['program_crc8']:02x}`)",
        f"- **Boundary status:** `{boundary['status']}` — boundary low cycles = `{boundary['boundary_low_cycles']}`",
        f"- **Note:** {boundary['note']}",
    ]
    if meta.get("oracle"):
        orc = meta["oracle"]
        lines.append(
            f"- **Oracle reference:** `clock={orc['clock_ns']} ns`, `delay={orc['target_delay_ns']} ns` -> theoretical first passing low cycle `{orc['rtl_zero_setup_min_low_cycles']}`"
        )
    lines += [
        "",
        "| `run_param` | `low_cycles` | Sampled MISO | Cycles | Event Tag | Pass |",
        "|---|---|---|---|---|---|",
    ]
    for tr in sorted(replayed["trials"], key=lambda r: r["low_cycles"]):
        ev_tag = f"0x{tr['events'][0]['tag']:02x}" if tr.get("events") else "-"
        lines.append(
            f"| {tr['run_param']} | {tr['low_cycles']} | `0x{tr['sample_reg']:02x}` | {tr['cycle_cnt']} | {ev_tag} | {'PASS' if tr['passed'] else 'FAIL'} |"
        )
    return "\n".join(lines) + "\n"


def write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _make_transport(args: argparse.Namespace) -> ProtocolTransport:
    if args.sim:
        return IcarusSimTransport()
    if args.port:
        return SerialTransport(args.port, baud=args.baud)
    raise ProtocolError("Specify either --sim (Icarus RTL) or --port <serial_device>.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Timing Boundary Explorer host CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="Run an SPI mode-0 timing-boundary exploration")
    p_sweep.add_argument("--sim", action="store_true", help="Run live Icarus RTL simulation")
    p_sweep.add_argument("--port", help="Serial device path for hardware UART")
    p_sweep.add_argument("--baud", type=int, default=115200)
    p_sweep.add_argument("--method", choices=["sweep", "binary"], default="sweep")
    p_sweep.add_argument("--delay-ns", type=int, default=23)
    p_sweep.add_argument("--min-low", type=int, default=2)
    p_sweep.add_argument("--max-low", type=int, default=8)
    p_sweep.add_argument("--mosi", type=lambda x: int(x, 0), default=0x55)
    p_sweep.add_argument("--expect-miso", type=lambda x: int(x, 0), default=0xAA)
    p_sweep.add_argument("--out", type=Path, help="Output JSONL trace path")
    p_sweep.add_argument("--report", type=Path, help="Output Markdown summary report path")

    p_replay = sub.add_parser("replay", help="Verify and optionally re-execute a saved JSONL trace")
    p_replay.add_argument("trace", type=Path)
    p_replay.add_argument("--report", type=Path)
    p_replay.add_argument("--exec", dest="do_exec", action="store_true", help="Re-run stored cases on target")
    p_replay.add_argument("--sim", action="store_true")
    p_replay.add_argument("--port")
    p_replay.add_argument("--baud", type=int, default=115200)
    p_replay.add_argument("--trials", type=int, default=5)
    p_replay.add_argument("--all", dest="all_cases", action="store_true")

    p_ex = sub.add_parser("run-example", help="Run built-in protocol example over UART")
    p_ex.add_argument("--sim", action="store_true")
    p_ex.add_argument("--port")
    p_ex.add_argument("--baud", type=int, default=115200)
    p_ex.add_argument(
        "--example",
        choices=[
            "uart",
            "uart-multibyte",
            "spi",
            "spi-multibyte",
            "spi-mode1",
            "spi-mode2",
            "spi-mode3",
            "spi-responder",
            "i2c",
            "i2c-multibyte",
            "i2c-100k",
            "i2c-400k-nominal",
            "i2c-fast-compliant",
            "jtag",
        ],
        required=True,
    )
    p_ex.add_argument("--param", type=int, default=2)
    p_ex.add_argument("--delay-ns", type=int, default=23)
    p_ex.add_argument("--i2c-ack", type=int, choices=[0, 1], default=1)

    p_scan = sub.add_parser("scan", help="Run an on-chip autonomous SPI timing scan (0x1A-0x1D)")
    p_scan.add_argument("--sim", action="store_true")
    p_scan.add_argument("--port")
    p_scan.add_argument("--baud", type=int, default=115200)
    p_scan.add_argument("--delay-ns", type=int, default=37)
    p_scan.add_argument("--start", type=int, default=0)
    p_scan.add_argument("--step", type=int, default=1)
    p_scan.add_argument("--steps", type=int, default=16)
    p_scan.add_argument("--mosi", type=lambda x: int(x, 0), default=0x55)
    p_scan.add_argument("--expect", type=lambda x: int(x, 0), default=0xAA)
    p_scan.add_argument("--pass-mode", type=int, choices=[0, 1], default=0)

    args = parser.parse_args(argv)
    if args.cmd == "replay":
        records = read_jsonl(args.trace)
        replayed = replay_records(records)
        md = format_report_markdown(replayed)
        if args.do_exec:
            with _make_transport(args) as transport:
                transport.reset(1)
                exec_res = execute_replay(
                    records,
                    ProtocolClient(transport),
                    repeats=args.trials,
                    only_failures=not args.all_cases,
                )
            md += "\n## Live Execution Replay Results\n\n```json\n" + json.dumps(exec_res, indent=2) + "\n```\n"
        if args.report:
            args.report.write_text(md, encoding="utf-8")
        print(md)
        return 0

    with _make_transport(args) as transport:
        transport.reset(1)
        client = ProtocolClient(transport)
        if args.cmd == "sweep":
            if args.sim:
                transport.configure_target(0, delay_ns=args.delay_ns, spi_resp=args.expect_miso)
            records = run_spi_boundary_exploration(
                client,
                mosi_byte=args.mosi,
                expected_miso=args.expect_miso,
                min_low_cycles=args.min_low,
                max_low_cycles=args.max_low,
                method=args.method,
                delay_ns=args.delay_ns if args.sim else None,
            )
            if args.out:
                write_jsonl(args.out, records)
            replayed = replay_records(records)
            md = format_report_markdown(replayed)
            if args.report:
                args.report.write_text(md, encoding="utf-8")
            print(md)
            return 0

        if args.cmd == "scan":
            if args.sim:
                transport.configure_target(0, delay_ns=args.delay_ns, spi_resp=args.expect)
            words = build_spi_mode0_program(args.mosi)
            client.load_program(words)
            res = client.run_autonomous_scan(
                param_start=args.start,
                param_step=args.step,
                num_steps=args.steps,
                expected_val=args.expect,
                pass_mode=args.pass_mode,
            )
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0

        if args.cmd == "run-example":
            param = args.param
            if args.example == "uart":
                if args.sim:
                    transport.configure_target(1)
                words = build_uart_tx_program(0xA3, bit_cycles=8, pin=0)
            elif args.example == "uart-multibyte":
                if args.sim:
                    transport.configure_target(1)
                words, _ = build_uart_tx_multibyte_program([0xA3, 0x5C], bit_cycles=8, pin=0)
            elif args.example == "spi":
                if args.sim:
                    transport.configure_target(0, delay_ns=args.delay_ns, spi_resp=0xAA)
                words = build_spi_mode0_program(0x55)
            elif args.example == "spi-multibyte":
                if args.sim:
                    transport.configure_target(0, delay_ns=args.delay_ns, spi_resp=0xAA)
                words, _ = build_spi_multibyte_program([0xA5, 0x3C], lsb_first=False)
            elif args.example in ("spi-mode1", "spi-mode2", "spi-mode3"):
                spi_mode = int(args.example[-1])
                if args.sim:
                    transport.configure_target(11 + spi_mode, delay_ns=args.delay_ns, spi_resp=0xAA)
                words = build_spi_program(0x55, mode=spi_mode)
            elif args.example == "spi-responder":
                if args.sim:
                    transport.configure_target(15, delay_ns=2, spi_resp=0xA5, i2c_ack=6)
                words = build_spi_target_responder_program(0x5A, max_wait=0)
                if param <= 2:
                    param = 120
            elif args.example == "i2c":
                if args.sim:
                    transport.configure_target(2, i2c_ack=args.i2c_ack)
                words = build_i2c_write_byte_program(0xA0, half_cycles=4)
            elif args.example == "i2c-multibyte":
                if args.sim:
                    transport.configure_target(2, i2c_ack=args.i2c_ack)
                words, _ = build_i2c_write_multibyte_program([0xA0, 0x55], half_cycles=None)
                if param <= 2:
                    param = 62
            elif args.example == "i2c-100k":
                if args.sim:
                    transport.configure_target(2, i2c_ack=args.i2c_ack)
                words = build_i2c_write_byte_program(0xA0, half_cycles=250)
            elif args.example == "i2c-400k-nominal":
                if args.sim:
                    transport.configure_target(2, i2c_ack=args.i2c_ack)
                words = build_i2c_write_byte_program(0xA0, half_cycles=62)
            elif args.example == "i2c-fast-compliant":
                if args.sim:
                    transport.configure_target(2, i2c_ack=args.i2c_ack)
                words = build_i2c_write_byte_program(0xA0, half_cycles=64)
            else:
                if args.sim:
                    transport.configure_target(11, delay_ns=0)
                words = build_jtag_idcode_program()
            client.load_program(words)
            result = client.run_trial(param)
            if args.example == "jtag":
                result["decoded_idcode_hex"] = f"0x{decode_jtag_idcode_events(result['events']):08x}"
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
