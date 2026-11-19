# SPDX-License-Identifier: Apache-2.0
"""Pulse-by-pulse 50 MHz I2C timing audit against UM10204 Standard & Fast-mode limits."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import (  # noqa: E402
    UART_OP_BEGIN_LOAD,
    UART_OP_COMMIT_LOAD,
    UART_OP_START,
    UART_OP_WRITE_WORD,
    build_i2c_write_byte_program,
    build_i2c_write_multibyte_program,
    make_uart_request,
    program_checksums,
)

# Idealized 0 ns RC pull-up limits from NXP UM10204 I2C-bus specification (Rev 7.0, Table 10)
I2C_LIMITS_NS: Dict[str, Dict[str, float]] = {
    "standard_100k": {
        "max_freq_khz": 100.0,
        "min_low_ns": 4700.0,
        "min_high_ns": 4000.0,
        "min_hd_sta_ns": 4000.0,
        "min_su_dat_ns": 250.0,
        "min_hd_dat_ns": 0.0,
        "min_su_sto_ns": 4000.0,
        "min_buf_ns": 4700.0,
    },
    "fast_400k": {
        "max_freq_khz": 400.0,
        "min_low_ns": 1300.0,
        "min_high_ns": 600.0,
        "min_hd_sta_ns": 600.0,
        "min_su_dat_ns": 100.0,
        "min_hd_dat_ns": 0.0,
        "min_su_sto_ns": 600.0,
        "min_buf_ns": 1300.0,
    },
}

_AUDIT_TB_VERILOG = r"""`timescale 1ns/1ps
`default_nettype none

module tb_i2c_audit;
  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  reg        target_sda_low = 1'b0;
  reg        do_ack = 1'b1;
  integer    exp_bytes = 1;

  // Exact 50 MHz core clock (period = 20 ns matching info.yaml clock_hz=50000000)
  always #10 clk = ~clk;

  wire scl_bus = (uio_oe[0] && !uio_out[0]) ? 1'b0 : (uio_oe[0] ? uio_out[0] : 1'b1);
  wire sda_bus = target_sda_low ? 1'b0 : ((uio_oe[1] && !uio_out[1]) ? 1'b0 : (uio_oe[1] ? uio_out[1] : 1'b1));
  wire [7:0] uio_in = {6'h3F, sda_bus, scl_bus};

  tt_um_harjag_21_timing_explorer dut (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  task automatic uart_send;
    input [7:0] b;
    integer i;
    begin
      ui_in[0] = 1'b0;
      repeat (8) @(posedge clk);
      for (i = 0; i < 8; i = i + 1) begin
        ui_in[0] = b[i];
        repeat (8) @(posedge clk);
      end
      ui_in[0] = 1'b1;
      repeat (8) @(posedge clk);
    end
  endtask

  task automatic send_cmd8;
    input [7:0] b0, b1, b2, b3, b4, b5, b6, b7;
    begin
      uart_send(b0); uart_send(b1); uart_send(b2); uart_send(b3);
      uart_send(b4); uart_send(b5); uart_send(b6); uart_send(b7);
      // Wait for 8-byte 8N1 UART response (10 bits * 8 bytes * 8 clks = 640 clks + margin)
      repeat (720) @(posedge clk);
    end
  endtask

  reg prev_scl = 1'b1;
  reg prev_sda = 1'b1;
  reg prev_active = 1'b0;
  reg monitoring = 1'b0;
  reg od_fault = 1'b0;

  always @(posedge clk) begin
    prev_active <= uo_out[1];
    if (monitoring && !prev_active && uo_out[1]) begin
      $display("TRIAL_START %0t %0d %0d", $time, scl_bus, sda_bus);
    end
    if (monitoring) begin
      if ((uio_oe[0] && uio_out[0]) || (uio_oe[1] && uio_out[1])) begin
        od_fault = 1'b1;
        $display("OD_FAULT %0t oe=%02x out=%02x", $time, uio_oe, uio_out);
      end
      if (scl_bus !== prev_scl || sda_bus !== prev_sda) begin
        $display("EDGE %0t %0d %0d", $time, scl_bus, sda_bus);
        prev_scl = scl_bus;
        prev_sda = sda_bus;
      end
    end
  end

  initial begin : wd_blk
    #10000000;
    $display("DONE_TIMEOUT %0t state=%0d prog_valid=%0d scl=%0d sda=%0d", $time, uo_out[4:3], uo_out[2], scl_bus, sda_bus);
    $finish;
  end

  integer fd, rc, cmd, nwords, idx;
  integer a0, a1, a2, a3, a4, a5, a6, a7;
  initial begin
    rst_n = 1'b0;
    ena = 1'b1;
    ui_in = 8'h01;
    repeat (6) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    // Commands are supplied via +CMDS=<path>
    begin : run_blk
      reg [1023:0] cmd_path;
      integer ack_v;
      if (!$value$plusargs("CMDS=%s", cmd_path)) begin
        $display("ERR no +CMDS");
        $finish;
      end
      if (!$value$plusargs("ACK=%d", ack_v)) ack_v = 1;
      if (!$value$plusargs("BYTES=%d", exp_bytes)) exp_bytes = 1;
      do_ack = ack_v[0];
      fd = $fopen(cmd_path, "r");
      if (fd == 0) begin
        $display("ERR cannot open %0s", cmd_path);
        $finish;
      end
      while ($fscanf(fd, "%x %x %x %x %x %x %x %x", a0, a1, a2, a3, a4, a5, a6, a7) == 8) begin
        if (a1 == 8'h14) begin
          monitoring = 1'b1;
          prev_scl = scl_bus;
          prev_sda = sda_bus;
          $display("START_MON %0t %0d %0d", $time, scl_bus, sda_bus);
        end
        send_cmd8(a0[7:0], a1[7:0], a2[7:0], a3[7:0], a4[7:0], a5[7:0], a6[7:0], a7[7:0]);
      end
      $fclose(fd);
    end
  end

  // Behavioral open-drain ACK responder aligned to I2C START (supports 1..2 bytes)
  always @(negedge sda_bus) begin : ack_blk
    integer b, by;
    reg [7:0] rx_byte, rx_byte1;
    if (monitoring && scl_bus === 1'b1) begin
      rx_byte = 8'h00;
      rx_byte1 = 8'h00;
      while (scl_bus === 1'b1) @(posedge clk);
      for (by = 0; by < exp_bytes; by = by + 1) begin
        for (b = 0; b < 8; b = b + 1) begin
          while (scl_bus === 1'b0) @(posedge clk);
          if (by == 0) rx_byte = {rx_byte[6:0], sda_bus};
          else         rx_byte1 = {rx_byte1[6:0], sda_bus};
          while (scl_bus === 1'b1) @(posedge clk);
        end
        if (do_ack) target_sda_low = 1'b1;
        while (scl_bus === 1'b0) @(posedge clk);
        while (scl_bus === 1'b1) @(posedge clk);
        target_sda_low = 1'b0;
        if (!do_ack) by = exp_bytes;
      end
      while (uo_out[4:3] != 2'd2 && uo_out[4:3] != 2'd3) @(posedge clk);
      repeat (4) @(posedge clk);
      $display("DONE %0t state=%0d od_fault=%0d rx=%02x rx1=%02x", $time, uo_out[4:3], od_fault, rx_byte, rx_byte1);
      $finish;
    end
  end
endmodule
`default_nettype wire
"""


def _compile_audit_vvp(build_dir: Path) -> Path:
    tb_path = build_dir / "tb_i2c_audit.v"
    vvp_path = build_dir / "i2c_audit.vvp"
    tb_path.write_text(_AUDIT_TB_VERILOG, encoding="utf-8")
    iverilog = shutil.which("iverilog") or "/usr/bin/iverilog"
    cmd = [
        iverilog,
        "-g2012",
        "-Wall",
        "-s",
        "tb_i2c_audit",
        "-o",
        str(vvp_path),
        str(ROOT_DIR / "src" / "project.v"),
        str(ROOT_DIR / "src" / "protocol_sequencer.v"),
        str(tb_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError(f"iverilog failed:\n{res.stderr}\n{res.stdout}")
    return vvp_path


def _run_i2c_rtl_trace(
    vvp_path: Path,
    build_dir: Path,
    words: Sequence[int],
    do_ack: int,
    *,
    run_param: int = 0,
    exp_bytes: int = 1,
    tag_name: str = "run",
) -> List[str]:
    crc, sum8 = program_checksums(words)
    frames: List[List[int]] = [make_uart_request(UART_OP_BEGIN_LOAD, 1, len(words), crc, sum8)]
    for idx, w in enumerate(words):
        frames.append(make_uart_request(UART_OP_WRITE_WORD, 2 + idx, idx, w & 0xFF, (w >> 8) & 0xFF))
    frames.append(make_uart_request(UART_OP_COMMIT_LOAD, 99))
    frames.append(make_uart_request(UART_OP_START, 100, run_param & 0xFF, (run_param >> 8) & 0xF))

    cmd_path = build_dir / f"cmds_{tag_name}_ack{do_ack}.hex"
    cmd_path.write_text("\n".join(" ".join(f"{b:02x}" for b in fr) for fr in frames) + "\n", encoding="utf-8")
    vvp = shutil.which("vvp") or "/usr/bin/vvp"
    res = subprocess.run(
        [vvp, str(vvp_path), f"+CMDS={cmd_path}", f"+ACK={do_ack}", f"+BYTES={exp_bytes}"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"vvp failed:\n{res.stderr}\n{res.stdout}")
    return res.stdout.splitlines()


def analyze_i2c_trace(
    lines: Sequence[str],
    *,
    mode_name: str,
    spec_profile: str,
    expected_rx_bytes: Sequence[int] = (0xA0,),
) -> Dict[str, Any]:
    """Analyze pulse-by-pulse START, bytes (8 bits + ACK/NACK each), STOP intervals in ns."""
    edges: List[Dict[str, int]] = []
    start_ns: Optional[int] = None
    trial_start_ns: Optional[int] = None
    done_state: Optional[int] = None
    rx_byte: Optional[int] = None
    rx_byte1: Optional[int] = None
    od_fault = 0
    for line in lines:
        parts = line.strip().split()
        if not parts:
            continue
        if parts[0] == "START_MON":
            start_ns = int(round(int(parts[1]) / 1000.0))
        elif parts[0] == "TRIAL_START":
            trial_start_ns = int(round(int(parts[1]) / 1000.0))
        elif parts[0] == "OD_FAULT":
            od_fault = 1
        elif parts[0] == "EDGE":
            edges.append(
                {
                    "t_ns": int(round(int(parts[1]) / 1000.0)),
                    "scl": int(parts[2]),
                    "sda": int(parts[3]),
                }
            )
        elif parts[0] == "DONE":
            done_state = int(parts[2].split("=")[1])
            od_fault |= int(parts[3].split("=")[1])
            rx_byte = int(parts[4].split("=")[1], 16)
            if len(parts) > 5 and parts[5].startswith("rx1="):
                rx_byte1 = int(parts[5].split("=")[1], 16)

    if od_fault:
        raise AssertionError("Open-drain violation detected")
    if done_state != 2:
        raise AssertionError(f"Expected ENG_HALTED (2), got {done_state}; tail={list(lines)[-15:]}")
    if rx_byte != expected_rx_bytes[0]:
        raise AssertionError(f"Expected I2C rx_byte=0x{expected_rx_bytes[0]:02x}, got {rx_byte}")
    if len(expected_rx_bytes) > 1 and rx_byte1 != expected_rx_bytes[1]:
        raise AssertionError(f"Expected I2C rx_byte1=0x{expected_rx_bytes[1]:02x}, got {rx_byte1}")

    # Find START (scl=1, sda: 1->0) and STOP (scl=1, sda: 0->1)
    start_evt: Optional[int] = None
    stop_evt: Optional[int] = None
    for idx in range(len(edges)):
        cur = edges[idx]
        prev_scl = 1 if idx == 0 else edges[idx - 1]["scl"]
        prev_sda = 1 if idx == 0 else edges[idx - 1]["sda"]
        if start_evt is None and prev_scl == 1 and cur["scl"] == 1 and prev_sda == 1 and cur["sda"] == 0:
            start_evt = idx
        if start_evt is not None and prev_scl == 1 and cur["scl"] == 1 and prev_sda == 0 and cur["sda"] == 1:
            stop_evt = idx
    assert start_evt is not None and stop_evt is not None, f"Missing START/STOP in edges: {edges}"

    t_start_ns = edges[start_evt]["t_ns"]
    t_stop_ns = edges[stop_evt]["t_ns"]
    assert trial_start_ns is not None, "Missing TRIAL_START"
    # trial_start_ns is logged on the posedge after ENG_RUN asserts (1 cycle = 20 ns into pre-START idle hold)
    t_buf_pre_ns = (t_start_ns - trial_start_ns) + 20

    # N bytes -> 9*N data/ACK pulses + 1 STOP prep SCL rise/fall pair = 9*N + 1 falls/rises
    n_bytes = len(expected_rx_bytes)
    n_pulses = 9 * n_bytes
    scl_falls_all = [e["t_ns"] for idx, e in enumerate(edges) if idx > start_evt and idx < stop_evt and edges[idx - 1]["scl"] == 1 and e["scl"] == 0]
    scl_rises = [e["t_ns"] for idx, e in enumerate(edges) if idx > start_evt and idx < stop_evt and edges[idx - 1]["scl"] == 0 and e["scl"] == 1]
    assert len(scl_falls_all) == n_pulses + 1, f"Expected {n_pulses + 1} SCL falls, got {len(scl_falls_all)}"
    assert len(scl_rises) == n_pulses + 1, f"Expected {n_pulses + 1} SCL rises, got {len(scl_rises)}"

    t_hd_sta_ns = scl_falls_all[0] - t_start_ns
    t_su_sto_ns = t_stop_ns - scl_rises[n_pulses]
    sda_changes = [e["t_ns"] for idx, e in enumerate(edges) if idx > 0 and e["sda"] != edges[idx - 1]["sda"]]

    pulses = []
    for k in range(n_pulses):
        t_low_start = scl_falls_all[k]
        t_rise = scl_rises[k]
        t_low_end_next = scl_falls_all[k + 1]
        low_ns = t_rise - t_low_start
        high_ns = t_low_end_next - t_rise
        period_ns = low_ns + high_ns
        freq_khz = 1_000_000.0 / period_ns
        prior_sda = [t for t in sda_changes if t <= t_rise]
        later_sda = [t for t in sda_changes if t >= t_low_end_next]
        t_su_dat_ns = t_rise - (prior_sda[-1] if prior_sda else t_start_ns)
        t_hd_dat_ns = (later_sda[0] if later_sda else t_stop_ns) - t_low_end_next
        bit_in_byte = k % 9
        role_prefix = f"b{k // 9}." if n_bytes > 1 else ""
        pulses.append(
            {
                "pulse_idx": k,
                "role": f"{role_prefix}ACK/NACK" if bit_in_byte == 8 else f"{role_prefix}bit_{7 - bit_in_byte}",
                "t_low_ns": low_ns,
                "t_high_ns": high_ns,
                "period_ns": period_ns,
                "freq_khz": round(freq_khz, 3),
                "t_su_dat_ns": t_su_dat_ns,
                "t_hd_dat_ns": t_hd_dat_ns,
            }
        )

    stop_prep_low_ns = scl_rises[n_pulses] - scl_falls_all[n_pulses]
    limits = I2C_LIMITS_NS[spec_profile]
    violations: List[str] = []
    if t_buf_pre_ns < limits["min_buf_ns"]:
        violations.append(f"t_BUF {t_buf_pre_ns}ns < {limits['min_buf_ns']}ns")
    if t_hd_sta_ns < limits["min_hd_sta_ns"]:
        violations.append(f"t_HD;STA {t_hd_sta_ns}ns < {limits['min_hd_sta_ns']}ns")
    if t_su_sto_ns < limits["min_su_sto_ns"]:
        violations.append(f"t_SU;STO {t_su_sto_ns}ns < {limits['min_su_sto_ns']}ns")
    for p in pulses:
        if p["t_low_ns"] < limits["min_low_ns"]:
            violations.append(f"{p['role']} t_LOW {p['t_low_ns']}ns < {limits['min_low_ns']}ns")
        if p["t_high_ns"] < limits["min_high_ns"]:
            violations.append(f"{p['role']} t_HIGH {p['t_high_ns']}ns < {limits['min_high_ns']}ns")
        if p["freq_khz"] > limits["max_freq_khz"]:
            violations.append(f"{p['role']} freq {p['freq_khz']}kHz > {limits['max_freq_khz']}kHz")
        if p["t_su_dat_ns"] < limits["min_su_dat_ns"]:
            violations.append(f"{p['role']} t_SU;DAT {p['t_su_dat_ns']}ns < {limits['min_su_dat_ns']}ns")
        if p["t_hd_dat_ns"] < limits["min_hd_dat_ns"]:
            violations.append(f"{p['role']} t_HD;DAT {p['t_hd_dat_ns']}ns < {limits['min_hd_dat_ns']}ns")

    steady = pulses[1:]
    rx_str = ",".join(f"0x{b:02x}" for b in expected_rx_bytes)
    return {
        "mode_name": mode_name,
        "spec_profile": spec_profile,
        "compliant": len(violations) == 0,
        "violations": violations,
        "od_fault": od_fault,
        "rx_byte": rx_str,
        "t_buf_ns": t_buf_pre_ns,
        "t_hd_sta_ns": t_hd_sta_ns,
        "stop_prep_low_ns": stop_prep_low_ns,
        "t_su_sto_ns": t_su_sto_ns,
        "min_low_ns": min(p["t_low_ns"] for p in pulses),
        "min_high_ns": min(p["t_high_ns"] for p in pulses),
        "min_su_dat_ns": min(p["t_su_dat_ns"] for p in pulses),
        "min_hd_dat_ns": min(p["t_hd_dat_ns"] for p in pulses),
        "max_freq_khz": max(p["freq_khz"] for p in pulses),
        "steady_freq_khz": steady[0]["freq_khz"],
        "pulses": pulses,
    }


def run_i2c_audit() -> Dict[str, Any]:
    configs = [
        ("i2c-400k-nominal", 62, 0, "fast_400k", 1, False),
        ("i2c-400k-nominal-nack", 62, 0, "fast_400k", 0, False),
        ("i2c-fast-compliant", 64, 0, "fast_400k", 1, False),
        ("i2c-fast-compliant-nack", 64, 0, "fast_400k", 0, False),
        ("i2c-100k", 250, 0, "standard_100k", 1, False),
        ("i2c-100k-nack", 250, 0, "standard_100k", 0, False),
        ("i2c-2byte-od-fast", 64, 62, "fast_400k", 1, True),
        ("i2c-2byte-od-fast-nack", 64, 62, "fast_400k", 0, True),
        ("i2c-2byte-od-100k", 250, 248, "standard_100k", 1, True),
        ("i2c-2byte-od-100k-nack", 250, 248, "standard_100k", 0, True),
    ]
    results: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="i2c_audit_") as tmp:
        build_dir = Path(tmp)
        vvp_path = _compile_audit_vvp(build_dir)
        for name, half_cycles, run_param, profile, ack, is_mb in configs:
            if is_mb:
                words, _ = build_i2c_write_multibyte_program([0xA0, 0x55], half_cycles=None)
                exp_bytes = (0xA0, 0x55) if ack else (0xA0,)
            else:
                words = build_i2c_write_byte_program(0xA0, half_cycles=half_cycles, ack_tag=0xAC, nack_tag=0xDE)
                exp_bytes = (0xA0,)
            lines = _run_i2c_rtl_trace(
                vvp_path,
                build_dir,
                words,
                ack,
                run_param=run_param,
                exp_bytes=2 if is_mb else 1,
                tag_name=name,
            )
            rec = analyze_i2c_trace(lines, mode_name=name, spec_profile=profile, expected_rx_bytes=exp_bytes)
            rec["half_cycles"] = half_cycles
            rec["ack"] = ack
            rec["program_words"] = len(words)
            results.append(rec)
    return {"clock_hz": 50_000_000, "clock_period_ns": 20, "runs": results}


def format_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# Phase 4 I2C Pulse-by-Pulse Timing Audit (50 MHz Core Clock)",
        "",
        "Audited real `tt_um_harjag_21_timing_explorer` RTL at `clock_hz = 50,000,000` (`T = 20 ns`) under idealized `0 ns` pull-up rise time across `START`, bits `7..0`, `ACK/NACK`, and `STOP` (`(uio_oe & uio_out) == 0` verified at every cycle).",
        "",
        "| Profile | `half_cycles` | ACK | Rx | `t_BUF` (ns) | `t_HD;STA` (ns) | Min `t_LOW` (ns) | Min `t_HIGH` (ns) | Min `t_SU;DAT` (ns) | Min `t_HD;DAT` (ns) | `t_SU;STO` (ns) | Steady Rate (kHz) | Spec Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["runs"]:
        status = "PASS" if r["compliant"] else f"FAIL ({'; '.join(r['violations'])})"
        lines.append(
            f"| `{r['mode_name']}` | {r['half_cycles']} | {r['ack']} | `{r['rx_byte']}` | {r['t_buf_ns']} | {r['t_hd_sta_ns']} | {r['min_low_ns']} | {r['min_high_ns']} | {r['min_su_dat_ns']} | {r['min_hd_dat_ns']} | {r['t_su_sto_ns']} | {r['steady_freq_khz']:.3f} | {status} |"
        )
    lines += [
        "",
        "## Per-Pulse Detail (`i2c-fast-compliant`, `half_cycles=64`, ACK)",
        "",
        "| Pulse | Role | `t_LOW` (ns) | `t_HIGH` (ns) | Period (ns) | Freq (kHz) | `t_SU;DAT` (ns) | `t_HD;DAT` (ns) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    comp = next(r for r in report["runs"] if r["mode_name"] == "i2c-fast-compliant")
    for p in comp["pulses"]:
        lines.append(
            f"| {p['pulse_idx']} | `{p['role']}` | {p['t_low_ns']} | {p['t_high_ns']} | {p['period_ns']} | {p['freq_khz']:.3f} | {p['t_su_dat_ns']} | {p['t_hd_dat_ns']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="50 MHz pulse-by-pulse I2C timing audit")
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    report = run_i2c_audit()
    md = format_markdown(report)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(md)
    # Require compliant fast-mode & 100k modes to pass all limits, and nominal 400k to be accurately flagged
    by_name = {r["mode_name"]: r for r in report["runs"]}
    assert not by_name["i2c-400k-nominal"]["compliant"]
    assert by_name["i2c-fast-compliant"]["compliant"]
    assert by_name["i2c-fast-compliant-nack"]["compliant"]
    assert by_name["i2c-100k"]["compliant"]
    assert by_name["i2c-100k-nack"]["compliant"]
    assert by_name["i2c-2byte-od-fast"]["compliant"]
    assert by_name["i2c-2byte-od-fast-nack"]["compliant"]
    assert by_name["i2c-2byte-od-100k"]["compliant"]
    assert by_name["i2c-2byte-od-100k-nack"]["compliant"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
