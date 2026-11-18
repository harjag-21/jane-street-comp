/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/1ps
`default_nettype none

// Standalone JTAG TAP verification harness driving the top-level ASIC
// (`tt_um_harjag_21_timing_explorer`) over UART and verifying reset recovery,
// full IEEE 1149.1 state progression, returned 32-bit IDCODE, and cycle timing.
module tb_jtag_tap;
  localparam integer CLKS_PER_BIT = 8;

  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;
  wire [7:0] uio_in;

  integer    tdo_delay_ns = 0;

  always #5 clk = ~clk;

  tt_um_harjag_21_timing_explorer #(
      .CLKS_PER_BIT(CLKS_PER_BIT)
  ) dut (
      .ui_in  (ui_in),
      .uo_out (uo_out),
      .uio_in (uio_in),
      .uio_out(uio_out),
      .uio_oe (uio_oe),
      .ena    (ena),
      .clk    (clk),
      .rst_n  (rst_n)
  );

  // Separate IEEE 1149.1 JTAG TAP peripheral on uio[3:0]
  wire jtag_tck = uio_oe[0] ? uio_out[0] : 1'b0;
  wire jtag_tms = uio_oe[1] ? uio_out[1] : 1'b1;
  wire jtag_tdi = uio_oe[2] ? uio_out[2] : 1'b0;
  wire jtag_tdo;
  wire [3:0] tap_state;
  wire [3:0] tap_ir;
  wire [15:0] tlr_entries, uir_cnt, udr_cnt;

  assign uio_in = {4'hF, jtag_tdo, 3'b111};

  jtag_tap_model #(
      .IDCODE_VALUE(32'h1A2B4D6F)
  ) u_tap (
      .tck              (jtag_tck),
      .tms              (jtag_tms),
      .tdi              (jtag_tdi),
      .tdo_delay_ns     (tdo_delay_ns),
      .tdo              (jtag_tdo),
      .tap_state_o      (tap_state),
      .ir_o             (tap_ir),
      .tlr_entries_o    (tlr_entries),
      .update_ir_count_o(uir_cnt),
      .update_dr_count_o(udr_cnt)
  );

  // Track ordered IEEE 1149.1 state milestones monotonically on core clock (sees settled NBA tap_state)
  reg [3:0] milestone_stage = 4'd0;
  reg       trace_en = 1'b0;

  always @(posedge clk) begin
    if (trace_en) begin
      // Wait for initial PAUSE_DR(3) -> 5x TMS=1 -> TLR(F) -> RTI(C) -> SEL_DR(7)
      // -> SEL_IR(4) -> CAP_IR(E) -> SHIFT_IR(A) -> EXIT1_IR(9) -> UPDATE_IR(D)
      // -> SEL_DR(7) -> CAP_DR(6) -> SHIFT_DR(2) -> EXIT1_DR(1) -> UPDATE_DR(5) -> RTI(C)
      case (milestone_stage)
        4'd0:  if (tap_state == 4'hF) milestone_stage <= 4'd1;  // TLR reached
        4'd1:  if (tap_state == 4'hC) milestone_stage <= 4'd2;  // RTI
        4'd2:  if (tap_state == 4'h7) milestone_stage <= 4'd3;  // SEL_DR
        4'd3:  if (tap_state == 4'h4) milestone_stage <= 4'd4;  // SEL_IR
        4'd4:  if (tap_state == 4'hE) milestone_stage <= 4'd5;  // CAP_IR
        4'd5:  if (tap_state == 4'hA) milestone_stage <= 4'd6;  // SHIFT_IR
        4'd6:  if (tap_state == 4'hD) milestone_stage <= 4'd7;  // UPDATE_IR
        4'd7:  if (tap_state == 4'h6) milestone_stage <= 4'd8;  // CAP_DR
        4'd8:  if (tap_state == 4'h2) milestone_stage <= 4'd9;  // SHIFT_DR
        4'd9:  if (tap_state == 4'h5) milestone_stage <= 4'd10; // UPDATE_DR
        4'd10: if (tap_state == 4'hC) milestone_stage <= 4'd11; // Final RTI
        default: ;
      endcase
    end
  end

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

  function [7:0] bitrev8;
    input [7:0] x;
    integer k;
    begin
      for (k = 0; k < 8; k = k + 1) begin
        bitrev8[k] = x[7 - k];
      end
    end
  endfunction

  task automatic uart_send_byte;
    input [7:0] b;
    integer i;
    begin
      ui_in[0] = 1'b0;
      repeat (CLKS_PER_BIT) @(posedge clk);
      for (i = 0; i < 8; i = i + 1) begin
        ui_in[0] = b[i];
        repeat (CLKS_PER_BIT) @(posedge clk);
      end
      ui_in[0] = 1'b1;
      repeat (CLKS_PER_BIT) @(posedge clk);
    end
  endtask

  task automatic uart_recv_byte;
    output [7:0] b;
    integer i;
    integer wd;
    begin
      wd = 0;
      while ((uo_out[0] !== 1'b0) && (wd < 2000)) begin
        @(posedge clk);
        wd = wd + 1;
      end
      if (uo_out[0] !== 1'b0) begin
        $display("FAIL: UART TX start timeout");
        $finish_and_return(1);
      end
      repeat (CLKS_PER_BIT + CLKS_PER_BIT / 2) @(posedge clk);
      for (i = 0; i < 8; i = i + 1) begin
        b[i] = uo_out[0];
        repeat (CLKS_PER_BIT) @(posedge clk);
      end
    end
  endtask

  task automatic send_cmd;
    input  [7:0] op, id, a0, a1, a2, a3;
    output [7:0] st, d0, d1, d2, d3;
    reg [7:0] c, rs, rid, rcrc;
    begin
      c = 8'h00;
      c = crc8_byte(c, 8'hA5);
      c = crc8_byte(c, op);
      c = crc8_byte(c, id);
      c = crc8_byte(c, a0);
      c = crc8_byte(c, a1);
      c = crc8_byte(c, a2);
      c = crc8_byte(c, a3);
      fork
        begin
          uart_send_byte(8'hA5);
          uart_send_byte(op);
          uart_send_byte(id);
          uart_send_byte(a0);
          uart_send_byte(a1);
          uart_send_byte(a2);
          uart_send_byte(a3);
          uart_send_byte(c);
        end
        begin
          uart_recv_byte(rs);
          uart_recv_byte(rid);
          uart_recv_byte(st);
          uart_recv_byte(d0);
          uart_recv_byte(d1);
          uart_recv_byte(d2);
          uart_recv_byte(d3);
          uart_recv_byte(rcrc);
        end
      join
      repeat (CLKS_PER_BIT / 2 + 2) @(posedge clk);
      if ((rs !== 8'h5A) || (rid !== id) || (st !== 8'h00)) begin
        $display("FAIL: UART cmd op=%02x st=%02x", op, st);
        $finish_and_return(1);
      end
    end
  endtask

  reg [15:0] prog [0:49];
  integer    idx;
  reg [7:0]  crc, s8, st, d0, d1, d2, d3;
  reg [31:0] cap_idcode;
  reg [15:0] ts [0:3];
  reg [15:0] total_cycles;

  initial begin
    // Exact 50-word output of `tools/protocol_asm.py --example jtag_idcode`
    prog[ 0] = 16'h1002; prog[ 1] = 16'h2007; prog[ 2] = 16'h8005; prog[ 3] = 16'h1003;
    prog[ 4] = 16'h1002; prog[ 5] = 16'h7003; prog[ 6] = 16'h1000; prog[ 7] = 16'h1001;
    prog[ 8] = 16'h1002; prog[ 9] = 16'h1003; prog[10] = 16'h1002; prog[11] = 16'h1003;
    prog[12] = 16'h1000; prog[13] = 16'h1001; prog[14] = 16'h1000; prog[15] = 16'h1001;
    prog[16] = 16'h1004; prog[17] = 16'h1005; prog[18] = 16'h1000; prog[19] = 16'h1001;
    prog[20] = 16'h1000; prog[21] = 16'h1001; prog[22] = 16'h1002; prog[23] = 16'h1003;
    prog[24] = 16'h1002; prog[25] = 16'h1003; prog[26] = 16'h1002; prog[27] = 16'h1003;
    prog[28] = 16'h1000; prog[29] = 16'h1001; prog[30] = 16'h1000; prog[31] = 16'h1001;
    prog[32] = 16'h8104; prog[33] = 16'h8008; prog[34] = 16'h1000; prog[35] = 16'hc000;
    prog[36] = 16'h4003; prog[37] = 16'h1001; prog[38] = 16'h3001; prog[39] = 16'h7022;
    prog[40] = 16'h9100; prog[41] = 16'h7121; prog[42] = 16'h1002; prog[43] = 16'h1003;
    prog[44] = 16'h1002; prog[45] = 16'h1003; prog[46] = 16'h1000; prog[47] = 16'h1001;
    prog[48] = 16'h1000; prog[49] = 16'ha000;

    crc = 8'h00;
    s8  = 8'h00;
    for (idx = 0; idx < 50; idx = idx + 1) begin
      crc = crc8_byte(crc, prog[idx][7:0]);
      crc = crc8_byte(crc, prog[idx][15:8]);
      s8  = s8 + prog[idx][7:0] + prog[idx][15:8];
    end

    rst_n = 1'b0;
    repeat (6) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    // Load the 50-word JTAG IDCODE microprogram over UART
    send_cmd(8'h11, 8'h01, 8'd50, crc, s8, 8'h00, st, d0, d1, d2, d3);
    for (idx = 0; idx < 50; idx = idx + 1) begin
      send_cmd(8'h12, 8'h10 + idx[7:0], idx[7:0], prog[idx][7:0], prog[idx][15:8], 8'h00,
               st, d0, d1, d2, d3);
    end
    send_cmd(8'h13, 8'h50, 8'h00, 8'h00, 8'h00, 8'h00, st, d0, d1, d2, d3);

    // Deliberately pre-dirty TAP state into PAUSE_DR (4'h3) and IR=BYPASS (4'hF) before run!
    u_tap.tap_state_o = 4'h3;
    u_tap.ir_o        = 4'hF;

    milestone_stage = 4'd0;
    trace_en = 1'b1;

    tdo_delay_ns = 0;
    send_cmd(8'h14, 8'h60, 8'd1, 8'd0, 8'h00, 8'h00, st, d0, d1, d2, d3);
    while (uo_out[4:3] != 2'd2) @(posedge clk);
    trace_en = 1'b0;

    if (milestone_stage !== 4'd11) begin
      $display("FAIL: Out-of-order/incomplete IEEE 1149.1 TAP progression (stage=%0d)", milestone_stage);
      $finish_and_return(1);
    end
    if (tap_state !== 4'hC || tap_ir !== 4'h1 || uir_cnt < 1 || udr_cnt < 1) begin
      $display("FAIL: End TAP state=%h IR=%h", tap_state, tap_ir);
      $finish_and_return(1);
    end

    send_cmd(8'h18, 8'h61, 8'd0, 8'd0, 8'h00, 8'h00, st, d0, d1, d2, d3);
    total_cycles = {d1, d0};

    for (idx = 0; idx < 4; idx = idx + 1) begin
      send_cmd(8'h17, 8'h70 + idx[7:0], 8'd0, 8'd0, 8'h00, 8'h00, st, d0, d1, d2, d3);
      if (d0 == 8'd0) begin
        $display("FAIL: Missing IDCODE MARK byte %0d", idx);
        $finish_and_return(1);
      end
      ts[idx] = {d3, d2};
      case (idx)
        0: cap_idcode[7:0]   = bitrev8(d1);
        1: cap_idcode[15:8]  = bitrev8(d1);
        2: cap_idcode[23:16] = bitrev8(d1);
        3: cap_idcode[31:24] = bitrev8(d1);
      endcase
    end

    if (cap_idcode !== 32'h1A2B4D6F) begin
      $display("FAIL: Captured IDCODE=%08x expected=1a2b4d6f", cap_idcode);
      $finish_and_return(1);
    end

    $display("PASS: JTAG TAP reset, state progression, IDCODE=0x%08x, total_cycles=%0d, marks=(%0d,%0d,%0d,%0d)",
             cap_idcode, total_cycles, ts[0], ts[1], ts[2], ts[3]);
    $finish;
  end
endmodule
`default_nettype wire
