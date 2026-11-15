`timescale 1ns/1ps
`default_nettype none

// Self-checking Icarus testbench for environments without Cocotb/Make.
module tb_v01;
  reg clk = 0, rst_n = 0, ena = 1;
  reg [7:0] ui_in = 8'h01, uio_in = 0;
  wire [7:0] uo_out, uio_out, uio_oe;
  reg [7:0] reply [0:6];
  reg [7:0] request [0:6];
  integer i, spi_starts, target_bit, errors, rising_edges, falling_edges;
  time rise_time [0:7], fall_time [0:7];

  tt_um_example #(.CLKS_PER_BIT(8)) dut (
    .ui_in(ui_in), .uo_out(uo_out), .uio_in(uio_in),
    .uio_out(uio_out), .uio_oe(uio_oe), .ena(ena), .clk(clk), .rst_n(rst_n)
  );

  always #5 clk = ~clk;

  function [7:0] crc8_byte;
    input [7:0] crc_in, data_in;
    reg [7:0] c;
    integer k;
    begin
      c = crc_in ^ data_in;
      for (k=0; k<8; k=k+1) c = c[7] ? ((c << 1) ^ 8'h07) : (c << 1);
      crc8_byte = c;
    end
  endfunction

  function [7:0] crc6;
    input [7:0] a,b,c,d,e,f;
    begin crc6=crc8_byte(crc8_byte(crc8_byte(crc8_byte(crc8_byte(crc8_byte(0,a),b),c),d),e),f); end
  endfunction

  task send_byte;
    input [7:0] value;
    integer bit_index;
    begin
      @(negedge clk); ui_in[0] = 0;
      repeat (8) @(posedge clk);
      for (bit_index=0; bit_index<8; bit_index=bit_index+1) begin
        @(negedge clk); ui_in[0] = value[bit_index];
        repeat (8) @(posedge clk);
      end
      @(negedge clk); ui_in[0] = 1;
      repeat (8) @(posedge clk);
    end
  endtask

  task send_request;
    input [7:0] opcode, id, low_lo, low_hi, mosi;
    input corrupt_crc;
    reg [7:0] crc;
    begin
      crc = crc6(8'hA5, opcode, id, low_lo, low_hi, mosi);
      if (corrupt_crc) crc = crc ^ 8'h01;
      send_byte(8'hA5); send_byte(opcode); send_byte(id); send_byte(low_lo);
      send_byte(low_hi); send_byte(mosi); send_byte(crc);
    end
  endtask

  task receive_byte;
    output [7:0] value;
    integer bit_index;
    begin
      @(negedge uo_out[0]);
      repeat (12) @(posedge clk); #1 value[0] = uo_out[0];
      for (bit_index=1; bit_index<8; bit_index=bit_index+1) begin
        repeat (8) @(posedge clk); #1 value[bit_index] = uo_out[0];
      end
    end
  endtask

  task receive_response;
    begin for (i=0; i<7; i=i+1) receive_byte(reply[i]); end
  endtask

  // Simple SPI target: 0xAA is preloaded and changes after SCLK falling edges.
  always @(negedge uo_out[2]) begin
    spi_starts = spi_starts + 1;
    target_bit = 7;
    uio_in[0] = 1'b1; // 0xAA[7]
  end
  always @(negedge uo_out[1]) begin
    if (uo_out[2] == 0 && target_bit > 0) begin
      target_bit = target_bit - 1;
      uio_in[0] = (8'hAA >> target_bit) & 1;
    end
  end
  always @(posedge uo_out[1]) begin
    if (rst_n && uo_out[2] == 0) begin
      if (rising_edges < 8) rise_time[rising_edges] = $time;
      rising_edges = rising_edges + 1;
    end
  end
  always @(negedge uo_out[1]) begin
    // The final falling edge deasserts CS_N in the same clocked RTL step.
    // Count it whenever it closes a previously observed active rising edge.
    if (rst_n && falling_edges < rising_edges) begin
      if (falling_edges < 8) fall_time[falling_edges] = $time;
      falling_edges = falling_edges + 1;
    end
  end

  task check;
    input condition;
    input [8*80-1:0] message;
    begin
      if (!condition) begin $display("FAIL: %0s", message); errors = errors + 1; end
    end
  endtask

  initial begin
    errors = 0; spi_starts = 0; target_bit = 0; rising_edges = 0; falling_edges = 0;
    repeat (4) @(posedge clk); rst_n = 1;
    repeat (2) @(posedge clk);

    send_request(8'h01, 8'h42, 8'h03, 8'h00, 8'h55, 0);
    receive_response;
    check(reply[0] == 8'h5A, "valid response sync");
    check(reply[1] == 8'h42, "valid response id");
    check(reply[2] == 0, "valid response status");
    check(reply[3] == 3 && reply[4] == 0, "valid response low_cycles");
    check(reply[5] == 8'hAA, "SPI sampled target byte");
    check(reply[6] == crc6(reply[0],reply[1],reply[2],reply[3],reply[4],reply[5]), "valid response CRC");
    check(spi_starts == 1, "one SPI transaction for valid command");
    check(rising_edges == 8 && falling_edges == 8, "eight SPI rising and falling edges");
    check(fall_time[0] - rise_time[0] == 20, "SPI high phase is two core clocks");
    check(rise_time[1] - fall_time[0] == 30, "SPI low phase is requested three core clocks");

    send_request(8'h01, 8'h43, 8'h03, 8'h00, 8'h55, 1); receive_response;
    check(reply[2] == 1 && reply[5] == 0, "bad CRC status and no data");
    check(reply[6] == crc6(reply[0],reply[1],reply[2],reply[3],reply[4],reply[5]), "bad CRC response CRC");
    check(spi_starts == 1, "bad CRC does not start SPI");

    send_request(8'h7E, 8'h44, 8'h03, 8'h00, 8'h55, 0); receive_response;
    check(reply[2] == 2 && reply[5] == 0, "bad opcode status and no data");
    check(spi_starts == 1, "bad opcode does not start SPI");

    send_request(8'h01, 8'h45, 8'h00, 8'h00, 8'h55, 0); receive_response;
    check(reply[2] == 3 && reply[5] == 0, "bad argument status and no data");
    check(spi_starts == 1, "zero low phase does not start SPI");

    if (errors == 0) $display("PASS: v0.1 UART/SPI contract checks passed");
    else $display("FAIL: %0d checks failed", errors);
    $finish_and_return(errors != 0);
  end
endmodule
`default_nettype wire
