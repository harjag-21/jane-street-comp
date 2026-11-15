/* SPDX-License-Identifier: Apache-2.0 */
/* Timing Boundary Explorer v0.1 simulation bring-up; not the final engine. */
`ifndef __librelane__
`timescale 1ns/1ps
`endif
`default_nettype none
module tt_um_example #(
    parameter integer CLKS_PER_BIT = 8
) (
    input wire [7:0] ui_in, output wire [7:0] uo_out,
    input wire [7:0] uio_in, output wire [7:0] uio_out,
    output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n
);
  localparam integer HALF_BIT = (CLKS_PER_BIT < 2) ? 1 : CLKS_PER_BIT / 2;
  localparam [2:0] RX_IDLE=0, RX_START=1, RX_DATA=2, RX_STOP=3;
  localparam [1:0] TX_IDLE=0, TX_START=1, TX_DATA=2, TX_STOP=3;
  localparam [1:0] SPI_IDLE=0, SPI_LOW=1, SPI_HIGH=2;
  localparam [3:0] CMD_SYNC=0, CMD_BODY=1, CMD_CRC=2, CMD_START=3,
                   CMD_WAIT=4, CMD_PREP=5, CMD_SEND=6, CMD_TX_BUSY=7,
                   CMD_TX_DONE=8;

  reg [2:0] rx_state; reg [15:0] rx_count; reg [2:0] rx_bit;
  reg [7:0] rx_shift, rx_byte; reg rx_byte_valid;
  reg [1:0] tx_state; reg [15:0] tx_count; reg [2:0] tx_bit;
  reg [7:0] tx_shift, tx_data; reg tx_start, tx_busy;
  reg [1:0] spi_state; reg [15:0] spi_count; reg [2:0] spi_bit;
  reg [15:0] spi_low_cycles; reg [7:0] spi_mosi, spi_miso;
  reg spi_start, spi_done, sclk, cs_n, mosi, trial_active;
  reg [3:0] cmd_state; reg [2:0] body_index, response_index;
  reg [7:0] crc_work, opcode, request_id, low_lo, low_hi, request_mosi;
  reg [7:0] command_status, response [0:6]; integer i;

  function [7:0] crc8_byte;
    input [7:0] crc_in; input [7:0] data_in; reg [7:0] c; integer k;
    begin
      c = crc_in ^ data_in;
      for (k=0; k<8; k=k+1) c = c[7] ? ((c << 1) ^ 8'h07) : (c << 1);
      crc8_byte = c;
    end
  endfunction

  // UART RX: 8N1, LSB first. A bad stop bit is discarded.
  always @(posedge clk) begin
    if (!rst_n) begin rx_state<=RX_IDLE; rx_count<=0; rx_bit<=0; rx_shift<=0; rx_byte<=0; rx_byte_valid<=0; end
    else begin
      rx_byte_valid <= 0;
      case (rx_state)
        RX_IDLE: if (!ui_in[0]) begin rx_state<=RX_START; rx_count<=HALF_BIT[15:0]-16'd1; end
        RX_START: if (rx_count==0) begin
          if (!ui_in[0]) begin rx_state<=RX_DATA; rx_count<=CLKS_PER_BIT[15:0]-16'd1; rx_bit<=0; end
          else rx_state<=RX_IDLE;
        end else rx_count<=rx_count-16'd1;
        RX_DATA: if (rx_count==0) begin
          rx_count<=CLKS_PER_BIT[15:0]-16'd1;
          if (rx_bit==7) begin rx_shift[7]<=ui_in[0]; rx_state<=RX_STOP; end
          else begin rx_shift[rx_bit]<=ui_in[0]; rx_bit<=rx_bit+1'b1; end
        end else rx_count<=rx_count-16'd1;
        RX_STOP: if (rx_count==0) begin
          rx_state<=RX_IDLE;
          if (ui_in[0]) begin rx_byte<=rx_shift; rx_byte_valid<=1; end
        end else rx_count<=rx_count-16'd1;
        default: rx_state<=RX_IDLE;
      endcase
    end
  end

  // UART TX: tx_start is accepted only in TX_IDLE.
  always @(posedge clk) begin
    if (!rst_n) begin tx_state<=TX_IDLE; tx_count<=0; tx_bit<=0; tx_shift<=8'hff; tx_busy<=0; end
    else case (tx_state)
      TX_IDLE: begin tx_busy<=0; if (tx_start) begin tx_shift<=tx_data; tx_bit<=0; tx_count<=CLKS_PER_BIT[15:0]-16'd1; tx_state<=TX_START; tx_busy<=1; end end
      TX_START: if (tx_count==0) begin tx_state<=TX_DATA; tx_count<=CLKS_PER_BIT[15:0]-16'd1; end else tx_count<=tx_count-16'd1;
      TX_DATA: if (tx_count==0) begin
        tx_count<=CLKS_PER_BIT[15:0]-16'd1;
        if (tx_bit==7) tx_state<=TX_STOP;
        else begin tx_bit<=tx_bit+1'b1; tx_shift<={1'b0,tx_shift[7:1]}; end
      end else tx_count<=tx_count-16'd1;
      TX_STOP: if (tx_count==0) begin tx_state<=TX_IDLE; tx_busy<=0; end else tx_count<=tx_count-16'd1;
      default: tx_state<=TX_IDLE;
    endcase
  end

  // SPI master: mode 0, MSB first, two core-clock high cycles.
  always @(posedge clk) begin
    if (!rst_n) begin
      spi_state<=SPI_IDLE; spi_count<=0; spi_bit<=0; spi_low_cycles<=0; spi_mosi<=0; spi_miso<=0;
      spi_done<=0; sclk<=0; cs_n<=1; mosi<=0; trial_active<=0;
    end else begin
      spi_done<=0;
      case (spi_state)
        SPI_IDLE: begin
          sclk<=0; cs_n<=1; trial_active<=0;
          if (spi_start) begin
            spi_low_cycles<={low_hi,low_lo}; spi_mosi<=request_mosi;
            spi_state<=SPI_LOW; spi_count<=0; spi_bit<=0; spi_miso<=0; cs_n<=0; mosi<=request_mosi[7]; trial_active<=1;
          end
        end
        SPI_LOW: if (spi_count==spi_low_cycles-16'd1) begin
          sclk<=1; spi_count<=0; spi_miso<={spi_miso[6:0],uio_in[0]}; spi_state<=SPI_HIGH;
        end else spi_count<=spi_count+16'd1;
        SPI_HIGH: if (spi_count==16'd1) begin
          sclk<=0; spi_count<=0;
          if (spi_bit==7) begin cs_n<=1; trial_active<=0; spi_done<=1; spi_state<=SPI_IDLE; end
          else begin spi_bit<=spi_bit+1'b1; mosi<=spi_mosi[3'd6-spi_bit]; spi_state<=SPI_LOW; end
        end else spi_count<=spi_count+16'd1;
        default: spi_state<=SPI_IDLE;
      endcase
    end
  end

  // Requests: A5, opcode, id, low_lo, low_hi, MOSI, CRC-8/07.
  // Responses: 5A, id, status, low_lo, low_hi, MISO, CRC-8/07.
  always @(posedge clk) begin
    if (!rst_n) begin
      cmd_state<=CMD_SYNC; body_index<=0; response_index<=0; crc_work<=0;
      opcode<=0; request_id<=0; low_lo<=0; low_hi<=0; request_mosi<=0; command_status<=0;
      tx_start<=0; tx_data<=8'hff; spi_start<=0;
      for (i=0;i<7;i=i+1) response[i]<=0;
    end else begin
      tx_start<=0; spi_start<=0;
      case (cmd_state)
        CMD_SYNC: if (rx_byte_valid && rx_byte==8'hA5) begin crc_work<=crc8_byte(0,8'hA5); body_index<=0; cmd_state<=CMD_BODY; end
        CMD_BODY: if (rx_byte_valid) begin
          crc_work<=crc8_byte(crc_work,rx_byte);
          case (body_index)
            0: opcode<=rx_byte; 1: request_id<=rx_byte; 2: low_lo<=rx_byte; 3: low_hi<=rx_byte; 4: request_mosi<=rx_byte;
          endcase
          if (body_index==4) cmd_state<=CMD_CRC; else body_index<=body_index+1'b1;
        end
        CMD_CRC: if (rx_byte_valid) begin
          if (rx_byte!=crc_work) command_status<=1;
          else if (opcode!=8'h01) command_status<=2;
          else if ({low_hi,low_lo}==0) command_status<=3;
          else command_status<=0;
          cmd_state<=CMD_START;
        end
        CMD_START: if (command_status==0) begin
          spi_start<=1; cmd_state<=CMD_WAIT;
        end else cmd_state<=CMD_PREP;
        CMD_WAIT: if (spi_done) cmd_state<=CMD_PREP;
        CMD_PREP: begin
          response[0]<=8'h5A; response[1]<=request_id; response[2]<=command_status;
          response[3]<=low_lo; response[4]<=low_hi; response[5]<=(command_status==0)?spi_miso:0;
          response[6]<=crc8_byte(crc8_byte(crc8_byte(crc8_byte(crc8_byte(crc8_byte(0,8'h5A),request_id),command_status),low_lo),low_hi),(command_status==0)?spi_miso:0);
          response_index<=0; cmd_state<=CMD_SEND;
        end
        CMD_SEND: if (!tx_busy) begin tx_data<=response[response_index]; tx_start<=1; cmd_state<=CMD_TX_BUSY; end
        CMD_TX_BUSY: if (tx_busy) cmd_state<=CMD_TX_DONE;
        CMD_TX_DONE: if (!tx_busy) begin
          if (response_index==6) cmd_state<=CMD_SYNC;
          else begin response_index<=response_index+1'b1; cmd_state<=CMD_SEND; end
        end
        default: cmd_state<=CMD_SYNC;
      endcase
    end
  end

  assign uo_out[0]=(tx_state==TX_START)?0:(tx_state==TX_DATA)?tx_shift[0]:1;
  assign uo_out[1]=sclk; assign uo_out[2]=cs_n; assign uo_out[3]=mosi; assign uo_out[4]=trial_active; assign uo_out[7:5]=0;
  assign uio_out=0; assign uio_oe=0;
  wire _unused=&{ena,ui_in[7:1],uio_in[7:1],1'b0};
endmodule
`default_nettype wire
