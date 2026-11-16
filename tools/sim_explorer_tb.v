/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/1ps
`default_nettype none

// Stdin/stdout simulation bridge for Timing Boundary Explorer host CLI.
// Instantiates the actual ASIC top module `tt_um_example` and models
// a configurable SPI mode-0 target, UART monitor, or I2C open-drain target on `uio[7:0]`.
module sim_explorer_tb;
  localparam integer CLKS_PER_BIT = 8;
  localparam integer MODE_SPI     = 0;
  localparam integer MODE_UART    = 1;
  localparam integer MODE_I2C     = 2;

  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  reg  [7:0] uio_in = 8'hFF;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  tt_um_example #(
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

  // 100 MHz nominal core clock (T = 10 ns) so integer cycles map cleanly to ns.
  always #5 clk = ~clk;

  // Target model configuration
  integer    target_mode   = MODE_SPI;
  integer    spi_delay_ns  = 23;
  reg  [7:0] spi_resp_byte = 8'hAA;
  reg        i2c_do_ack    = 1'b1;

  // Observed protocol-pin captures
  reg  [7:0] spi_mosi_rx   = 8'h00;
  reg  [3:0] spi_bit_idx   = 4'd0;
  reg        spi_miso_drv  = 1'b1;

  reg  [7:0] uart_mon_byte = 8'h00;
  reg        uart_mon_seen = 1'b0;
  reg        uart_mon_busy = 1'b0;

  reg  [7:0] i2c_rx_byte   = 8'h00;
  reg        i2c_seen      = 1'b0;
  reg        i2c_sda_low   = 1'b0;
  reg        i2c_od_fault  = 1'b0;

  // Open-drain resolved wires for I2C mode (pins 0=SCL, 1=SDA)
  wire scl_bus = uio_oe[0] ? uio_out[0] : 1'b1;
  wire sda_bus = i2c_sda_low ? 1'b0 : (uio_oe[1] ? uio_out[1] : 1'b1);

  // Drive uio_in according to active mode
  always @(*) begin
    if (target_mode == MODE_SPI) begin
      uio_in = {4'hF, spi_miso_drv, 3'b111};
    end else if (target_mode == MODE_I2C) begin
      uio_in = {6'h3F, sda_bus, scl_bus};
    end else begin
      uio_in = 8'hFF;
    end
  end

  // SPI mode-0 target (CS_N=uio[0], SCLK=uio[1], MOSI=uio[2], MISO=uio[3])
  wire spi_cs_n = uio_oe[0] ? uio_out[0] : 1'b1;
  wire spi_sclk = uio_oe[1] ? uio_out[1] : 1'b0;

  always @(negedge spi_cs_n) begin
    if (target_mode == MODE_SPI) begin
      spi_bit_idx  = 4'd0;
      spi_mosi_rx  = 8'h00;
      spi_miso_drv <= spi_resp_byte[7];
    end
  end

  always @(posedge spi_sclk) begin
    if ((target_mode == MODE_SPI) && !spi_cs_n) begin
      spi_mosi_rx = {spi_mosi_rx[6:0], uio_out[2]};
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI) && !spi_cs_n) begin
      if (spi_bit_idx < 4'd7) begin
        spi_bit_idx = spi_bit_idx + 4'd1;
        spi_miso_drv <= #(spi_delay_ns) spi_resp_byte[7 - spi_bit_idx];
      end
    end
  end

  // UART TX monitor on uio[0]
  wire uart_tx_line = uio_oe[0] ? uio_out[0] : 1'b1;
  always @(negedge uart_tx_line) begin : uart_mon_blk
    integer k;
    reg [7:0] captured;
    if ((target_mode == MODE_UART) && !uart_mon_busy && uio_oe[0]) begin
      uart_mon_busy = 1'b1;
      repeat (CLKS_PER_BIT + (CLKS_PER_BIT / 2)) @(posedge clk);
      for (k = 0; k < 8; k = k + 1) begin
        captured[k] = uart_tx_line;
        repeat (CLKS_PER_BIT) @(posedge clk);
      end
      uart_mon_byte = captured;
      uart_mon_seen = 1'b1;
      uart_mon_busy = 1'b0;
    end
  end

  // I2C open-drain monitor & ACK responder
  always @(posedge clk) begin
    if (target_mode == MODE_I2C) begin
      if ((uio_oe[0] && uio_out[0]) || (uio_oe[1] && uio_out[1])) begin
        i2c_od_fault <= 1'b1;
      end
    end
  end

  always @(negedge sda_bus) begin : i2c_blk
    integer b;
    reg [7:0] rx;
    if ((target_mode == MODE_I2C) && (scl_bus === 1'b1)) begin
      // Wait for START falling SCL edge
      while (scl_bus === 1'b1) @(posedge clk);
      rx = 8'h00;
      for (b = 0; b < 8; b = b + 1) begin
        while (scl_bus === 1'b0) @(posedge clk);
        rx = {rx[6:0], sda_bus};
        while (scl_bus === 1'b1) @(posedge clk);
      end
      if (i2c_do_ack) begin
        i2c_sda_low = 1'b1;
      end
      while (scl_bus === 1'b0) @(posedge clk);
      while (scl_bus === 1'b1) @(posedge clk);
      i2c_sda_low = 1'b0;
      i2c_rx_byte = rx;
      i2c_seen    = 1'b1;
    end
  end

  task automatic uart_send_byte_host;
    input [7:0] b;
    integer bit_i;
    begin
      ui_in[0] = 1'b0;
      repeat (CLKS_PER_BIT) @(posedge clk);
      for (bit_i = 0; bit_i < 8; bit_i = bit_i + 1) begin
        ui_in[0] = b[bit_i];
        repeat (CLKS_PER_BIT) @(posedge clk);
      end
      ui_in[0] = 1'b1;
      repeat (CLKS_PER_BIT) @(posedge clk);
    end
  endtask

  task automatic uart_recv_byte_host;
    output [7:0] b;
    output       ok;
    integer bit_i;
    integer timeout;
    begin
      ok = 1'b0;
      b  = 8'h00;
      timeout = 0;
      while ((uo_out[0] !== 1'b0) && (timeout < 2000)) begin
        @(posedge clk);
        timeout = timeout + 1;
      end
      if (uo_out[0] === 1'b0) begin
        ok = 1'b1;
        repeat (CLKS_PER_BIT + (CLKS_PER_BIT / 2)) @(posedge clk);
        for (bit_i = 0; bit_i < 8; bit_i = bit_i + 1) begin
          b[bit_i] = uo_out[0];
          repeat (CLKS_PER_BIT) @(posedge clk);
        end
      end
    end
  endtask

  integer cmd_ch;
  integer arg_a, arg_b, arg_c, arg_d;
  integer i;
  reg [7:0] tx_buf [0:7];
  reg [7:0] rx_buf [0:7];
  reg       rx_ok;
  reg       all_ok;

  initial begin
    ena = 1'b1;
    ui_in = 8'h01;
    rst_n = 1'b0;
    repeat (6) @(posedge clk);
    rst_n = 1'b1;
    repeat (4) @(posedge clk);

    cmd_ch = $fgetc(32'h8000_0000);
    while ((cmd_ch != -1) && (cmd_ch != "q")) begin
      if (cmd_ch == "r") begin
        // Reset DUT with requested ena (hex 0/1)
        if ($fscanf(32'h8000_0000, "%x", arg_a) == 1) begin
          ena   = arg_a[0];
          ui_in = 8'h01;
          rst_n = 1'b0;
          uart_mon_seen = 1'b0;
          i2c_seen      = 1'b0;
          i2c_od_fault  = 1'b0;
          i2c_sda_low   = 1'b0;
          spi_mosi_rx   = 8'h00;
          spi_miso_drv  = spi_resp_byte[7];
          repeat (6) @(posedge clk);
          rst_n = 1'b1;
          repeat (4) @(posedge clk);
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "c") begin
        // Configure target: c <mode_dec> <delay_ns_dec> <resp_hex> <ack_dec>
        if ($fscanf(32'h8000_0000, "%d %d %x %d", arg_a, arg_b, arg_c, arg_d) == 4) begin
          target_mode   = arg_a;
          spi_delay_ns  = arg_b;
          spi_resp_byte = arg_c[7:0];
          i2c_do_ack    = arg_d[0];
          spi_miso_drv  = spi_resp_byte[7];
          uart_mon_seen = 1'b0;
          i2c_seen      = 1'b0;
          i2c_od_fault  = 1'b0;
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "f") begin
        // Send 8-byte UART frame and capture 8-byte response frame
        for (i = 0; i < 8; i = i + 1) begin
          if ($fscanf(32'h8000_0000, "%x", arg_a) == 1) begin
            tx_buf[i] = arg_a[7:0];
          end else begin
            tx_buf[i] = 8'h00;
          end
        end
        all_ok = 1'b1;
        fork
          begin : tx_thr
            integer ti;
            for (ti = 0; ti < 8; ti = ti + 1) begin
              uart_send_byte_host(tx_buf[ti]);
            end
          end
          begin : rx_thr
            integer ri;
            for (ri = 0; ri < 8; ri = ri + 1) begin
              uart_recv_byte_host(rx_buf[ri], rx_ok);
              if (!rx_ok) all_ok = 1'b0;
            end
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
      end else if (cmd_ch == "w") begin
        // Wait N cycles (or until uo_out[4:3] halted/fault up to N cycles)
        if ($fscanf(32'h8000_0000, "%d", arg_a) == 1) begin
          arg_b = 0;
          while ((arg_b < arg_a) && (uo_out[4:3] != 2'd2) && (uo_out[4:3] != 2'd3)) begin
            @(posedge clk);
            arg_b = arg_b + 1;
          end
          @(posedge clk);
          $display("STAT %02x %02x %02x %02x %02x %0d",
                   uo_out, spi_mosi_rx, uart_mon_byte, i2c_rx_byte,
                   {5'd0, i2c_od_fault, i2c_seen, uart_mon_seen}, arg_b);
          $fflush(32'h8000_0001);
        end
      end
      cmd_ch = $fgetc(32'h8000_0000);
    end
    $finish;
  end
endmodule
`default_nettype wire
