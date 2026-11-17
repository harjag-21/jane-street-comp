/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/1ps
`default_nettype none

// Separately written behavioral IEEE 1149.1 JTAG TAP controller for verification.
// Implements the 16-state TAP FSM, 4-bit IR (Capture-IR=4'b0101, IDCODE=4'h1,
// BYPASS=4'hF), 32-bit IDCODE DR, and falling-edge TDO with configurable delay.
module jtag_tap_model #(
    parameter [31:0] IDCODE_VALUE = 32'h1A2B4D6F,
    parameter [3:0]  IR_IDCODE    = 4'h1,
    parameter [3:0]  IR_BYPASS    = 4'hF
) (
    input  wire        tck,
    input  wire        tms,
    input  wire        tdi,
    input  integer     tdo_delay_ns,
    output reg         tdo,
    output reg  [3:0]  tap_state_o,
    output reg  [3:0]  ir_o,
    output reg  [15:0] tlr_entries_o,
    output reg  [15:0] update_ir_count_o,
    output reg  [15:0] update_dr_count_o
);

  // Standard IEEE 1149.1 TAP state encodings
  localparam [3:0] EXIT2_DR  = 4'h0,
                   EXIT1_DR  = 4'h1,
                   SHIFT_DR  = 4'h2,
                   PAUSE_DR  = 4'h3,
                   SEL_IR    = 4'h4,
                   UPDATE_DR = 4'h5,
                   CAP_DR    = 4'h6,
                   SEL_DR    = 4'h7,
                   EXIT2_IR  = 4'h8,
                   EXIT1_IR  = 4'h9,
                   SHIFT_IR  = 4'hA,
                   PAUSE_IR  = 4'hB,
                   RTI       = 4'hC,
                   UPDATE_IR = 4'hD,
                   CAP_IR    = 4'hE,
                   TLR       = 4'hF;

  reg [3:0]  ir_shift;
  reg [31:0] dr_shift;
  reg        bypass_reg;
  reg [3:0]  next_state;

  initial begin
    tdo               = 1'b0;
    tap_state_o       = TLR;
    ir_o              = IR_IDCODE;
    ir_shift          = 4'b0101;
    dr_shift          = IDCODE_VALUE;
    bypass_reg        = 1'b0;
    tlr_entries_o     = 16'd0;
    update_ir_count_o = 16'd0;
    update_dr_count_o = 16'd0;
  end

  // Combinational next-state logic per IEEE 1149.1 Table 6-1
  always @(*) begin
    case (tap_state_o)
      TLR:       next_state = tms ? TLR       : RTI;
      RTI:       next_state = tms ? SEL_DR    : RTI;
      SEL_DR:    next_state = tms ? SEL_IR    : CAP_DR;
      CAP_DR:    next_state = tms ? EXIT1_DR  : SHIFT_DR;
      SHIFT_DR:  next_state = tms ? EXIT1_DR  : SHIFT_DR;
      EXIT1_DR:  next_state = tms ? UPDATE_DR : PAUSE_DR;
      PAUSE_DR:  next_state = tms ? EXIT2_DR  : PAUSE_DR;
      EXIT2_DR:  next_state = tms ? UPDATE_DR : SHIFT_DR;
      UPDATE_DR: next_state = tms ? SEL_DR    : RTI;
      SEL_IR:    next_state = tms ? TLR       : CAP_IR;
      CAP_IR:    next_state = tms ? EXIT1_IR  : SHIFT_IR;
      SHIFT_IR:  next_state = tms ? EXIT1_IR  : SHIFT_IR;
      EXIT1_IR:  next_state = tms ? UPDATE_IR : PAUSE_IR;
      PAUSE_IR:  next_state = tms ? EXIT2_IR  : PAUSE_IR;
      EXIT2_IR:  next_state = tms ? UPDATE_IR : SHIFT_IR;
      UPDATE_IR: next_state = tms ? SEL_DR    : RTI;
      default:   next_state = TLR;
    endcase
  end

  // TAP transitions and capture/shift/update happen on posedge TCK
  always @(posedge tck) begin
    case (tap_state_o)
      CAP_IR:    ir_shift <= 4'b0101;
      SHIFT_IR:  ir_shift <= {tdi, ir_shift[3:1]};
      UPDATE_IR: begin
        ir_o              <= ir_shift;
        update_ir_count_o <= update_ir_count_o + 16'd1;
      end
      CAP_DR: begin
        if (ir_o == IR_IDCODE) begin
          dr_shift <= IDCODE_VALUE;
        end else begin
          bypass_reg <= 1'b0;
        end
      end
      SHIFT_DR: begin
        if (ir_o == IR_IDCODE) begin
          dr_shift <= {tdi, dr_shift[31:1]};
        end else begin
          bypass_reg <= tdi;
        end
      end
      UPDATE_DR: begin
        update_dr_count_o <= update_dr_count_o + 16'd1;
      end
      default: ;
    endcase

    if (next_state == TLR) begin
      ir_o          <= IR_IDCODE;
      tlr_entries_o <= tlr_entries_o + 16'd1;
    end
    tap_state_o <= next_state;
  end

  // IEEE 1149.1 drives TDO on negedge TCK
  always @(negedge tck) begin : tdo_drv_blk
    reg next_tdo;
    if (tap_state_o == SHIFT_DR) begin
      next_tdo = (ir_o == IR_IDCODE) ? dr_shift[0] : bypass_reg;
    end else if (tap_state_o == SHIFT_IR) begin
      next_tdo = ir_shift[0];
    end else begin
      next_tdo = 1'b0;
    end
    if (tdo_delay_ns > 0) begin
      tdo <= #(tdo_delay_ns) next_tdo;
    end else begin
      tdo <= next_tdo;
    end
  end

endmodule
`default_nettype wire
