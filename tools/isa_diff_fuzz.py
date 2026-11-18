# SPDX-License-Identifier: Apache-2.0
"""Randomized cycle-accurate differential tester for protocol_sequencer against an independent Python ISA model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.protocol_asm import PROG_DEPTH, insn  # noqa: E402

ENG_IDLE = 0
ENG_RUN = 1
ENG_HALTED = 2
ENG_FAULT = 3
EVENT_DEPTH = 8

_DIFF_TB_VERILOG = r"""`timescale 1ns/1ps
`default_nettype none

module tb_diff_sequencer;
  reg         clk = 1'b0;
  reg         rst_n = 1'b0;
  reg         prog_we = 1'b0;
  reg  [5:0]  prog_addr = 6'd0;
  reg  [15:0] prog_wdata = 16'd0;
  reg         prog_valid = 1'b1;
  reg         cmd_start = 1'b0;
  reg  [11:0] cmd_param = 12'd0;
  reg         cmd_halt = 1'b0;
  reg         event_pop = 1'b0;
  wire [3:0]  event_count_o;
  wire        event_overflow_o;
  wire [7:0]  event_head_tag_o;
  wire [15:0] event_head_ts_o;
  wire [1:0]  eng_state_o;
  wire [5:0]  pc_o;
  wire        last_sample_o;
  wire [7:0]  sample_reg_o;
  wire [15:0] cycle_cnt_o;
  wire [7:0]  last_mark_tag_o;
  reg  [7:0]  uio_in = 8'h00;
  wire [7:0]  uio_out_o;
  wire [7:0]  uio_oe_o;
  wire        trial_active_o;

  always #5 clk = ~clk;

  protocol_sequencer dut (
      .clk             (clk),
      .rst_n           (rst_n),
      .prog_we         (prog_we),
      .prog_addr       (prog_addr),
      .prog_wdata      (prog_wdata),
      .prog_valid      (prog_valid),
      .cmd_start       (cmd_start),
      .cmd_param       (cmd_param),
      .cmd_halt        (cmd_halt),
      .event_pop       (event_pop),
      .event_count_o   (event_count_o),
      .event_overflow_o(event_overflow_o),
      .event_head_tag_o(event_head_tag_o),
      .event_head_ts_o (event_head_ts_o),
      .eng_state_o     (eng_state_o),
      .pc_o            (pc_o),
      .last_sample_o   (last_sample_o),
      .sample_reg_o    (sample_reg_o),
      .cycle_cnt_o     (cycle_cnt_o),
      .last_mark_tag_o (last_mark_tag_o),
      .uio_in          (uio_in),
      .uio_out_o       (uio_out_o),
      .uio_oe_o        (uio_oe_o),
      .trial_active_o  (trial_active_o)
  );

  task automatic emit_state(input integer seed_id, input integer step_id);
    begin
      $display("S %0d %0d %0d %0d %02x %02x %0d %02x %04x %02x %0d %0d %02x %04x",
               seed_id, step_id, eng_state_o, pc_o, uio_out_o, uio_oe_o,
               last_sample_o, sample_reg_o, cycle_cnt_o, last_mark_tag_o,
               event_count_o, event_overflow_o, event_head_tag_o, event_head_ts_o);
    end
  endtask

  reg [1023:0] stim_path;
  integer fd, rc, seed_id, nwords, rparam, max_steps;
  integer i, w, pin_v, do_halt, pop_i;
  initial begin
    if (!$value$plusargs("STIM=%s", stim_path)) begin
      $display("ERR missing +STIM");
      $finish;
    end
    fd = $fopen(stim_path, "r");
    if (fd == 0) begin
      $display("ERR cannot open +STIM");
      $finish;
    end
    while ($fscanf(fd, "%d %d %d %d", seed_id, nwords, rparam, max_steps) == 4) begin
      rst_n = 1'b0;
      prog_we = 1'b0;
      cmd_start = 1'b0;
      cmd_halt = 1'b0;
      event_pop = 1'b0;
      uio_in = 8'h00;
      @(posedge clk);
      #1;
      rst_n = 1'b1;

      for (i = 0; i < nwords; i = i + 1) begin
        rc = $fscanf(fd, "%x", w);
        prog_we = 1'b1;
        prog_addr = i[5:0];
        prog_wdata = w[15:0];
        @(posedge clk);
        #1;
      end
      prog_we = 1'b0;

      // Start pulse
      cmd_start = 1'b1;
      cmd_param = rparam[11:0];
      @(posedge clk);
      #1;
      cmd_start = 1'b0;
      emit_state(seed_id, 0);

      for (i = 1; i <= max_steps; i = i + 1) begin
        rc = $fscanf(fd, "%x %d", pin_v, do_halt);
        if ((eng_state_o == 2'd1)) begin
          uio_in = pin_v[7:0];
          cmd_halt = do_halt[0];
          @(posedge clk);
          #1;
          cmd_halt = 1'b0;
          emit_state(seed_id, i);
        end
      end

      // Drain remaining FIFO entries via event_pop
      for (pop_i = 0; pop_i < 8; pop_i = pop_i + 1) begin
        if (event_count_o != 4'd0) begin
          $display("E %0d %0d %02x %04x", seed_id, pop_i, event_head_tag_o, event_head_ts_o);
          event_pop = 1'b1;
          @(posedge clk);
          #1;
          event_pop = 1'b0;
        end
      end
      $display("END %0d", seed_id);
    end
    $fclose(fd);
    $finish;
  end
endmodule
`default_nettype wire
"""


class SequencerRefModel:
    """Independent ISA reference model built directly from docs/PROGRAMMABLE_ENGINE_ARCHITECTURE.md."""

    def __init__(self) -> None:
        self.imem: List[int] = [0] * PROG_DEPTH
        self.eng_state: int = ENG_IDLE
        self.pc: int = 0
        self.last_sample: int = 0
        self.sample_reg: int = 0
        self.cycle_cnt: int = 0
        self.last_mark_tag: int = 0
        self.uio_out: int = 0
        self.uio_oe: int = 0
        self.wait_cnt: int = 0
        self.wait_edge_active: bool = False
        self.wait_edge_pol: int = 0
        self.wait_edge_pin: int = 0
        self.run_param: int = 0
        self.loop_cnt0: int = 0
        self.loop_cnt1: int = 0
        self.ev_fifo: List[Tuple[int, int]] = []
        self.event_overflow: int = 0

    def load(self, words: Sequence[int]) -> None:
        assert len(words) <= PROG_DEPTH
        self.imem = list(words) + [0] * (PROG_DEPTH - len(words))

    def observable(self) -> Tuple[int, int, int, int, int, int, int, int, int, int, int, int]:
        head_tag, head_ts = self.ev_fifo[0] if self.ev_fifo else (0, 0)
        return (
            self.eng_state,
            self.pc,
            self.uio_out,
            self.uio_oe,
            self.last_sample,
            self.sample_reg,
            self.cycle_cnt,
            self.last_mark_tag,
            len(self.ev_fifo),
            self.event_overflow,
            head_tag,
            head_ts,
        )

    def start(self, run_param: int) -> None:
        assert self.eng_state != ENG_RUN
        self.eng_state = ENG_RUN
        self.pc = 0
        self.last_sample = 0
        self.sample_reg = 0
        self.cycle_cnt = 0
        self.last_mark_tag = 0
        self.wait_cnt = 0
        self.wait_edge_active = False
        self.run_param = run_param & 0xFFF
        self.loop_cnt0 = 0
        self.loop_cnt1 = 0
        self.ev_fifo = []
        self.event_overflow = 0

    def pop_event(self) -> Optional[Tuple[int, int]]:
        if not self.ev_fifo:
            return None
        return self.ev_fifo.pop(0)

    def step(self, uio_in: int, cmd_halt: bool = False) -> None:
        if cmd_halt:
            self.eng_state = ENG_HALTED
            self.wait_cnt = 0
            self.wait_edge_active = False
            self.uio_oe = 0
            return
        if self.eng_state != ENG_RUN:
            return

        pre_cycle = self.cycle_cnt
        self.cycle_cnt = (self.cycle_cnt + 1) & 0xFFFF

        if self.wait_cnt != 0:
            if self.wait_edge_active:
                pin_val = (uio_in >> self.wait_edge_pin) & 1
                if pin_val == self.wait_edge_pol:
                    self.wait_edge_active = False
                    self.wait_cnt = 0
                    self.last_sample = 1
                    self.pc = (self.pc + 1) & (PROG_DEPTH - 1)
                elif self.wait_cnt == 1:
                    self.wait_edge_active = False
                    self.wait_cnt = 0
                    self.last_sample = 0
                    self.pc = (self.pc + 1) & (PROG_DEPTH - 1)
                else:
                    self.wait_cnt -= 1
            else:
                self.wait_cnt -= 1
                if self.wait_cnt == 0:
                    self.pc = (self.pc + 1) & (PROG_DEPTH - 1)
            return

        w = self.imem[self.pc]
        op = (w >> 12) & 0xF
        imm = w & 0xFFF
        next_pc = (self.pc + 1) & (PROG_DEPTH - 1)

        if op == 0x0:  # NOP
            self.pc = next_pc
        elif op == 0x1:  # OUT
            self.uio_out = imm & 0xFF
            self.pc = next_pc
        elif op == 0x2:  # OE
            self.uio_oe = imm & 0xFF
            self.pc = next_pc
        elif op == 0x3:  # WAIT
            if imm == 0:
                self.pc = next_pc
            else:
                self.wait_cnt = imm
        elif op == 0x4:  # SAMPLE
            bit = (uio_in >> (imm & 0x7)) & 1
            self.last_sample = bit
            if not ((imm >> 11) & 1):
                self.sample_reg = ((self.sample_reg << 1) | bit) & 0xFF
            self.pc = next_pc
        elif op == 0x5:  # JZ
            self.pc = (imm & (PROG_DEPTH - 1)) if self.last_sample == 0 else next_pc
        elif op == 0x6:  # JMP
            self.pc = imm & (PROG_DEPTH - 1)
        elif op == 0x7:  # LOOP
            sel = (imm >> 8) & 1
            cur = self.loop_cnt1 if sel else self.loop_cnt0
            if cur > 1:
                if sel:
                    self.loop_cnt1 = cur - 1
                else:
                    self.loop_cnt0 = cur - 1
                self.pc = imm & (PROG_DEPTH - 1)
            else:
                if sel:
                    self.loop_cnt1 = 0
                else:
                    self.loop_cnt0 = 0
                self.pc = next_pc
        elif op == 0x8:  # SETCOUNT
            sel = (imm >> 8) & 1
            val = imm & 0xFF
            if sel:
                self.loop_cnt1 = val
            else:
                self.loop_cnt0 = val
            self.pc = next_pc
        elif op == 0x9:  # MARK
            tag = self.sample_reg if ((imm >> 8) & 1) else (imm & 0xFF)
            self.last_mark_tag = tag
            if len(self.ev_fifo) < EVENT_DEPTH:
                self.ev_fifo.append((tag, pre_cycle))
            else:
                self.event_overflow = 1
            self.pc = next_pc
        elif op == 0xA:  # HALT
            self.eng_state = ENG_HALTED
            if not (imm & 1):
                self.uio_oe = 0
        elif op == 0xB:  # OUT_SAMPLE
            self.uio_out = imm & 0xFF
            bit = (uio_in >> ((imm >> 8) & 0x7)) & 1
            self.last_sample = bit
            if not ((imm >> 11) & 1):
                self.sample_reg = ((self.sample_reg << 1) | bit) & 0xFF
            self.pc = next_pc
        elif op == 0xC:  # WAIT_PARAM
            extra = min(0xFFF, self.run_param + (imm & 0xFF))
            if extra == 0:
                self.pc = next_pc
            else:
                self.wait_cnt = extra
        elif op == 0xD:  # WAIT_EDGE
            pol = (imm >> 11) & 1
            pin = (imm >> 8) & 7
            lim = self.run_param if (imm & 0xFF) == 0 else (imm & 0xFF)
            if ((uio_in >> pin) & 1) == pol:
                self.last_sample = 1
                self.pc = next_pc
            elif lim == 0:
                self.last_sample = 0
                self.pc = next_pc
            else:
                self.wait_edge_active = True
                self.wait_edge_pol = pol
                self.wait_edge_pin = pin
                self.wait_cnt = lim
        else:  # 0xE / 0xF illegal -> ENG_FAULT + safe Hi-Z
            self.eng_state = ENG_FAULT
            self.uio_oe = 0


def generate_test_case(seed: int) -> Tuple[List[int], int, List[Tuple[int, int]]]:
    """Generate a guaranteed-terminating program exercising directed boundary corners + randomized mixes."""
    rng = random.Random(20261118 + seed)
    words: List[int] = []
    # Directed corner seeds 0..5 ensure every edge case is guaranteed covered, plus 6..119 random stress
    if seed == 0:
        # WAIT(0), WAIT(4095) max wait, WAIT_PARAM saturation (4090+10->4095), HALT keep_oe=1
        words = [
            insn("OE", 0xA5),
            insn("OUT", 0x5A),
            insn("WAIT", 0),
            insn("WAIT", 4095),
            insn("WAIT_PARAM", 10),
            insn("MARK", 0x11),
            insn("HALT", 1),
        ]
        run_param = 4090
    elif seed == 1:
        # FIFO overflow (>8 marks) + MARK_SAMPLE + illegal opcode 0xE fault clearing uio_oe
        words = [insn("OE", 0xFF), insn("SAMPLE", 3)] + [insn("MARK", 0x30 + k) for k in range(10)] + [
            insn("MARK", 0x100),
            (0xE << 12) | 0x123,
        ]
        run_param = 0
    elif seed == 2:
        # Illegal opcode 0xF fault clearing uio_oe
        words = [insn("OE", 0x3C), insn("OUT", 0xC3), insn("NOP", 0), (0xF << 12) | 0x456]
        run_param = 0
    elif seed == 3:
        # Nested loops + SETCOUNT=0 and SETCOUNT=1 edge cases + HALT keep_oe=0
        words = [
            insn("SETCOUNT", (0 << 8) | 0),  # cnt=0
            insn("NOP", 0),
            insn("LOOP", (0 << 8) | 1),
            insn("SETCOUNT", (1 << 8) | 1),  # cnt=1
            insn("NOP", 0),
            insn("LOOP", (1 << 8) | 4),
            insn("SETCOUNT", (1 << 8) | 3),
            insn("SETCOUNT", (0 << 8) | 2),
            insn("OUT_SAMPLE", (0 << 11) | (2 << 8) | 0x7E),
            insn("LOOP", (0 << 8) | 8),
            insn("LOOP", (1 << 8) | 7),
            insn("MARK", 0x100),
            insn("HALT", 0),
        ]
        run_param = 3
    elif seed == 4:
        # WAIT_EDGE run_param=0 timeout + nonzero max_wait timeout & hit + JZ taken/untaken + JMP
        words = [
            insn("WAIT_EDGE", (1 << 11) | (0 << 8) | 0),  # lim = run_param = 0
            insn("JZ", 3),
            insn("MARK", 0xA1),
            insn("WAIT_EDGE", (1 << 11) | (1 << 8) | 5),
            insn("JZ", 6),
            insn("MARK", 0xB2),
            insn("JMP", 8),
            insn("MARK", 0xC3),
            insn("HALT", 0),
        ]
        run_param = 0
    else:
        run_param = rng.choice([0, 1, 3, 7, 15, 250, 4092])
        # Build 48..62 words with forward JZ/JMP and optional bounded SETCOUNT/LOOP
        idx = 0
        # Reserve optional loop wrapper if seed is even
        use_loop = (seed % 2 == 0)
        if use_loop:
            cnt = rng.choice([1, 2, 3])
            ctr = rng.choice([0, 1])
            words.append(insn("OE", rng.randint(0, 255)))
            words.append(insn("SETCOUNT", (ctr << 8) | cnt))
            loop_hdr = len(words)
            for _ in range(rng.randint(4, 7)):
                op = rng.choice([0x0, 0x1, 0x2, 0x3, 0x4, 0x9, 0xB, 0xC, 0xD])
                if op == 0x0:
                    words.append(insn("NOP", 0))
                elif op in (0x1, 0x2):
                    words.append(insn("OUT" if op == 0x1 else "OE", rng.randint(0, 255)))
                elif op == 0x3:
                    words.append(insn("WAIT", rng.choice([0, 1, 2, 3, 6])))
                elif op == 0x4:
                    words.append(insn("SAMPLE", (rng.randint(0, 1) << 11) | rng.randint(0, 7)))
                elif op == 0x9:
                    words.append(insn("MARK", (rng.randint(0, 1) << 8) | rng.randint(0, 255)))
                elif op == 0xB:
                    words.append(insn("OUT_SAMPLE", (rng.randint(0, 1) << 11) | (rng.randint(0, 7) << 8) | rng.randint(0, 255)))
                elif op == 0xC:
                    words.append(insn("WAIT_PARAM", rng.randint(0, 10)))
                else:
                    words.append(insn("WAIT_EDGE", (rng.randint(0, 1) << 11) | (rng.randint(0, 7) << 8) | rng.choice([0, 1, 4, 8])))
            words.append(insn("LOOP", (ctr << 8) | loop_hdr))

        # Fill sequential body with forward-only JZ/JMP
        target_len = rng.randint(25, 52)
        while len(words) < target_len - 1:
            op = rng.choice([0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x9, 0xB, 0xC, 0xD])
            cur_pc = len(words)
            if op in (0x5, 0x6) and cur_pc + 4 < target_len:
                tgt = min(target_len - 1, cur_pc + rng.randint(2, 4))
                words.append(insn("JZ" if op == 0x5 else "JMP", tgt))
            elif op == 0x0:
                words.append(insn("NOP", 0))
            elif op in (0x1, 0x2):
                words.append(insn("OUT" if op == 0x1 else "OE", rng.randint(0, 255)))
            elif op == 0x3:
                words.append(insn("WAIT", rng.choice([0, 1, 2, 4, 9])))
            elif op == 0x4:
                words.append(insn("SAMPLE", (rng.randint(0, 1) << 11) | rng.randint(0, 7)))
            elif op == 0x9:
                words.append(insn("MARK", (rng.randint(0, 1) << 8) | rng.randint(0, 255)))
            elif op == 0xB:
                words.append(insn("OUT_SAMPLE", (rng.randint(0, 1) << 11) | (rng.randint(0, 7) << 8) | rng.randint(0, 255)))
            elif op == 0xC:
                words.append(insn("WAIT_PARAM", rng.choice([0, 1, 8, 255])))
            else:
                words.append(insn("WAIT_EDGE", (rng.randint(0, 1) << 11) | (rng.randint(0, 7) << 8) | rng.choice([0, 1, 3, 6])))

        if seed % 11 == 0:
            words.append((rng.choice([0xE, 0xF]) << 12) | rng.randint(0, 0xFFF))
        else:
            words.append(insn("HALT", rng.randint(0, 1)))

    assert len(words) <= PROG_DEPTH

    # Pre-simulate to determine exact step count and generate matching random pin schedule + optional cmd_halt test
    max_steps = 30000
    schedule: List[Tuple[int, int]] = []
    inject_halt_at: Optional[int] = 12 if seed == 7 else None
    for t in range(max_steps):
        pin_v = rng.randint(0, 255)
        do_h = 1 if (inject_halt_at is not None and t == inject_halt_at) else 0
        schedule.append((pin_v, do_h))
    return words, run_param, schedule


def run_differential_suite(num_seeds: int = 120) -> Dict[str, Any]:
    cases: List[Tuple[int, List[int], int, List[Tuple[int, int]], List[Tuple[int, ...]], List[Tuple[int, int]]]] = []
    coverage: Dict[str, Any] = {
        "opcodes_executed": set(),
        "jz_taken": 0,
        "jz_not_taken": 0,
        "loop_taken": 0,
        "loop_fallthrough_cnt0": 0,
        "loop_fallthrough_cnt1": 0,
        "sample_shift": 0,
        "sample_no_shift": 0,
        "out_sample_shift": 0,
        "out_sample_no_shift": 0,
        "mark_imm": 0,
        "mark_sample": 0,
        "wait_zero": 0,
        "wait_max_4095": 0,
        "wait_param_zero": 0,
        "wait_param_saturated": 0,
        "wait_edge_immediate_hit": 0,
        "wait_edge_delayed_hit": 0,
        "wait_edge_timeout": 0,
        "mark_overflow": 0,
        "halt_keep_oe_0": 0,
        "halt_keep_oe_1": 0,
        "illegal_fault_hiz": 0,
        "cmd_halt_abort": 0,
    }

    with tempfile.TemporaryDirectory(prefix="isa_diff_") as tmp:
        build_dir = Path(tmp)
        tb_path = build_dir / "tb_diff_sequencer.v"
        vvp_path = build_dir / "diff.vvp"
        stim_path = build_dir / "stim.txt"
        tb_path.write_text(_DIFF_TB_VERILOG, encoding="utf-8")

        iverilog = shutil.which("iverilog") or "/usr/bin/iverilog"
        comp = subprocess.run(
            [
                iverilog,
                "-g2012",
                "-Wall",
                "-s",
                "tb_diff_sequencer",
                "-o",
                str(vvp_path),
                str(ROOT_DIR / "src" / "protocol_sequencer.v"),
                str(tb_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if comp.returncode != 0:
            raise RuntimeError(f"iverilog failed:\n{comp.stderr}\n{comp.stdout}")

        stim_lines: List[str] = []
        total_cycles = 0
        for seed in range(num_seeds):
            words, run_param, full_sched = generate_test_case(seed)
            ref = SequencerRefModel()
            ref.load(words)
            ref.start(run_param)
            ref_states = [ref.observable()]
            used_sched: List[Tuple[int, int]] = []
            for step_idx, (pin_v, do_h) in enumerate(full_sched):
                if ref.eng_state != ENG_RUN:
                    break
                # Record instruction/branch coverage right before step
                if ref.wait_cnt == 0 and not do_h:
                    w = ref.imem[ref.pc]
                    op = (w >> 12) & 0xF
                    imm = w & 0xFFF
                    coverage["opcodes_executed"].add(op)
                    if op == 0x3 and imm == 0:
                        coverage["wait_zero"] += 1
                    elif op == 0x3 and imm == 4095:
                        coverage["wait_max_4095"] += 1
                    elif op == 0x4:
                        if (imm >> 11) & 1:
                            coverage["sample_no_shift"] += 1
                        else:
                            coverage["sample_shift"] += 1
                    elif op == 0x5:
                        if ref.last_sample == 0:
                            coverage["jz_taken"] += 1
                        else:
                            coverage["jz_not_taken"] += 1
                    elif op == 0x7:
                        sel = (imm >> 8) & 1
                        cur = ref.loop_cnt1 if sel else ref.loop_cnt0
                        if cur > 1:
                            coverage["loop_taken"] += 1
                        elif cur == 1:
                            coverage["loop_fallthrough_cnt1"] += 1
                        else:
                            coverage["loop_fallthrough_cnt0"] += 1
                    elif op == 0x9:
                        if (imm >> 8) & 1:
                            coverage["mark_sample"] += 1
                        else:
                            coverage["mark_imm"] += 1
                    elif op == 0xA:
                        if imm & 1:
                            coverage["halt_keep_oe_1"] += 1
                        else:
                            coverage["halt_keep_oe_0"] += 1
                    elif op == 0xB:
                        if (imm >> 11) & 1:
                            coverage["out_sample_no_shift"] += 1
                        else:
                            coverage["out_sample_shift"] += 1
                    elif op == 0xC:
                        if min(0xFFF, ref.run_param + (imm & 0xFF)) == 0:
                            coverage["wait_param_zero"] += 1
                        elif ref.run_param + (imm & 0xFF) >= 0xFFF:
                            coverage["wait_param_saturated"] += 1
                    elif op == 0xD:
                        pol = (imm >> 11) & 1
                        pin = (imm >> 8) & 7
                        lim = ref.run_param if (imm & 0xFF) == 0 else (imm & 0xFF)
                        if ((pin_v >> pin) & 1) == pol:
                            coverage["wait_edge_immediate_hit"] += 1
                        elif lim == 0:
                            coverage["wait_edge_timeout"] += 1
                    elif op in (0xE, 0xF):
                        coverage["illegal_fault_hiz"] += 1
                elif ref.wait_edge_active and not do_h:
                    if ((pin_v >> ref.wait_edge_pin) & 1) == ref.wait_edge_pol:
                        coverage["wait_edge_delayed_hit"] += 1
                    elif ref.wait_cnt == 1:
                        coverage["wait_edge_timeout"] += 1
                if do_h:
                    coverage["cmd_halt_abort"] += 1

                ref.step(pin_v, cmd_halt=bool(do_h))
                if ref.event_overflow:
                    coverage["mark_overflow"] += 1
                ref_states.append(ref.observable())
                used_sched.append((pin_v, do_h))
            assert ref.eng_state in (ENG_HALTED, ENG_FAULT), f"Seed {seed} failed to terminate in {len(full_sched)} steps"
            ref_pops: List[Tuple[int, int]] = []
            while True:
                p = ref.pop_event()
                if p is None:
                    break
                ref_pops.append(p)
            total_cycles += len(used_sched)

            stim_lines.append(f"{seed} {len(words)} {run_param} {len(used_sched)}")
            stim_lines.extend(f"{w:04x}" for w in words)
            stim_lines.extend(f"{pv:02x} {dh}" for pv, dh in used_sched)
            cases.append((seed, words, run_param, used_sched, ref_states, ref_pops))

        stim_path.write_text("\n".join(stim_lines) + "\n", encoding="utf-8")
        vvp = shutil.which("vvp") or "/usr/bin/vvp"
        res = subprocess.run([vvp, str(vvp_path), f"+STIM={stim_path}"], capture_output=True, text=True, check=False)
        if res.returncode != 0:
            raise RuntimeError(f"vvp failed:\n{res.stderr}\n{res.stdout}")

    rtl_states: Dict[int, List[Tuple[int, ...]]] = {s: [] for s in range(num_seeds)}
    rtl_pops: Dict[int, List[Tuple[int, int]]] = {s: [] for s in range(num_seeds)}
    for line in res.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "S":
            sid = int(parts[1])
            rtl_states[sid].append(
                (
                    int(parts[3]),
                    int(parts[4]),
                    int(parts[5], 16),
                    int(parts[6], 16),
                    int(parts[7]),
                    int(parts[8], 16),
                    int(parts[9], 16),
                    int(parts[10], 16),
                    int(parts[11]),
                    int(parts[12]),
                    int(parts[13], 16),
                    int(parts[14], 16),
                )
            )
        elif parts[0] == "E":
            sid = int(parts[1])
            rtl_pops[sid].append((int(parts[3], 16), int(parts[4], 16)))

    mismatches: List[Dict[str, Any]] = []
    for seed, _, _, _, ref_s, ref_p in cases:
        if rtl_states[seed] != ref_s:
            for idx, (rs, ms) in enumerate(zip(rtl_states[seed], ref_s)):
                if rs != ms:
                    mismatches.append({"seed": seed, "cycle": idx, "rtl": rs, "ref": ms})
                    break
            else:
                mismatches.append({"seed": seed, "reason": f"len {len(rtl_states[seed])} != {len(ref_s)}"})
        if rtl_pops[seed] != ref_p:
            mismatches.append({"seed": seed, "reason": f"pops {rtl_pops[seed]} != {ref_p}"})

    cov_json = dict(coverage)
    cov_json["opcodes_executed"] = sorted(f"0x{op:X}" for op in coverage["opcodes_executed"])
    return {
        "seeds": num_seeds,
        "total_cycles_compared": total_cycles,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:10],
        "coverage": cov_json,
    }


def format_markdown(rep: Dict[str, Any]) -> str:
    cov = rep["coverage"]
    lines = [
        "# Phase 4 Randomized ISA Differential Testing Report",
        "",
        f"Compared `src/protocol_sequencer.v` cycle-by-cycle against independent Python ISA reference model `SequencerRefModel` across **{rep['seeds']}** reproducible programs (**{rep['total_cycles_compared']:,}** simulated clock cycles).",
        "",
        f"- **Cycle & FIFO Mismatches:** **`{rep['mismatch_count']}`** (`0` tolerated)",
        f"- **Opcodes Executed (`16/16`):** `{', '.join(cov['opcodes_executed'])}`",
        "",
        "| Boundary / Branch Feature | Observed Count |",
        "|---|---|",
        f"| `JZ` taken / not taken | {cov['jz_taken']} / {cov['jz_not_taken']} |",
        f"| `LOOP` taken / fallthrough (`cnt=1` / `cnt=0`) | {cov['loop_taken']} / {cov['loop_fallthrough_cnt1']} / {cov['loop_fallthrough_cnt0']} |",
        f"| `SAMPLE` shift / `no_shift=1` | {cov['sample_shift']} / {cov['sample_no_shift']} |",
        f"| `OUT_SAMPLE` shift / `no_shift=1` | {cov['out_sample_shift']} / {cov['out_sample_no_shift']} |",
        f"| `MARK` immediate / `MARK_SAMPLE` | {cov['mark_imm']} / {cov['mark_sample']} |",
        f"| `WAIT(0)` / `WAIT(4095)` | {cov['wait_zero']} / {cov['wait_max_4095']} |",
        f"| `WAIT_PARAM(0)` / saturated `0xFFF` | {cov['wait_param_zero']} / {cov['wait_param_saturated']} |",
        f"| `WAIT_EDGE` immediate hit / delayed hit / timeout | {cov['wait_edge_immediate_hit']} / {cov['wait_edge_delayed_hit']} / {cov['wait_edge_timeout']} |",
        f"| Event FIFO overflow cycles (`>8` `MARK`s) | {cov['mark_overflow']} |",
        f"| `HALT(keep_oe=0)` / `HALT(keep_oe=1)` | {cov['halt_keep_oe_0']} / {cov['halt_keep_oe_1']} |",
        f"| Illegal opcode (`0xE`/`0xF`) `ENG_FAULT` + Hi-Z (`uio_oe=0`) | {cov['illegal_fault_hiz']} |",
        f"| `cmd_halt` mid-run abort | {cov['cmd_halt_abort']} |",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Randomized ISA differential test against protocol_sequencer.v")
    parser.add_argument("--seeds", type=int, default=120)
    parser.add_argument("--md-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    rep = run_differential_suite(num_seeds=args.seeds)
    md = format_markdown(rep)
    if args.md_out:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(md, encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
    print(md)
    assert rep["mismatch_count"] == 0, rep["mismatches"]
    cov = rep["coverage"]
    assert len(cov["opcodes_executed"]) == 16
    assert cov["jz_taken"] > 0 and cov["jz_not_taken"] > 0
    assert cov["loop_taken"] > 0 and cov["loop_fallthrough_cnt1"] > 0 and cov["loop_fallthrough_cnt0"] > 0
    assert cov["sample_shift"] > 0 and cov["sample_no_shift"] > 0
    assert cov["out_sample_shift"] > 0 and cov["out_sample_no_shift"] > 0
    assert cov["mark_imm"] > 0 and cov["mark_sample"] > 0
    assert cov["wait_zero"] > 0 and cov["wait_max_4095"] > 0
    assert cov["wait_param_zero"] > 0 and cov["wait_param_saturated"] > 0
    assert cov["wait_edge_immediate_hit"] > 0 and cov["wait_edge_delayed_hit"] > 0 and cov["wait_edge_timeout"] > 0
    assert cov["mark_overflow"] > 0
    assert cov["halt_keep_oe_0"] > 0 and cov["halt_keep_oe_1"] > 0
    assert cov["illegal_fault_hiz"] > 0
    assert cov["cmd_halt_abort"] > 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
