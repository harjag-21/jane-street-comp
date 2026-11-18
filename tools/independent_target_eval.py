# SPDX-License-Identifier: Apache-2.0
"""Validate the Timing Boundary Explorer against an independent Verilog SPI target model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.timing_explorer import (  # noqa: E402
    ProtocolClient,
    ProtocolError,
    ProtocolTransport,
    SimObservations,
    replay_records,
    run_spi_boundary_exploration,
    summarize_repeated_trials,
)
from tools.protocol_asm import build_spi_mode0_program  # noqa: E402

_INDEP_TB_VERILOG = r"""`timescale 1ns/1ps
`default_nettype none

// Completely separate SPI target module with hidden internal rules:
//   profile 0: sharp threshold at `low_cycles >= 5`
//   profile 1: non-monotonic pass-fail-pass (`[2..4] U [8..13]`, fail `[5..7]`)
//   profile 2: probabilistic noisy zone (`p=0` for `<=3`, `p=0.25,0.50,0.75` for `4,5,6`, `p=1` for `>=7`)
module independent_spi_target (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [1:0] profile,
    input  wire       cs_n,
    input  wire       sclk,
    input  wire       mosi,
    output reg        miso
);
  reg        prev_cs_n = 1'b1;
  reg        prev_sclk = 1'b0;
  reg  [7:0] low_cnt   = 8'd0;
  reg  [3:0] bit_pos   = 4'd0;
  reg  [7:0] tx_byte   = 8'hAA;
  reg [31:0] lfsr      = 32'h1357_9BDF;

  function automatic reg eval_rule(input [1:0] prof, input [7:0] lc, input [7:0] rnd);
    begin
      if (prof == 2'd0) begin
        eval_rule = (lc >= 8'd5);
      end else if (prof == 2'd1) begin
        eval_rule = ((lc >= 8'd2) && (lc <= 8'd4)) || (lc >= 8'd8);
      end else begin
        if (lc <= 8'd3)      eval_rule = 1'b0;
        else if (lc == 8'd4) eval_rule = (rnd < 8'd64);
        else if (lc == 8'd5) eval_rule = (rnd < 8'd128);
        else if (lc == 8'd6) eval_rule = (rnd < 8'd192);
        else                 eval_rule = 1'b1;
      end
    end
  endfunction

  always @(posedge clk) begin
    if (!rst_n) begin
      prev_cs_n <= 1'b1;
      prev_sclk <= 1'b0;
      low_cnt   <= 8'd0;
      bit_pos   <= 4'd0;
      tx_byte   <= 8'hAA;
      miso      <= 1'b1;
      lfsr      <= 32'h1357_9BDF;
    end else begin
      prev_cs_n <= cs_n;
      prev_sclk <= sclk;

      // Advance independent target PRNG every cycle
      lfsr <= {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};

      if (cs_n) begin
        low_cnt <= 8'd0;
        bit_pos <= 4'd0;
        tx_byte <= 8'hAA;
        miso    <= 1'b1;
      end else if (prev_cs_n && !cs_n) begin
        // CS_N just fell: preload MSB=1 before bit 7 rising sample
        low_cnt <= 8'd1;
        bit_pos <= 4'd0;
        miso    <= 1'b1;
      end else if (!prev_sclk && sclk) begin
        // Rising edge: bit 7 evaluates secret rule using exact SCLK low-phase cycle count,
        // and every bit verifies the ASIC drove expected MOSI (0x55 = 8'b0101_0101).
        if (bit_pos == 4'd0) begin
          if ((mosi == 1'b0) && eval_rule(profile, low_cnt, lfsr[7:0])) begin
            tx_byte <= 8'hAA;
            miso    <= 1'b0;
          end else begin
            tx_byte <= 8'h55;
            miso    <= 1'b1;
          end
        end else if (bit_pos < 4'd7) begin
          if (mosi != ((8'h55 >> (4'd7 - bit_pos)) & 1'b1)) begin
            tx_byte <= 8'h55;
            miso    <= 1'b1;
          end else begin
            miso <= tx_byte[6 - bit_pos];
          end
        end
        bit_pos <= bit_pos + 4'd1;
        low_cnt <= 8'd0;
      end else if (!sclk) begin
        low_cnt <= low_cnt + 8'd1;
      end
    end
  end
endmodule

module independent_target_tb;
  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  reg  [1:0] profile = 2'd0;
  wire       target_miso;

  always #5 clk = ~clk;

  wire spi_cs_n = uio_oe[0] ? uio_out[0] : 1'b1;
  wire spi_sclk = uio_oe[1] ? uio_out[1] : 1'b0;
  wire spi_mosi = uio_oe[2] ? uio_out[2] : 1'b0;
  wire [7:0] uio_in = {4'hF, target_miso, 3'b111};

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

  independent_spi_target u_indep (
      .clk    (clk),
      .rst_n  (rst_n),
      .profile(profile),
      .cs_n   (spi_cs_n),
      .sclk   (spi_sclk),
      .mosi   (spi_mosi),
      .miso   (target_miso)
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

  task automatic uart_recv;
    output [7:0] b;
    output       ok;
    integer i, to;
    begin
      ok = 1'b0;
      b  = 8'h00;
      to = 0;
      while ((uo_out[0] !== 1'b0) && (to < 2048)) begin
        @(posedge clk);
        to = to + 1;
      end
      if (uo_out[0] === 1'b0) begin
        ok = 1'b1;
        repeat (12) @(posedge clk);
        for (i = 0; i < 8; i = i + 1) begin
          b[i] = uo_out[0];
          repeat (8) @(posedge clk);
        end
      end
    end
  endtask

  integer ch, a, i;
  reg [7:0] tx_buf [0:7];
  reg [7:0] rx_buf [0:7];
  reg       ok, all_ok;

  initial begin
    rst_n = 1'b0;
    ena   = 1'b1;
    ui_in = 8'h01;
    repeat (6) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    ch = $fgetc(32'h8000_0000);
    while ((ch != -1) && (ch != "q")) begin
      if (ch == "r") begin
        if ($fscanf(32'h8000_0000, "%d", a) == 1) begin
          ena = a[0];
          rst_n = 1'b0;
          ui_in = 8'h01;
          repeat (6) @(posedge clk);
          rst_n = 1'b1;
          repeat (4) @(posedge clk);
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (ch == "p") begin
        if ($fscanf(32'h8000_0000, "%d", a) == 1) begin
          profile = a[1:0];
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (ch == "f") begin
        for (i = 0; i < 8; i = i + 1) begin
          if ($fscanf(32'h8000_0000, "%x", a) == 1) tx_buf[i] = a[7:0];
          else tx_buf[i] = 8'h00;
        end
        all_ok = 1'b1;
        fork
          begin : tx_b
            integer k;
            for (k = 0; k < 8; k = k + 1) uart_send(tx_buf[k]);
          end
          begin : rx_b
            integer k;
            for (k = 0; k < 8; k = k + 1) begin
              uart_recv(rx_buf[k], ok);
              if (!ok) all_ok = 1'b0;
            end
            if (all_ok) repeat (6) @(posedge clk);
          end
        join
        if (all_ok) begin
          $display("RESP %02x %02x %02x %02x %02x %02x %02x %02x",
                   rx_buf[0], rx_buf[1], rx_buf[2], rx_buf[3],
                   rx_buf[4], rx_buf[5], rx_buf[6], rx_buf[7]);
        end else begin
          $display("TIMEOUT");
        end
        $fflush(32'h8000_0001);
      end else if (ch == "w") begin
        if ($fscanf(32'h8000_0000, "%d", a) == 1) begin
          i = 0;
          while ((i < a) && (uo_out[4:3] != 2'd2) && (uo_out[4:3] != 2'd3)) begin
            @(posedge clk);
            i = i + 1;
          end
          @(posedge clk);
          $display("STAT %02x %0d", uo_out, i);
          $fflush(32'h8000_0001);
        end
      end else if (ch == "x") begin
        if ($fscanf(32'h8000_0000, "%d", a) == 1) begin
          i = 0;
          while ((i < a) && (uo_out[7] != 1'b1)) begin
            @(posedge clk);
            i = i + 1;
          end
          @(posedge clk);
          $display("STAT %02x %0d", uo_out, i);
          $fflush(32'h8000_0001);
        end
      end
      ch = $fgetc(32'h8000_0000);
    end
    $finish;
  end
endmodule
`default_nettype wire
"""


class IndependentTargetTransport(ProtocolTransport):
    """Transport running the ASIC against the completely separate `independent_spi_target` bench."""

    evidence_class = "rtl_independent_target_simulation"

    def __init__(self) -> None:
        self._tmpdir = Path(tempfile.mkdtemp(prefix="indep_tgt_"))
        tb_path = self._tmpdir / "independent_target_tb.v"
        vvp_path = self._tmpdir / "indep.vvp"
        tb_path.write_text(_INDEP_TB_VERILOG, encoding="utf-8")
        iverilog = shutil.which("iverilog") or "/usr/bin/iverilog"
        comp = subprocess.run(
            [
                iverilog,
                "-g2012",
                "-Wall",
                "-s",
                "independent_target_tb",
                "-o",
                str(vvp_path),
                str(ROOT_DIR / "src" / "project.v"),
                str(ROOT_DIR / "src" / "protocol_sequencer.v"),
                str(tb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if comp.returncode != 0:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            raise RuntimeError(f"Failed to compile independent_target_tb:\n{comp.stderr}")
        vvp = shutil.which("vvp") or "/usr/bin/vvp"
        self._proc = subprocess.Popen(
            [vvp, str(vvp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def _cmd(self, line: str) -> str:
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()
        out = self._proc.stdout.readline().strip()
        if not out:
            raise ProtocolError(f"Independent target closed unexpectedly on command {line!r}")
        return out

    def set_profile(self, profile: int) -> None:
        if self._cmd(f"p {profile}") != "OK":
            raise ProtocolError("Failed to set profile")

    def reset(self, ena: int = 1) -> None:
        if self._cmd(f"r {ena & 1}") != "OK":
            raise ProtocolError("Reset failed")

    def exchange(self, frame: Sequence[int]) -> List[int]:
        out = self._cmd("f " + " ".join(f"{b & 0xFF:02x}" for b in frame))
        if not out.startswith("RESP "):
            raise ProtocolError(f"UART exchange failed: {out}")
        return [int(tok, 16) for tok in out.split()[1:]]

    def wait_execution(self, max_cycles: int = 4000) -> Optional[SimObservations]:
        self._cmd(f"w {max_cycles}")
        return None

    def wait_scan_completion(self, max_cycles: int = 200000) -> Optional[Tuple[int, int]]:
        parts = self._cmd(f"x {max_cycles}").split()
        return int(parts[1], 16), int(parts[2])

    def close(self) -> None:
        try:
            if self._proc.poll() is None and self._proc.stdin is not None:
                self._proc.stdin.write("q\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
        finally:
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is not None:
                    stream.close()
            shutil.rmtree(self._tmpdir, ignore_errors=True)


def run_independent_validation() -> Dict[str, Any]:
    with IndependentTargetTransport() as transport:
        transport.reset(1)
        client = ProtocolClient(transport)

        # 1) Sharp threshold (ground truth: low_cycles >= 5)
        transport.set_profile(0)
        sharp_sweep = replay_records(
            run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=13, method="sweep", delay_ns=None, clock_ns=None
            )
        )
        sharp_bin = replay_records(
            run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=13, method="binary", delay_ns=None, clock_ns=None
            )
        )
        client.load_program(build_spi_mode0_program(0x55))
        sharp_scan = client.run_autonomous_scan(param_start=0, param_step=1, num_steps=12, expected_val=0xAA, pass_mode=0)
        sharp_truth = 5

        # 2) Non-monotonic pass-fail-pass (ground truth: [2..4] U [8..13], fail notch [5..7])
        transport.set_profile(1)
        nm_sweep = replay_records(
            run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=13, method="sweep", delay_ns=None, clock_ns=None
            )
        )
        nm_bin = replay_records(
            run_spi_boundary_exploration(
                client, min_low_cycles=2, max_low_cycles=13, method="binary", delay_ns=None, clock_ns=None
            )
        )
        client.load_program(build_spi_mode0_program(0x55))
        nm_scan = client.run_autonomous_scan(param_start=0, param_step=1, num_steps=12, expected_val=0xAA, pass_mode=0)
        nm_truth_pass = [[2, 4], [8, 13]]
        nm_truth_fail = [[5, 7]]

        # 3) Probabilistic noisy transition (K=20 repeats/point across low_cycles 2..9)
        transport.set_profile(2)
        client.load_program(build_spi_mode0_program(0x55))
        outcomes: Dict[int, List[bool]] = {}
        repeats = 20
        for low_c in range(2, 10):
            outcomes[low_c] = []
            for _ in range(repeats):
                tr = client.run_trial(low_c - 2)
                outcomes[low_c].append(tr["sample_reg"] == 0xAA and tr["eng_state"] == "ENG_HALTED")
        noisy_summary = summarize_repeated_trials(outcomes)

    assert sharp_sweep["boundary"]["status"] == "bracketed", sharp_sweep["boundary"]
    assert abs(sharp_sweep["boundary"]["boundary_low_cycles"] - sharp_truth) == 0, sharp_sweep["boundary"]
    assert abs(sharp_bin["boundary"]["boundary_low_cycles"] - sharp_truth) == 0, sharp_bin["boundary"]
    assert [pt["passed"] for pt in sharp_scan["results"]] == [x >= sharp_truth for x in range(2, 14)], sharp_scan

    assert nm_sweep["boundary"]["status"] == "non_monotone", nm_sweep["boundary"]
    assert nm_sweep["boundary"]["pass_regions"] == nm_truth_pass, nm_sweep["boundary"]
    assert nm_sweep["boundary"]["fail_regions"] == nm_truth_fail, nm_sweep["boundary"]
    assert [pt["passed"] for pt in nm_scan["results"]] == [((2 <= x <= 4) or (x >= 8)) for x in range(2, 14)], nm_scan

    assert noisy_summary["status"] == "probabilistic_transition"
    assert noisy_summary["boundary_low_cycles"] is None
    assert noisy_summary["uncertain_params"] == [4, 5, 6], noisy_summary
    true_p = {2: 0.0, 3: 0.0, 4: 0.25, 5: 0.50, 6: 0.75, 7: 1.0, 8: 1.0, 9: 1.0}
    for row in noisy_summary["per_param"]:
        tp = true_p[row["param"]]
        assert row["wilson_lo"] <= tp <= row["wilson_hi"], f"Analytic p={tp} outside CI {row}"

    return {
        "sharp": {
            "truth_boundary": sharp_truth,
            "sweep_boundary": sharp_sweep["boundary"]["boundary_low_cycles"],
            "binary_boundary": sharp_bin["boundary"]["boundary_low_cycles"],
            "error_cycles": abs(sharp_sweep["boundary"]["boundary_low_cycles"] - sharp_truth),
            "scan_match": True,
        },
        "non_monotonic": {
            "truth_pass_regions": nm_truth_pass,
            "truth_fail_regions": nm_truth_fail,
            "sweep_status": nm_sweep["boundary"]["status"],
            "sweep_pass_regions": nm_sweep["boundary"]["pass_regions"],
            "sweep_fail_regions": nm_sweep["boundary"]["fail_regions"],
            "binary_status_note": nm_bin["boundary"],
            "scan_match": True,
        },
        "probabilistic": noisy_summary,
    }


def format_markdown(rep: Dict[str, Any]) -> str:
    s = rep["sharp"]
    nm = rep["non_monotonic"]
    pr = rep["probabilistic"]
    lines = [
        "# Phase 4 Independent Target Validation Report",
        "",
        "Validated the Timing Boundary Explorer (`ProtocolClient`, `run_spi_boundary_exploration`, `run_autonomous_scan`, `classify_boundary`, `summarize_repeated_trials`) against a separately written SPI target module (`independent_spi_target`) with hidden ground-truth rules.",
        "",
        "| Case | Ground Truth | Explorer Result | Pass Gate |",
        "|---|---|---|---|",
        f"| Sharp threshold | `low_cycles >= {s['truth_boundary']}` | `sweep={s['sweep_boundary']}`, `binary={s['binary_boundary']}`, error=`{s['error_cycles']}` cycles, scan match=`{s['scan_match']}` | PASS (`0 <= 1` cycle error) |",
        f"| Non-monotonic (`pass-fail-pass`) | Pass `{nm['truth_pass_regions']}`, Fail `{nm['truth_fail_regions']}` | `status={nm['sweep_status']}`, pass=`{nm['sweep_pass_regions']}`, fail=`{nm['sweep_fail_regions']}`, scan match=`{nm['scan_match']}` *(note: 1-D binary search missed `{nm['truth_fail_regions']}` and reported `{nm['binary_status_note']['status']}`)* | PASS (both pass regions preserved) |",
        f"| Probabilistic noisy (`K=20`) | Certain fail `<=3`, stochastic `[4..6]`, certain pass `>=7` | `status={pr['status']}`, `boundary={pr['boundary_low_cycles']}`, `uncertain_params={pr['uncertain_params']}` | PASS (no deterministic boundary claimed) |",
        "",
        "## Probabilistic Zone Measurements (`K=20` per point)",
        "",
        "| `low_cycles` | Passes / Trials | Pass Rate | Wilson 95% CI | True Analytic `p` |",
        "|---|---|---|---|---|",
    ]
    true_p = {2: 0.0, 3: 0.0, 4: 0.25, 5: 0.50, 6: 0.75, 7: 1.0, 8: 1.0, 9: 1.0}
    for row in pr["per_param"]:
        tp = true_p[row["param"]]
        lines.append(
            f"| {row['param']} | {row['passes']}/{row['trials']} | {row['pass_rate']:.2f} | [{row['wilson_lo']:.3f}, {row['wilson_hi']:.3f}] | {tp:.2f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate explorer against an independent Verilog SPI target")
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    rep = run_independent_validation()
    md = format_markdown(rep)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
