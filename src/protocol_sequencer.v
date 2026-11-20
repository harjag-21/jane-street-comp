/* SPDX-License-Identifier: Apache-2.0 */
`ifndef __librelane__
`timescale 1ns/1ps
`endif
`default_nettype none

// Deterministic single-issue programmable protocol sequencer (v1).
// Owns a 64x16 instruction store, 8-bit pin outputs/enables, sample/loop state,
// and an 8-entry timestamped event FIFO for timing-boundary observation.
module protocol_sequencer #(
    parameter integer PROG_DEPTH  = 64,
    parameter integer ADDR_W      = 6,
    parameter integer EVENT_DEPTH = 8,
    parameter integer EVENT_PTR_W = 3
) (
    input  wire                   clk,
    input  wire                   rst_n,

    // Program store write port (ignored while ENG_RUN)
    input  wire                   prog_we,
    input  wire [ADDR_W-1:0]      prog_addr,
    input  wire [15:0]            prog_wdata,
    input  wire                   prog_valid,
    input  wire                   prog_clear,

    // Execution control
    input  wire                   cmd_start,
    input  wire [11:0]            cmd_param,
    input  wire [11:0]            cmd_param2,
    input  wire                   cmd_halt,

    // Optional runtime robustness, budget, and passive observation controls
    input  wire [7:0]             cfg_sync_mask,
    input  wire [15:0]            cfg_cycle_budget,
    input  wire [7:0]             cfg_cap_mask,
    input  wire [1:0]             cfg_cap_mode,

    // Event FIFO readback port
    input  wire                   event_pop,
    output wire [EVENT_PTR_W:0]   event_count_o,
    output reg                    event_overflow_o,
    output wire [7:0]             event_head_tag_o,
    output wire [15:0]            event_head_ts_o,

    // Status readback
    output reg  [1:0]             eng_state_o,
    output reg  [ADDR_W-1:0]      pc_o,
    output reg                    last_sample_o,
    output reg  [7:0]             sample_reg_o,
    output reg  [15:0]            cycle_cnt_o,
    output reg  [7:0]             last_mark_tag_o,
    output reg  [2:0]             stop_reason_o,

    // Protocol I/O pins
    input  wire [7:0]             uio_in,
    output reg  [7:0]             uio_out_o,
    output reg  [7:0]             uio_oe_o,
    output wire                   trial_active_o
);

  localparam [1:0] ENG_IDLE   = 2'd0,
                   ENG_RUN    = 2'd1,
                   ENG_HALTED = 2'd2,
                   ENG_FAULT  = 2'd3;

  localparam [2:0] STOP_NONE        = 3'd0,
                   STOP_HALT_INSN   = 3'd1,
                   STOP_ILLEGAL_OP  = 3'd2,
                   STOP_CMD_HALT    = 3'd3,
                   STOP_CYCLE_LIMIT = 3'd4;

  localparam [3:0] OP_NOP        = 4'h0,
                   OP_OUT        = 4'h1,
                   OP_OE         = 4'h2,
                   OP_WAIT       = 4'h3,
                   OP_SAMPLE     = 4'h4,
                   OP_JZ         = 4'h5,
                   OP_JMP        = 4'h6,
                   OP_LOOP       = 4'h7,
                   OP_SETCOUNT   = 4'h8,
                   OP_MARK       = 4'h9,
                   OP_HALT       = 4'hA,
                   OP_OUT_SAMPLE = 4'hB,
                   OP_WAIT_PARAM = 4'hC,
                   OP_WAIT_EDGE  = 4'hD,
                   OP_TX         = 4'hE;

  reg [15:0] imem [0:PROG_DEPTH-1];
  reg [11:0] wait_cnt;
  reg        wait_edge_active;
  reg        wait_edge_pol;
  reg [2:0]  wait_edge_pin;
  reg [11:0] run_param, run_param2;
  reg [7:0]  loop_cnt0, loop_cnt1;
  reg [7:0]  tx_reg;
  reg        tx_lsb_first;
  reg        tx_od;
  reg [7:0]  sync_d1, sync_d2;
  reg [7:0]  cap_prev;

  reg [7:0]  ev_tag [0:EVENT_DEPTH-1];
  reg [15:0] ev_ts  [0:EVENT_DEPTH-1];
  reg [EVENT_PTR_W-1:0] ev_wptr, ev_rptr;
  reg [EVENT_PTR_W:0]   ev_count;

  integer idx;

  wire [15:0] ir          = imem[pc_o];
  wire [3:0]  op          = ir[15:12];
  wire [11:0] imm         = ir[11:0];
  wire        ctr_sel     = imm[8];
  wire [7:0]  cur_cnt     = ctr_sel ? loop_cnt1 : loop_cnt0;
  wire [7:0]  uio_eff     = (sync_d2 & cfg_sync_mask) | (uio_in & ~cfg_sync_mask);
  wire        samp_bit_s  = uio_eff[imm[2:0]];
  wire        samp_bit_os = uio_eff[imm[10:8]];
  wire [11:0] selected_param = imm[8] ? run_param2 : run_param;
  wire [12:0] param_sum     = {1'b0, selected_param} + {5'd0, imm[7:0]};
  wire [11:0] param_sat     = param_sum[12] ? 12'hFFF : param_sum[11:0];
  wire [11:0] edge_wait_lim = (imm[7:0] == 8'd0) ? run_param : {4'd0, imm[7:0]};
  wire        edge_now_hit  = (uio_eff[imm[10:8]] == imm[11]);
  wire        edge_wait_hit = (uio_eff[wait_edge_pin] == wait_edge_pol);

  wire        tx_cur_bit    = tx_lsb_first ? tx_reg[0] : tx_reg[7];
  wire [2:0]  tx_pin_sel    = imm[2:0];
  wire [2:0]  tx_rx_sel     = imm[5:3];
  wire [2:0]  tx_clk_sel    = imm[8:6];
  wire        tx_clk_val    = imm[9];
  wire        tx_do_shift   = imm[10];
  wire        tx_samp_bit   = uio_eff[tx_rx_sel];
  wire [7:0]  tx_pin_mask   = (8'h01 << tx_pin_sel);
  wire [7:0]  tx_clk_mask   = (8'h01 << tx_clk_sel);
  wire [7:0]  tx_out_step1   = tx_cur_bit ? (uio_out_o | tx_pin_mask) : (uio_out_o & ~tx_pin_mask);
  wire [7:0]  tx_out_next    = (tx_clk_sel != tx_pin_sel)
                             ? (tx_clk_val ? (tx_out_step1 | tx_clk_mask) : (tx_out_step1 & ~tx_clk_mask))
                             : tx_out_step1;
  wire [7:0]  tx_od_clr_mask = tx_pin_mask | ((tx_clk_sel != tx_pin_sel) ? tx_clk_mask : 8'h00);
  wire [7:0]  tx_oe_step1    = (!tx_cur_bit) ? (uio_oe_o | tx_pin_mask) : (uio_oe_o & ~tx_pin_mask);
  wire [7:0]  tx_oe_next     = (tx_clk_sel != tx_pin_sel)
                             ? ((!tx_clk_val) ? (tx_oe_step1 | tx_clk_mask) : (tx_oe_step1 & ~tx_clk_mask))
                             : tx_oe_step1;

  wire [EVENT_PTR_W:0] ev_depth_max = EVENT_DEPTH[EVENT_PTR_W:0];
  wire                 budget_hit   = (eng_state_o == ENG_RUN) && (cfg_cycle_budget != 16'd0) && (cycle_cnt_o >= cfg_cycle_budget);
  wire                 active_step  = (eng_state_o == ENG_RUN) && !cmd_halt && !budget_hit;
  wire [7:0]           cap_diff     = (uio_eff ^ cap_prev) & cfg_cap_mask;
  wire [7:0]           cap_rise     = cfg_cap_mode[0] ? (cap_diff & uio_eff)  : 8'h00;
  wire [7:0]           cap_fall     = cfg_cap_mode[1] ? (cap_diff & ~uio_eff) : 8'h00;
  wire                 do_cap       = active_step && (cfg_cap_mode != 2'b00) && ((cap_rise | cap_fall) != 8'h00);
  wire                 do_pop       = event_pop && (ev_count != 0);
  wire                 do_mark      = active_step && (wait_cnt == 12'd0) && (op == OP_MARK);
  wire                 do_ev        = do_mark || do_cap;
  wire [7:0]           mark_payload = imm[8] ? sample_reg_o : imm[7:0];
  wire [7:0]           ev_payload   = do_mark ? mark_payload : uio_eff;
  wire                 do_push      = do_ev && ((ev_count < ev_depth_max) || do_pop);

  assign event_count_o    = ev_count;
  assign event_head_tag_o = (ev_count != 0) ? ev_tag[ev_rptr] : 8'h00;
  assign event_head_ts_o  = (ev_count != 0) ? ev_ts[ev_rptr]  : 16'h0000;
  assign trial_active_o   = (eng_state_o == ENG_RUN);

  always @(posedge clk) begin
    if (!rst_n) begin
      for (idx = 0; idx < PROG_DEPTH; idx = idx + 1) begin
        imem[idx] <= 16'h0000;
      end
      for (idx = 0; idx < EVENT_DEPTH; idx = idx + 1) begin
        ev_tag[idx] <= 8'h00;
        ev_ts[idx]  <= 16'h0000;
      end
      eng_state_o      <= ENG_IDLE;
      pc_o             <= {ADDR_W{1'b0}};
      last_sample_o    <= 1'b0;
      sample_reg_o     <= 8'h00;
      cycle_cnt_o      <= 16'h0000;
      last_mark_tag_o  <= 8'h00;
      stop_reason_o    <= STOP_NONE;
      wait_cnt         <= 12'd0;
      wait_edge_active <= 1'b0;
      wait_edge_pol    <= 1'b0;
      wait_edge_pin    <= 3'd0;
      run_param        <= 12'd0;
      run_param2       <= 12'd0;
      loop_cnt0        <= 8'd0;
      loop_cnt1        <= 8'd0;
      tx_reg           <= 8'h00;
      tx_lsb_first     <= 1'b0;
      tx_od            <= 1'b0;
      sync_d1          <= 8'h00;
      sync_d2          <= 8'h00;
      cap_prev         <= 8'h00;
      ev_wptr          <= {EVENT_PTR_W{1'b0}};
      ev_rptr          <= {EVENT_PTR_W{1'b0}};
      ev_count         <= {(EVENT_PTR_W+1){1'b0}};
      event_overflow_o <= 1'b0;
      uio_out_o        <= 8'h00;
      uio_oe_o         <= 8'h00;
    end else begin
      sync_d1 <= uio_in;
      sync_d2 <= sync_d1;

      // Program clear or write allowed only when not executing.
      if (prog_clear && (eng_state_o != ENG_RUN)) begin
        for (idx = 0; idx < PROG_DEPTH; idx = idx + 1) begin
          imem[idx] <= 16'h0000;
        end
      end else if (prog_we && (eng_state_o != ENG_RUN)) begin
        imem[prog_addr] <= prog_wdata;
      end

      // Event FIFO pointer and count update (explicit MARK or hardware edge capture).
      if (do_ev) begin
        last_mark_tag_o <= ev_payload;
      end
      if (do_push) begin
        ev_tag[ev_wptr] <= ev_payload;
        ev_ts[ev_wptr]  <= cycle_cnt_o;
        ev_wptr         <= ev_wptr + {{(EVENT_PTR_W-1){1'b0}}, 1'b1};
      end else if (do_ev) begin
        event_overflow_o <= 1'b1;
      end

      if (do_pop) begin
        ev_rptr <= ev_rptr + {{(EVENT_PTR_W-1){1'b0}}, 1'b1};
      end

      if (do_push && !do_pop) begin
        ev_count <= ev_count + {{EVENT_PTR_W{1'b0}}, 1'b1};
      end else if (!do_push && do_pop) begin
        ev_count <= ev_count - {{EVENT_PTR_W{1'b0}}, 1'b1};
      end

      // Execution control & instruction step.
      if (cmd_halt) begin
        eng_state_o      <= ENG_HALTED;
        wait_cnt         <= 12'd0;
        wait_edge_active <= 1'b0;
        uio_oe_o         <= 8'h00;
        if (eng_state_o == ENG_RUN) begin
          stop_reason_o <= STOP_CMD_HALT;
        end
      end else if (eng_state_o != ENG_RUN) begin
        if (cmd_start && prog_valid) begin
          eng_state_o      <= ENG_RUN;
          pc_o             <= {ADDR_W{1'b0}};
          last_sample_o    <= 1'b0;
          sample_reg_o     <= 8'h00;
          cycle_cnt_o      <= 16'h0000;
          last_mark_tag_o  <= 8'h00;
          stop_reason_o    <= STOP_NONE;
          wait_cnt         <= 12'd0;
          wait_edge_active <= 1'b0;
          run_param        <= cmd_param;
          run_param2       <= cmd_param2;
          loop_cnt0        <= 8'd0;
          loop_cnt1        <= 8'd0;
          tx_reg           <= 8'h00;
          tx_lsb_first     <= 1'b0;
          tx_od            <= 1'b0;
          cap_prev         <= uio_eff;
          ev_wptr          <= {EVENT_PTR_W{1'b0}};
          ev_rptr          <= {EVENT_PTR_W{1'b0}};
          ev_count         <= {(EVENT_PTR_W+1){1'b0}};
          event_overflow_o <= 1'b0;
        end
      end else if (budget_hit) begin
        eng_state_o      <= ENG_FAULT;
        wait_cnt         <= 12'd0;
        wait_edge_active <= 1'b0;
        uio_oe_o         <= 8'h00;
        stop_reason_o    <= STOP_CYCLE_LIMIT;
      end else begin
        // ENG_RUN
        cap_prev    <= uio_eff;
        cycle_cnt_o <= cycle_cnt_o + 16'd1;

        if (wait_cnt != 12'd0) begin
          if (wait_edge_active) begin
            if (edge_wait_hit) begin
              wait_edge_active <= 1'b0;
              wait_cnt         <= 12'd0;
              last_sample_o    <= 1'b1;
              pc_o             <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end else if (wait_cnt == 12'd1) begin
              wait_edge_active <= 1'b0;
              wait_cnt         <= 12'd0;
              last_sample_o    <= 1'b0;
              pc_o             <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end else begin
              wait_cnt <= wait_cnt - 12'd1;
            end
          end else begin
            wait_cnt <= wait_cnt - 12'd1;
            if (wait_cnt == 12'd1) begin
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
          end
        end else begin
          case (op)
            OP_NOP: begin
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_OUT: begin
              uio_out_o <= imm[7:0];
              pc_o      <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_OE: begin
              uio_oe_o <= imm[7:0];
              pc_o     <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_WAIT: begin
              if (imm == 12'd0) begin
                pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end else begin
                wait_cnt <= imm;
              end
            end
            OP_SAMPLE: begin
              last_sample_o <= samp_bit_s;
              if (!imm[11]) begin
                sample_reg_o <= {sample_reg_o[6:0], samp_bit_s};
              end
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_JZ: begin
              pc_o <= (last_sample_o == 1'b0) ? imm[ADDR_W-1:0]
                                              : (pc_o + {{(ADDR_W-1){1'b0}}, 1'b1});
            end
            OP_JMP: begin
              pc_o <= imm[ADDR_W-1:0];
            end
            OP_LOOP: begin
              if (cur_cnt > 8'd1) begin
                if (ctr_sel) loop_cnt1 <= loop_cnt1 - 8'd1;
                else         loop_cnt0 <= loop_cnt0 - 8'd1;
                pc_o <= imm[ADDR_W-1:0];
              end else begin
                if (ctr_sel) loop_cnt1 <= 8'd0;
                else         loop_cnt0 <= 8'd0;
                pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end
            end
            OP_SETCOUNT: begin
              if (ctr_sel) loop_cnt1 <= imm[7:0];
              else         loop_cnt0 <= imm[7:0];
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_MARK: begin
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_HALT: begin
              eng_state_o   <= ENG_HALTED;
              stop_reason_o <= STOP_HALT_INSN;
              if (!imm[0]) begin
                uio_oe_o <= 8'h00;
              end
            end
            OP_OUT_SAMPLE: begin
              uio_out_o     <= imm[7:0];
              last_sample_o <= samp_bit_os;
              if (!imm[11]) begin
                sample_reg_o <= {sample_reg_o[6:0], samp_bit_os};
              end
              pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
            end
            OP_WAIT_PARAM: begin
              if (param_sat == 12'd0) begin
                pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end else begin
                wait_cnt <= param_sat;
              end
            end
            OP_WAIT_EDGE: begin
              if (edge_now_hit) begin
                last_sample_o <= 1'b1;
                pc_o          <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end else if (edge_wait_lim == 12'd0) begin
                last_sample_o <= 1'b0;
                pc_o          <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end else begin
                wait_edge_active <= 1'b1;
                wait_edge_pol    <= imm[11];
                wait_edge_pin    <= imm[10:8];
                wait_cnt         <= edge_wait_lim;
              end
            end
            OP_TX: begin
              if (!imm[11]) begin
                if (!imm[10]) begin
                  tx_reg       <= imm[7:0];
                  tx_lsb_first <= imm[8];
                  tx_od        <= imm[9];
                  pc_o         <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
                end else begin
                  eng_state_o   <= ENG_FAULT;
                  stop_reason_o <= STOP_ILLEGAL_OP;
                  uio_oe_o      <= 8'h00;
                end
              end else begin
                if (tx_od) begin
                  uio_out_o <= uio_out_o & ~tx_od_clr_mask;
                  uio_oe_o  <= tx_oe_next;
                end else begin
                  uio_out_o <= tx_out_next;
                end
                if (tx_do_shift) begin
                  tx_reg <= tx_lsb_first ? {1'b0, tx_reg[7:1]} : {tx_reg[6:0], 1'b0};
                  if (tx_rx_sel != tx_pin_sel) begin
                    last_sample_o <= tx_samp_bit;
                    sample_reg_o  <= {sample_reg_o[6:0], tx_samp_bit};
                  end
                end
                pc_o <= pc_o + {{(ADDR_W-1){1'b0}}, 1'b1};
              end
            end
            default: begin
              eng_state_o   <= ENG_FAULT;
              stop_reason_o <= STOP_ILLEGAL_OP;
              uio_oe_o      <= 8'h00;
            end
          endcase
        end
      end
    end
  end

endmodule
`default_nettype wire
