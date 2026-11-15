/* SPDX-License-Identifier: Apache-2.0 */
/* Timing Boundary Explorer v1: UART loader + programmable protocol sequencer. */
`ifndef __librelane__
`timescale 1ns/1ps
`endif
`default_nettype none

module tt_um_example #(
    parameter integer CLKS_PER_BIT = 8
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);

  localparam integer PROG_DEPTH         = 64;
  localparam integer EVENT_DEPTH        = 8;
  localparam integer HALF_BIT           = (CLKS_PER_BIT < 2) ? 1 : (CLKS_PER_BIT / 2);
  localparam integer FRAME_TIMEOUT_CLKS = 64 * CLKS_PER_BIT;

  localparam [2:0] RX_IDLE = 3'd0, RX_START = 3'd1, RX_DATA = 3'd2, RX_STOP = 3'd3;
  localparam [1:0] TX_IDLE = 2'd0, TX_START = 2'd1, TX_DATA = 2'd2, TX_STOP = 2'd3;

  localparam [3:0] CMD_SYNC    = 4'd0,
                   CMD_BODY    = 4'd1,
                   CMD_CRC     = 4'd2,
                   CMD_EXEC    = 4'd3,
                   CMD_PREP    = 4'd4,
                   CMD_SEND    = 4'd5,
                   CMD_TX_BUSY = 4'd6,
                   CMD_TX_DONE = 4'd7;

  localparam [7:0] OP_PING_INFO      = 8'h10,
                   OP_BEGIN_LOAD     = 8'h11,
                   OP_WRITE_WORD     = 8'h12,
                   OP_COMMIT_LOAD    = 8'h13,
                   OP_START          = 8'h14,
                   OP_HALT           = 8'h15,
                   OP_READ_STATUS    = 8'h16,
                   OP_POP_EVENT      = 8'h17,
                   OP_READ_CYCLE_CNT = 8'h18;

  localparam [7:0] ST_OK        = 8'h00,
                   ST_BAD_CRC   = 8'h01,
                   ST_BAD_OP    = 8'h02,
                   ST_BAD_ARG   = 8'h03,
                   ST_BAD_STATE = 8'h04,
                   ST_CHECKSUM  = 8'h05;

  localparam [1:0] ENG_RUN = 2'd1;

  wire rst_active_n = rst_n & ena;

  // UART RX with 2-flop input synchronizer
  reg       rx_d1, rx_d2;
  reg [2:0] rx_state;
  reg [15:0] rx_count;
  reg [2:0] rx_bit;
  reg [7:0] rx_shift, rx_byte;
  reg       rx_byte_valid;

  // UART TX
  reg [1:0] tx_state;
  reg [15:0] tx_count;
  reg [2:0] tx_bit;
  reg [7:0] tx_shift, tx_data;
  reg       tx_start, tx_busy;

  // Loader & command FSM
  reg [3:0]  cmd_state;
  reg [2:0]  body_idx, resp_idx;
  reg [15:0] frame_timer;
  reg [7:0]  crc_work, tx_crc;
  reg [7:0]  req_op, req_id, req_arg0, req_arg1, req_arg2, req_arg3;
  reg [7:0]  cmd_status, resp_d0, resp_d1, resp_d2, resp_d3;
  reg [7:0]  response [0:6];

  reg        prog_we, prog_valid, load_active;
  reg [5:0]  prog_addr;
  reg [15:0] prog_wdata;
  reg [6:0]  load_len, load_idx;
  reg [7:0]  exp_crc, exp_sum, calc_crc, calc_sum;
  reg        cmd_start, cmd_halt, event_pop;
  reg [11:0] cmd_param;

  wire [3:0]  event_count;
  wire        event_overflow;
  wire [7:0]  event_head_tag;
  wire [15:0] event_head_ts;
  wire [1:0]  eng_state;
  wire [5:0]  pc;
  wire        last_sample;
  wire [7:0]  sample_reg;
  wire [15:0] cycle_cnt;
  wire        trial_active;

  integer i;

  function [7:0] crc8_byte;
    input [7:0] crc_in;
    input [7:0] data_in;
    reg [7:0] c;
    integer k;
    begin
      c = crc_in ^ data_in;
      for (k = 0; k < 8; k = k + 1) begin
        c = c[7] ? ((c << 1) ^ 8'h07) : (c << 1);
      end
      crc8_byte = c;
    end
  endfunction

  protocol_sequencer #(
      .PROG_DEPTH(PROG_DEPTH),
      .ADDR_W(6),
      .EVENT_DEPTH(EVENT_DEPTH),
      .EVENT_PTR_W(3)
  ) u_sequencer (
      .clk(clk),
      .rst_n(rst_active_n),
      .prog_we(prog_we),
      .prog_addr(prog_addr),
      .prog_wdata(prog_wdata),
      .prog_valid(prog_valid),
      .cmd_start(cmd_start),
      .cmd_param(cmd_param),
      .cmd_halt(cmd_halt),
      .event_pop(event_pop),
      .event_count_o(event_count),
      .event_overflow_o(event_overflow),
      .event_head_tag_o(event_head_tag),
      .event_head_ts_o(event_head_ts),
      .eng_state_o(eng_state),
      .pc_o(pc),
      .last_sample_o(last_sample),
      .sample_reg_o(sample_reg),
      .cycle_cnt_o(cycle_cnt),
      .uio_in(uio_in),
      .uio_out_o(uio_out),
      .uio_oe_o(uio_oe),
      .trial_active_o(trial_active)
  );

  // UART RX: 8N1, LSB first, 2-FF synchronized. Bad stop bit discarded.
  always @(posedge clk) begin
    if (!rst_active_n) begin
      rx_d1         <= 1'b1;
      rx_d2         <= 1'b1;
      rx_state      <= RX_IDLE;
      rx_count      <= 16'd0;
      rx_bit        <= 3'd0;
      rx_shift      <= 8'h00;
      rx_byte       <= 8'h00;
      rx_byte_valid <= 1'b0;
    end else begin
      rx_d1         <= ui_in[0];
      rx_d2         <= rx_d1;
      rx_byte_valid <= 1'b0;
      case (rx_state)
        RX_IDLE: begin
          if (!rx_d2) begin
            rx_state <= RX_START;
            rx_count <= HALF_BIT[15:0] - 16'd1;
          end
        end
        RX_START: begin
          if (rx_count == 16'd0) begin
            if (!rx_d2) begin
              rx_state <= RX_DATA;
              rx_count <= CLKS_PER_BIT[15:0] - 16'd1;
              rx_bit   <= 3'd0;
            end else begin
              rx_state <= RX_IDLE;
            end
          end else begin
            rx_count <= rx_count - 16'd1;
          end
        end
        RX_DATA: begin
          if (rx_count == 16'd0) begin
            rx_count         <= CLKS_PER_BIT[15:0] - 16'd1;
            rx_shift[rx_bit] <= rx_d2;
            if (rx_bit == 3'd7) begin
              rx_state <= RX_STOP;
            end else begin
              rx_bit <= rx_bit + 3'd1;
            end
          end else begin
            rx_count <= rx_count - 16'd1;
          end
        end
        RX_STOP: begin
          if (rx_count == 16'd0) begin
            rx_state <= RX_IDLE;
            if (rx_d2) begin
              rx_byte       <= rx_shift;
              rx_byte_valid <= 1'b1;
            end
          end else begin
            rx_count <= rx_count - 16'd1;
          end
        end
        default: rx_state <= RX_IDLE;
      endcase
    end
  end

  // UART TX: 8N1, LSB first.
  always @(posedge clk) begin
    if (!rst_active_n) begin
      tx_state <= TX_IDLE;
      tx_count <= 16'd0;
      tx_bit   <= 3'd0;
      tx_shift <= 8'hFF;
      tx_busy  <= 1'b0;
    end else begin
      case (tx_state)
        TX_IDLE: begin
          tx_busy <= 1'b0;
          if (tx_start) begin
            tx_shift <= tx_data;
            tx_bit   <= 3'd0;
            tx_count <= CLKS_PER_BIT[15:0] - 16'd1;
            tx_state <= TX_START;
            tx_busy  <= 1'b1;
          end
        end
        TX_START: begin
          if (tx_count == 16'd0) begin
            tx_state <= TX_DATA;
            tx_count <= CLKS_PER_BIT[15:0] - 16'd1;
          end else begin
            tx_count <= tx_count - 16'd1;
          end
        end
        TX_DATA: begin
          if (tx_count == 16'd0) begin
            tx_count <= CLKS_PER_BIT[15:0] - 16'd1;
            if (tx_bit == 3'd7) begin
              tx_state <= TX_STOP;
            end else begin
              tx_bit   <= tx_bit + 3'd1;
              tx_shift <= {1'b0, tx_shift[7:1]};
            end
          end else begin
            tx_count <= tx_count - 16'd1;
          end
        end
        TX_STOP: begin
          if (tx_count == 16'd0) begin
            tx_state <= TX_IDLE;
            tx_busy  <= 1'b0;
          end else begin
            tx_count <= tx_count - 16'd1;
          end
        end
        default: tx_state <= TX_IDLE;
      endcase
    end
  end

  // Loader & Command FSM:
  // Request:  A5, op, id, arg0, arg1, arg2, arg3, crc8
  // Response: 5A, id, status, d0, d1, d2, d3, crc8
  always @(posedge clk) begin
    if (!rst_active_n) begin
      cmd_state   <= CMD_SYNC;
      body_idx    <= 3'd0;
      resp_idx    <= 3'd0;
      frame_timer <= 16'd0;
      crc_work    <= 8'h00;
      tx_crc      <= 8'h00;
      req_op      <= 8'h00;
      req_id      <= 8'h00;
      req_arg0    <= 8'h00;
      req_arg1    <= 8'h00;
      req_arg2    <= 8'h00;
      req_arg3    <= 8'h00;
      cmd_status  <= ST_OK;
      resp_d0     <= 8'h00;
      resp_d1     <= 8'h00;
      resp_d2     <= 8'h00;
      resp_d3     <= 8'h00;
      for (i = 0; i < 7; i = i + 1) begin
        response[i] <= 8'h00;
      end
      tx_start    <= 1'b0;
      tx_data     <= 8'hFF;
      prog_we     <= 1'b0;
      prog_valid  <= 1'b0;
      load_active <= 1'b0;
      prog_addr   <= 6'd0;
      prog_wdata  <= 16'h0000;
      load_len    <= 7'd0;
      load_idx    <= 7'd0;
      exp_crc     <= 8'h00;
      exp_sum     <= 8'h00;
      calc_crc    <= 8'h00;
      calc_sum    <= 8'h00;
      cmd_start   <= 1'b0;
      cmd_halt    <= 1'b0;
      event_pop   <= 1'b0;
      cmd_param   <= 12'd0;
    end else begin
      tx_start  <= 1'b0;
      prog_we   <= 1'b0;
      cmd_start <= 1'b0;
      cmd_halt  <= 1'b0;
      event_pop <= 1'b0;

      case (cmd_state)
        CMD_SYNC: begin
          frame_timer <= 16'd0;
          if (rx_byte_valid && (rx_byte == 8'hA5)) begin
            crc_work  <= crc8_byte(8'h00, 8'hA5);
            body_idx  <= 3'd0;
            cmd_state <= CMD_BODY;
          end
        end

        CMD_BODY: begin
          if (rx_byte_valid) begin
            frame_timer <= 16'd0;
            crc_work    <= crc8_byte(crc_work, rx_byte);
            case (body_idx)
              3'd0: req_op   <= rx_byte;
              3'd1: req_id   <= rx_byte;
              3'd2: req_arg0 <= rx_byte;
              3'd3: req_arg1 <= rx_byte;
              3'd4: req_arg2 <= rx_byte;
              3'd5: req_arg3 <= rx_byte;
              default: ;
            endcase
            if (body_idx == 3'd5) begin
              cmd_state <= CMD_CRC;
            end else begin
              body_idx <= body_idx + 3'd1;
            end
          end else if (frame_timer == FRAME_TIMEOUT_CLKS[15:0]) begin
            cmd_state   <= CMD_SYNC;
            frame_timer <= 16'd0;
          end else begin
            frame_timer <= frame_timer + 16'd1;
          end
        end

        CMD_CRC: begin
          if (rx_byte_valid) begin
            frame_timer <= 16'd0;
            if (rx_byte != crc_work) begin
              cmd_status <= ST_BAD_CRC;
              resp_d0    <= 8'h00;
              resp_d1    <= 8'h00;
              resp_d2    <= 8'h00;
              resp_d3    <= 8'h00;
              cmd_state  <= CMD_PREP;
            end else begin
              cmd_state <= CMD_EXEC;
            end
          end else if (frame_timer == FRAME_TIMEOUT_CLKS[15:0]) begin
            cmd_state   <= CMD_SYNC;
            frame_timer <= 16'd0;
          end else begin
            frame_timer <= frame_timer + 16'd1;
          end
        end

        CMD_EXEC: begin
          resp_d0 <= 8'h00;
          resp_d1 <= 8'h00;
          resp_d2 <= 8'h00;
          resp_d3 <= 8'h00;
          case (req_op)
            OP_PING_INFO: begin
              cmd_status <= ST_OK;
              resp_d0    <= 8'h01;
              resp_d1    <= PROG_DEPTH[7:0];
              resp_d2    <= EVENT_DEPTH[7:0];
              resp_d3    <= {4'd0, prog_valid, event_overflow, eng_state};
            end

            OP_BEGIN_LOAD: begin
              if (eng_state == ENG_RUN) begin
                cmd_status <= ST_BAD_STATE;
              end else if ((req_arg0 == 8'd0) || (req_arg0 > PROG_DEPTH[7:0])) begin
                cmd_status <= ST_BAD_ARG;
              end else begin
                cmd_status  <= ST_OK;
                prog_valid  <= 1'b0;
                load_active <= 1'b1;
                load_len    <= req_arg0[6:0];
                load_idx    <= 7'd0;
                exp_crc     <= req_arg1;
                exp_sum     <= req_arg2;
                calc_crc    <= 8'h00;
                calc_sum    <= 8'h00;
                resp_d0     <= req_arg0;
                resp_d1     <= req_arg1;
                resp_d2     <= req_arg2;
              end
            end

            OP_WRITE_WORD: begin
              if ((eng_state == ENG_RUN) || !load_active) begin
                cmd_status <= ST_BAD_STATE;
              end else if ((req_arg0 >= PROG_DEPTH[7:0]) || ({1'b0, req_arg0[5:0]} != load_idx)) begin
                cmd_status <= ST_BAD_ARG;
              end else begin
                cmd_status <= ST_OK;
                prog_we    <= 1'b1;
                prog_addr  <= req_arg0[5:0];
                prog_wdata <= {req_arg2, req_arg1};
                calc_crc   <= crc8_byte(crc8_byte(calc_crc, req_arg1), req_arg2);
                calc_sum   <= calc_sum + req_arg1 + req_arg2;
                load_idx   <= load_idx + 7'd1;
                resp_d0    <= req_arg0;
                resp_d1    <= {1'b0, load_idx + 7'd1};
              end
            end

            OP_COMMIT_LOAD: begin
              if ((eng_state == ENG_RUN) || !load_active) begin
                cmd_status <= ST_BAD_STATE;
              end else if ((load_idx != load_len) || (calc_crc != exp_crc) || (calc_sum != exp_sum)) begin
                cmd_status  <= ST_CHECKSUM;
                prog_valid  <= 1'b0;
                load_active <= 1'b0;
                resp_d0     <= {1'b0, load_idx};
                resp_d1     <= calc_crc;
                resp_d2     <= calc_sum;
                resp_d3     <= 8'h00;
              end else begin
                cmd_status  <= ST_OK;
                prog_valid  <= 1'b1;
                load_active <= 1'b0;
                resp_d0     <= {1'b0, load_idx};
                resp_d1     <= calc_crc;
                resp_d2     <= calc_sum;
                resp_d3     <= 8'h01;
              end
            end

            OP_START: begin
              if (!prog_valid || (eng_state == ENG_RUN)) begin
                cmd_status <= ST_BAD_STATE;
              end else begin
                cmd_status <= ST_OK;
                cmd_param  <= {req_arg1[3:0], req_arg0};
                cmd_start  <= 1'b1;
                resp_d0    <= req_arg0;
                resp_d1    <= {4'd0, req_arg1[3:0]};
              end
            end

            OP_HALT: begin
              cmd_status <= ST_OK;
              cmd_halt   <= 1'b1;
              resp_d0    <= {4'd0, prog_valid, event_overflow, eng_state};
              resp_d1    <= {2'd0, pc};
            end

            OP_READ_STATUS: begin
              cmd_status <= ST_OK;
              resp_d0    <= {4'd0, prog_valid, event_overflow, eng_state};
              resp_d1    <= {2'd0, pc};
              resp_d2    <= sample_reg;
              resp_d3    <= {last_sample, 3'd0, event_count};
            end

            OP_POP_EVENT: begin
              cmd_status <= ST_OK;
              resp_d0    <= {4'd0, event_count};
              resp_d1    <= event_head_tag;
              resp_d2    <= event_head_ts[7:0];
              resp_d3    <= event_head_ts[15:8];
              if (event_count != 4'd0) begin
                event_pop <= 1'b1;
              end
            end

            OP_READ_CYCLE_CNT: begin
              cmd_status <= ST_OK;
              resp_d0    <= cycle_cnt[7:0];
              resp_d1    <= cycle_cnt[15:8];
              resp_d2    <= sample_reg;
              resp_d3    <= {4'd0, event_count};
            end

            default: begin
              cmd_status <= ST_BAD_OP;
            end
          endcase
          cmd_state <= CMD_PREP;
        end

        CMD_PREP: begin
          response[0] <= 8'h5A;
          response[1] <= req_id;
          response[2] <= cmd_status;
          response[3] <= resp_d0;
          response[4] <= resp_d1;
          response[5] <= resp_d2;
          response[6] <= resp_d3;
          tx_crc      <= 8'h00;
          resp_idx    <= 3'd0;
          cmd_state   <= CMD_SEND;
        end

        CMD_SEND: begin
          if (!tx_busy) begin
            if (resp_idx == 3'd7) begin
              tx_data <= tx_crc;
            end else begin
              tx_data <= response[resp_idx];
              tx_crc  <= crc8_byte(tx_crc, response[resp_idx]);
            end
            tx_start  <= 1'b1;
            cmd_state <= CMD_TX_BUSY;
          end
        end

        CMD_TX_BUSY: begin
          if (tx_busy) begin
            cmd_state <= CMD_TX_DONE;
          end
        end

        CMD_TX_DONE: begin
          if (!tx_busy) begin
            if (resp_idx == 3'd7) begin
              cmd_state <= CMD_SYNC;
            end else begin
              resp_idx  <= resp_idx + 3'd1;
              cmd_state <= CMD_SEND;
            end
          end
        end

        default: cmd_state <= CMD_SYNC;
      endcase
    end
  end

  assign uo_out[0]   = (tx_state == TX_START) ? 1'b0 : (tx_state == TX_DATA) ? tx_shift[0] : 1'b1;
  assign uo_out[1]   = trial_active;
  assign uo_out[2]   = prog_valid;
  assign uo_out[4:3] = eng_state;
  assign uo_out[5]   = event_overflow;
  assign uo_out[7:6] = 2'b00;

  wire _unused = &{ui_in[7:1], 1'b0};

endmodule
`default_nettype wire
