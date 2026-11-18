# SPDX-License-Identifier: Apache-2.0
"""Empirical timing & edge-case characterization of the SPI Mode-0 target-responder program."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import (  # noqa: E402
    UART_OP_BEGIN_LOAD,
    UART_OP_COMMIT_LOAD,
    UART_OP_POP_EVENT,
    UART_OP_READ_CYCLE_CNT,
    UART_OP_READ_STATUS,
    UART_OP_START,
    UART_OP_WRITE_WORD,
    build_spi_target_responder_program,
    make_uart_request,
    program_checksums,
)

_CHAR_TB_VERILOG = r"""`timescale 1ns/1ps
`default_nettype none

module tb_spi_resp_char;
  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  reg        ext_cs_n = 1'b1;
  reg        ext_sclk = 1'b0;
  reg        ext_mosi = 1'b0;
  wire [7:0] uio_in   = {5'b11111, ext_mosi, ext_sclk, ext_cs_n};

  always #5 clk = ~clk;

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

  reg [7:0] rx_buf [0:7];
  reg [7:0] tx_buf [0:7];
  integer k_tx, k_rx;
  reg xchg_ok, xchg_all_ok;
  task xchg8(
    input [7:0] b0, input [7:0] b1, input [7:0] b2, input [7:0] b3,
    input [7:0] b4, input [7:0] b5, input [7:0] b6, input [7:0] b7
  );
    begin
      tx_buf[0]=b0; tx_buf[1]=b1; tx_buf[2]=b2; tx_buf[3]=b3;
      tx_buf[4]=b4; tx_buf[5]=b5; tx_buf[6]=b6; tx_buf[7]=b7;
      xchg_all_ok = 1'b1;
      fork
        begin : txb
          for (k_tx = 0; k_tx < 8; k_tx = k_tx + 1) uart_send(tx_buf[k_tx]);
        end
        begin : rxb
          for (k_rx = 0; k_rx < 8; k_rx = k_rx + 1) begin
            uart_recv(rx_buf[k_rx], xchg_ok);
            if (!xchg_ok) xchg_all_ok = 1'b0;
          end
          if (xchg_all_ok) repeat (6) @(posedge clk);
        end
      join
      if (!xchg_all_ok) begin
        $display("ERR UART timeout");
        $finish;
      end
    end
  endtask

  integer fd, nwords, idx, i;
  integer a0,a1,a2,a3,a4,a5,a6,a7;
  integer case_id, cs_delay, num_bits, glitch_cycs, sclk_hi, sclk_lo, jitter_mode, rparam, ncases;
  reg [7:0] mosi_byte, ext_miso_rx;
  reg       arm_ext = 1'b0;
  integer eb, hi_eff, lo_eff, cyc_wait;
  reg [1023:0] cfg_path;

  always @(posedge uio_oe[3]) begin : drv_blk
    if (arm_ext) begin
      arm_ext = 1'b0;
      // Wait until MISO OE asserts; check glitch+shared SCLK if case_id==16
      if (glitch_cycs > 0) begin
        ext_cs_n = 1'b0;
        repeat (glitch_cycs) @(posedge clk);
        ext_cs_n = 1'b1;
        repeat (10) @(posedge clk);
      end
      for (cyc_wait = 0; (cyc_wait < cs_delay) && uio_oe[3]; cyc_wait = cyc_wait + 1) begin
        @(posedge clk);
      end
      if (uio_oe[3] && (num_bits > 0)) begin
        ext_mosi = mosi_byte[7];
        ext_cs_n = (jitter_mode == 2) ? 1'b1 : 1'b0;
        repeat (sclk_lo) @(posedge clk);
        for (eb = 0; eb < num_bits; eb = eb + 1) begin
          hi_eff = (jitter_mode == 1) ? (sclk_hi + ((eb % 2 == 0) ? 1 : -1)) : sclk_hi;
          lo_eff = (jitter_mode == 1) ? (sclk_lo + ((eb % 2 == 1) ? 1 : -1)) : sclk_lo;
          if (hi_eff < 1) hi_eff = 1;
          if (lo_eff < 1) lo_eff = 1;
          ext_sclk = 1'b1;
          ext_miso_rx = {ext_miso_rx[6:0], uio_out[3]};
          repeat (hi_eff) @(posedge clk);
          ext_sclk = 1'b0;
          if (eb < 7) ext_mosi = mosi_byte[6 - eb];
          repeat (lo_eff) @(posedge clk);
        end
        repeat (2) @(posedge clk);
        ext_cs_n = 1'b1;
      end
    end
  end

  initial begin
    rst_n = 1'b0; ena = 1'b1; ui_in = 8'h01;
    repeat (6) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    if (!$value$plusargs("CFG=%s", cfg_path)) begin
      $display("ERR no +CFG");
      $finish;
    end
    fd = $fopen(cfg_path, "r");
    if (fd == 0) begin
      $display("ERR open +CFG");
      $finish;
    end
    // Load program once
    if ($fscanf(fd, "%d", nwords) != 1) $finish;
    for (idx = 0; idx < nwords; idx = idx + 1) begin
      if ($fscanf(fd, "%x %x %x %x %x %x %x %x", a0,a1,a2,a3,a4,a5,a6,a7) != 8) $finish;
      xchg8(a0[7:0],a1[7:0],a2[7:0],a3[7:0],a4[7:0],a5[7:0],a6[7:0],a7[7:0]);
    end

    if ($fscanf(fd, "%d", ncases) != 1) $finish;
    for (idx = 0; idx < ncases; idx = idx + 1) begin
      if ($fscanf(fd, "%d %x %d %d %d %d %d %d %d",
                  case_id, mosi_byte, rparam, cs_delay, num_bits, glitch_cycs, sclk_hi, sclk_lo, jitter_mode) != 9) $finish;
      ext_cs_n = 1'b1;
      ext_sclk = 1'b0;
      ext_mosi = 1'b0;
      ext_miso_rx = 8'h00;
      arm_ext = 1'b1;

      if ($fscanf(fd, "%x %x %x %x %x %x %x %x", a0,a1,a2,a3,a4,a5,a6,a7) != 8) $finish;
      xchg8(a0[7:0],a1[7:0],a2[7:0],a3[7:0],a4[7:0],a5[7:0],a6[7:0],a7[7:0]);

      // Wait for ENG_HALTED or ENG_FAULT (max 3000 cycles)
      cyc_wait = 0;
      while ((uo_out[4:3] == 2'd1) && (cyc_wait < 3000)) begin
        @(posedge clk);
        cyc_wait = cyc_wait + 1;
      end
      arm_ext = 1'b0;

      // Read READ_STATUS, POP_EVENT, READ_CYCLE_CNT frames from cfg
      if ($fscanf(fd, "%x %x %x %x %x %x %x %x", a0,a1,a2,a3,a4,a5,a6,a7) != 8) $finish;
      xchg8(a0[7:0],a1[7:0],a2[7:0],a3[7:0],a4[7:0],a5[7:0],a6[7:0],a7[7:0]);
      // rx_buf: d0=eng_state, d2=sample_reg
      a0 = rx_buf[3]; a1 = rx_buf[5];

      if ($fscanf(fd, "%x %x %x %x %x %x %x %x", a2,a3,a4,a5,a6,a7,cyc_wait,eb) != 8) $finish;
      xchg8(a2[7:0],a3[7:0],a4[7:0],a5[7:0],a6[7:0],a7[7:0],cyc_wait[7:0],eb[7:0]);
      // rx_buf: d0=cnt, d1=tag
      a2 = rx_buf[3]; a3 = rx_buf[4];

      if ($fscanf(fd, "%x %x %x %x %x %x %x %x", a4,a5,a6,a7,cyc_wait,eb,hi_eff,lo_eff) != 8) $finish;
      xchg8(a4[7:0],a5[7:0],a6[7:0],a7[7:0],cyc_wait[7:0],eb[7:0],hi_eff[7:0],lo_eff[7:0]);
      $display("RES %0d %02x %02x %0d %0d %02x %0d",
               case_id, ext_miso_rx, a1[7:0], (a0 & 3), a2[7:0], a3[7:0], {rx_buf[4], rx_buf[3]});
    end
    $fclose(fd);
    $finish;
  end
endmodule
`default_nettype wire
"""


def run_characterization() -> Dict[str, Any]:
    words = build_spi_target_responder_program(0x5A, max_wait=0)
    crc, sum8 = program_checksums(words)
    load_frames: List[List[int]] = [make_uart_request(UART_OP_BEGIN_LOAD, 1, len(words), crc, sum8)]
    for idx, w in enumerate(words):
        load_frames.append(make_uart_request(UART_OP_WRITE_WORD, 2 + idx, idx, w & 0xFF, (w >> 8) & 0xFF))
    load_frames.append(make_uart_request(UART_OP_COMMIT_LOAD, 99))

    # (case_id, name, group, mosi_byte, rparam, cs_delay, num_bits, glitch_cycs, sclk_hi, sclk_lo, jitter_mode)
    specs = [
        (0, "half_clks=1 (too fast)", "half_period", 0xA5, 60, 2, 8, 0, 1, 1, 0),
        (1, "half_clks=2 (too fast)", "half_period", 0xA5, 60, 2, 8, 0, 2, 2, 0),
        (2, "half_clks=3 (below min=4)", "half_period", 0xA5, 60, 2, 8, 0, 3, 3, 0),
        (3, "half_clks=4 (stated min)", "half_period", 0xA5, 60, 2, 8, 0, 4, 4, 0),
        (4, "half_clks=5", "half_period", 0xA5, 60, 2, 8, 0, 5, 5, 0),
        (5, "half_clks=6", "half_period", 0xA5, 60, 2, 8, 0, 6, 6, 0),
        (6, "half_clks=8", "half_period", 0xA5, 60, 2, 8, 0, 8, 8, 0),
        (7, "half_clks=12", "half_period", 0xA5, 60, 2, 8, 0, 12, 12, 0),
        (8, "asymmetric hi=3, lo=5", "asymmetry_jitter", 0xA5, 60, 2, 8, 0, 3, 5, 0),
        (9, "asymmetric hi=4, lo=3", "asymmetry_jitter", 0xA5, 60, 2, 8, 0, 4, 3, 0),
        (10, "asymmetric hi=4, lo=5", "asymmetry_jitter", 0xA5, 60, 2, 8, 0, 4, 5, 0),
        (11, "jittered nominal hi=5+/-1, lo=5-/+1", "asymmetry_jitter", 0xA5, 60, 2, 8, 0, 5, 5, 1),
        (12, "cs_delay=55 (< timeout 60)", "timeout", 0xA5, 60, 55, 8, 0, 6, 6, 0),
        (13, "cs_delay=80 (> timeout 60)", "timeout", 0xA5, 60, 80, 8, 0, 6, 6, 0),
        (14, "shortened 4-bit transfer", "truncated", 0xA5, 60, 2, 4, 0, 6, 6, 0),
        (15, "CS_N 2-cycle glitch + short 12-cyc gap (< timeout 60)", "glitch", 0xA5, 60, 2, 8, 2, 6, 6, 0),
        (16, "CS_N 2-cycle glitch + long 70-cyc gap (> timeout 60)", "glitch", 0xA5, 60, 60, 8, 2, 6, 6, 0),
        (17, "CS_N 2-cycle glitch then shared-bus SCLK w/ CS_N=1", "glitch", 0x3C, 60, 2, 8, 2, 6, 6, 2),
    ]

    with tempfile.TemporaryDirectory(prefix="spi_resp_char_") as tmp:
        build_dir = Path(tmp)
        tb_path = build_dir / "tb_spi_resp_char.v"
        vvp_path = build_dir / "char.vvp"
        cfg_path = build_dir / "cfg.txt"
        tb_path.write_text(_CHAR_TB_VERILOG, encoding="utf-8")

        lines: List[str] = [str(len(load_frames))]
        lines.extend(" ".join(f"{b:02x}" for b in fr) for fr in load_frames)
        lines.append(str(len(specs)))
        for row in specs:
            cid, _, _, mosi_b, rparam, csd, nb, gl, hi, lo, jit = row
            lines.append(f"{cid} {mosi_b:02x} {rparam} {csd} {nb} {gl} {hi} {lo} {jit}")
            lines.append(" ".join(f"{b:02x}" for b in make_uart_request(UART_OP_START, 100 + cid, rparam)))
            lines.append(" ".join(f"{b:02x}" for b in make_uart_request(UART_OP_READ_STATUS, 200 + cid)))
            lines.append(" ".join(f"{b:02x}" for b in make_uart_request(UART_OP_POP_EVENT, 300 + cid)))
            lines.append(" ".join(f"{b:02x}" for b in make_uart_request(UART_OP_READ_CYCLE_CNT, 400 + cid)))
        cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        iverilog = shutil.which("iverilog") or "/usr/bin/iverilog"
        comp = subprocess.run(
            [
                iverilog,
                "-g2012",
                "-Wall",
                "-s",
                "tb_spi_resp_char",
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
            raise RuntimeError(f"iverilog failed:\n{comp.stderr}\n{comp.stdout}")
        vvp = shutil.which("vvp") or "/usr/bin/vvp"
        res = subprocess.run([vvp, str(vvp_path), f"+CFG={cfg_path}"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(
                f"vvp failed rc={res.returncode} comp_err={comp.stderr!r}:\n{res.stderr}\n{res.stdout}"
            )

    rows: List[Dict[str, Any]] = []
    by_cid = {r[0]: r for r in specs}
    for line in res.stdout.splitlines():
        parts = line.split()
        if not parts or parts[0] != "RES":
            continue
        cid = int(parts[1])
        ext_miso = int(parts[2], 16)
        sample_reg = int(parts[3], 16)
        eng_state = int(parts[4])
        ev_cnt = int(parts[5])
        ev_tag = int(parts[6], 16)
        cyc = int(parts[7])
        _, name, group, *_ = by_cid[cid]
        exact_xfer = (ext_miso == 0x5A) and (sample_reg == 0xA5) and (ev_tag == 0xA5) and (eng_state == 2)
        rows.append(
            {
                "case_id": cid,
                "name": name,
                "group": group,
                "ext_miso_rx": f"0x{ext_miso:02x}",
                "responder_sample_reg": f"0x{sample_reg:02x}",
                "eng_state": eng_state,
                "ev_cnt": ev_cnt,
                "ev_tag": f"0x{ev_tag:02x}",
                "cycle_cnt": cyc,
                "exact_xfer": exact_xfer,
            }
        )

    assert len(rows) == len(specs)
    return {"program_words": len(words), "rows": rows}


def format_markdown(rep: Dict[str, Any]) -> str:
    lines = [
        "# Phase 4 SPI Target-Responder Operating Limit Characterization",
        "",
        f"Measured `build_spi_target_responder_program(0x5A, max_wait=0)` (`{rep['program_words']}` words, SPI **Mode 0** only, `run_param=60`) on `tt_um_harjag_21_timing_explorer` against an external controller sending `MOSI = 0xA5`.",
        "",
        "| Case | Group | Ext MISO (`exp 0x5a`) | Resp MOSI (`exp 0xa5`) | Event Tag | Cycles | Outcome |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rep["rows"]:
        if r["exact_xfer"]:
            out = "EXACT PASS"
        elif r["ev_tag"] == "0xee":
            out = "CS TIMEOUT (0xEE)"
        elif r["case_id"] == 17:
            out = "SPURIOUS SCLK CAPTURE (CS_N=1)"
        else:
            out = "MISFRAMED / FAIL"
        lines.append(
            f"| {r['name']} | `{r['group']}` | `{r['ext_miso_rx']}` | `{r['responder_sample_reg']}` | `{r['ev_tag']}` | {r['cycle_cnt']} | {out} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Characterize SPI target responder timing limits")
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    rep = run_characterization()
    md = format_markdown(rep)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(md)
    by_id = {r["case_id"]: r for r in rep["rows"]}
    assert by_id[3]["exact_xfer"] and by_id[4]["exact_xfer"] and by_id[5]["exact_xfer"]
    assert by_id[13]["ev_tag"] == "0xee"
    assert not by_id[16]["exact_xfer"] and by_id[16]["ev_tag"] != "0xee"
    assert by_id[17]["responder_sample_reg"] == "0x3c"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
