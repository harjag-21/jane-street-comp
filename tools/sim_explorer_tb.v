/* SPDX-License-Identifier: Apache-2.0 */
`timescale 1ns/1ps
`default_nettype none

// Stdin/stdout simulation bridge for Timing Boundary Explorer host CLI.
// Instantiates `tt_um_harjag_21_timing_explorer` and models configurable targets:
//   0 = SPI mode-0 delayed MISO responder (`delay_ns`)
//   1 = UART 8N1 RX monitor (fixed 8 clocks/bit, start+stop framed) on uio[0]
//   2 = I2C open-drain byte receiver + ACK/NACK on uio[0]=SCL, uio[1]=SDA
//   3 = Stateful SPI peripheral (Ref vs DUT deselect-gap bug threshold `min_cs_high`)
//   4 = I2C clock-stretching responder (`stretch_cycles` on SCL after release)
//   5 = Multi-bug stateful SPI peripheral (`delay_ns`: 0=Ref, 1=DUT, 4 timing+state bugs)
//   6 = Noisy SPI mode-0 responder (`delay_ns` +/- `arg_d` ns LFSR jitter per trial)
module sim_explorer_tb;
  localparam integer CLKS_PER_BIT       = 8;
  localparam integer MODE_SPI           = 0;
  localparam integer MODE_UART          = 1;
  localparam integer MODE_I2C           = 2;
  localparam integer MODE_SPI_STATEFUL  = 3;
  localparam integer MODE_I2C_STRETCH   = 4;
  localparam integer MODE_SPI_MULTISPEC = 5;
  localparam integer MODE_SPI_NOISY     = 6;

  reg        clk = 1'b0;
  reg        rst_n = 1'b0;
  reg        ena = 1'b1;
  reg  [7:0] ui_in = 8'h01;
  reg  [7:0] uio_in = 8'hFF;
  wire [7:0] uo_out;
  wire [7:0] uio_out;
  wire [7:0] uio_oe;

  integer    tb_bit_clks = CLKS_PER_BIT;

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

  // 100 MHz nominal core clock (T = 10 ns) so integer cycles map 1:1 to 10 ns units.
  always #5 clk = ~clk;

  // Target model configuration
  integer    target_mode   = MODE_SPI;
  integer    spi_delay_ns  = 23;
  integer    spi_jitter_ns = 0;
  integer    cur_tx_delay_ns = 23;
  reg [31:0] tb_lfsr       = 32'hCAFE_BEEF;
  reg  [7:0] spi_resp_byte = 8'hAA;
  reg        i2c_do_ack    = 1'b1;

  // Observed protocol-pin captures
  reg  [7:0] spi_mosi_rx   = 8'h00;
  reg  [3:0] spi_bit_idx   = 4'd0;
  reg        spi_miso_drv  = 1'b1;

  // Stateful SPI flash-style responder state (MODE_SPI_STATEFUL)
  reg        st_wren        = 1'b0;
  reg  [7:0] st_prev_cmd    = 8'h00;
  reg  [7:0] st_tx_byte     = 8'h00;
  reg  [7:0] st_rx_byte     = 8'h00;
  reg  [3:0] st_bit_idx     = 4'd0;
  integer    st_cs_high_cnt = 100;

  // Multi-bug SPI responder state (MODE_SPI_MULTISPEC)
  reg        ms_wren        = 1'b0;
  reg        ms_sleep       = 1'b0;
  reg        ms_woke_flag   = 1'b0;
  reg        ms_prog_done   = 1'b0;
  reg        ms_err         = 1'b0;
  reg        ms_read_glitch = 1'b0;
  reg  [7:0] ms_prev_cmd    = 8'h00;
  reg  [7:0] ms_tx_byte     = 8'h00;
  reg  [7:0] ms_rx_byte     = 8'h00;
  reg  [3:0] ms_bit_idx     = 4'd0;
  integer    ms_cs_high_cnt = 100;
  integer    ms_last_cs_gap = 100;

  reg  [7:0] uart_mon_byte = 8'h00;
  reg        uart_mon_seen = 1'b0;
  reg        uart_mon_busy = 1'b0;

  reg  [7:0] i2c_rx_byte   = 8'h00;
  reg        i2c_seen      = 1'b0;
  reg        i2c_sda_low   = 1'b0;
  reg        i2c_scl_low   = 1'b0;
  reg        i2c_od_fault  = 1'b0;
  reg        i2c_st_armed  = 1'b0;
  integer    i2c_st_cnt    = 0;

  // Open-drain resolved wires for I2C modes (pins 0=SCL, 1=SDA)
  wire scl_ctrl_low = uio_oe[0] && !uio_out[0];
  wire scl_bus = i2c_scl_low ? 1'b0 : (uio_oe[0] ? uio_out[0] : 1'b1);
  wire sda_bus = i2c_sda_low ? 1'b0 : (uio_oe[1] ? uio_out[1] : 1'b1);

  always @(*) begin
    if ((target_mode == MODE_SPI) || (target_mode == MODE_SPI_STATEFUL) ||
        (target_mode == MODE_SPI_MULTISPEC) || (target_mode == MODE_SPI_NOISY)) begin
      uio_in = {4'hF, spi_miso_drv, 3'b111};
    end else if ((target_mode == MODE_I2C) || (target_mode == MODE_I2C_STRETCH)) begin
      uio_in = {6'h3F, sda_bus, scl_bus};
    end else begin
      uio_in = 8'hFF;
    end
  end

  wire spi_cs_n = uio_oe[0] ? uio_out[0] : 1'b1;
  wire spi_sclk = uio_oe[1] ? uio_out[1] : 1'b0;

  // MODE_SPI & MODE_SPI_NOISY: transport-delayed MISO SPI target
  always @(negedge spi_cs_n) begin : spi_noisy_cs_blk
    integer span;
    integer off;
    integer raw_d;
    if ((target_mode == MODE_SPI) || (target_mode == MODE_SPI_NOISY)) begin
      spi_bit_idx  = 4'd0;
      spi_mosi_rx  = 8'h00;
      spi_miso_drv <= spi_resp_byte[7];
      if ((target_mode == MODE_SPI_NOISY) && (spi_jitter_ns > 0)) begin
        tb_lfsr = tb_lfsr ^ (tb_lfsr << 13);
        tb_lfsr = tb_lfsr ^ (tb_lfsr >> 17);
        tb_lfsr = tb_lfsr ^ (tb_lfsr << 5);
        if (tb_lfsr == 32'd0) tb_lfsr = 32'hCAFE_BEEF;
        span  = (2 * spi_jitter_ns) + 1;
        off   = (tb_lfsr % span) - spi_jitter_ns;
        raw_d = spi_delay_ns + off;
        cur_tx_delay_ns = (raw_d < 0) ? 0 : raw_d;
      end else begin
        cur_tx_delay_ns = spi_delay_ns;
      end
    end
  end

  always @(posedge spi_sclk) begin
    if (((target_mode == MODE_SPI) || (target_mode == MODE_SPI_NOISY)) && !spi_cs_n) begin
      spi_mosi_rx = {spi_mosi_rx[6:0], uio_out[2]};
    end
  end

  always @(negedge spi_sclk) begin
    if (((target_mode == MODE_SPI) || (target_mode == MODE_SPI_NOISY)) && !spi_cs_n) begin
      if (spi_bit_idx < 4'd7) begin
        spi_bit_idx = spi_bit_idx + 4'd1;
        spi_miso_drv <= #(cur_tx_delay_ns) spi_resp_byte[7 - spi_bit_idx];
      end
    end
  end

  // MODE_SPI_STATEFUL: peripheral where `spi_delay_ns` sets required CS_N high cycles
  // after WREN (0x06) for subsequent RDSR (0x05) to preserve WEL (`0x02`).
  always @(posedge clk) begin
    if (target_mode == MODE_SPI_STATEFUL) begin
      if (spi_cs_n) begin
        st_cs_high_cnt <= st_cs_high_cnt + 1;
      end else begin
        st_cs_high_cnt <= 0;
      end
    end
  end

  always @(negedge spi_cs_n) begin : stateful_spi_cs_blk
    reg next_wren;
    if (target_mode == MODE_SPI_STATEFUL) begin
      next_wren = st_wren;
      if ((st_prev_cmd == 8'h06) && (st_cs_high_cnt < spi_delay_ns)) begin
        next_wren = 1'b0;
      end
      st_wren      = next_wren;
      st_tx_byte   = next_wren ? 8'h02 : 8'h00;
      spi_miso_drv <= st_tx_byte[7];
      st_bit_idx   = 4'd0;
      st_rx_byte   = 8'h00;
    end
  end

  always @(posedge spi_sclk) begin : stateful_spi_rx_blk
    reg [7:0] next_rx;
    if ((target_mode == MODE_SPI_STATEFUL) && !spi_cs_n) begin
      next_rx    = {st_rx_byte[6:0], uio_out[2]};
      st_rx_byte = next_rx;
      if (st_bit_idx == 4'd7) begin
        spi_mosi_rx = next_rx;
        st_prev_cmd = next_rx;
        if (next_rx == 8'h06) begin
          st_wren = 1'b1;
        end else if (next_rx != 8'h05) begin
          st_wren = 1'b0;
        end
      end
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI_STATEFUL) && !spi_cs_n) begin
      if (st_bit_idx < 4'd7) begin
        st_bit_idx   = st_bit_idx + 4'd1;
        spi_miso_drv <= st_tx_byte[7 - st_bit_idx];
      end
    end
  end

  // MODE_SPI_MULTISPEC (`spi_delay_ns`: 0=Reference, !=0=Buggy DUT with 4 timing bugs D1..D4)
  always @(posedge clk) begin
    if (target_mode == MODE_SPI_MULTISPEC) begin
      if (spi_cs_n) begin
        ms_cs_high_cnt <= ms_cs_high_cnt + 1;
      end else begin
        ms_cs_high_cnt <= 0;
      end
    end
  end

  always @(negedge spi_cs_n) begin : multispec_cs_blk
    reg is_dut;
    if (target_mode == MODE_SPI_MULTISPEC) begin
      is_dut = (spi_delay_ns != 0);
      ms_last_cs_gap = ms_cs_high_cnt;

      // D1 (wren_fast_drop): short CS_N high (<= 5 cycles / gap_wait<=2) drops WREN in DUT
      if (is_dut && (ms_prev_cmd == 8'h06) && (ms_cs_high_cnt <= 5)) begin
        ms_wren = 1'b0;
      end
      // D2 (wakeup_slow_recovery): after 0xB9 SLEEP -> 0xAB WAKEUP, DUT needs >= 10 CS_N high cycles (gap_wait>=7)
      if ((ms_prev_cmd == 8'hAB) && ms_sleep) begin
        if (!is_dut || (ms_cs_high_cnt >= 10)) begin
          ms_sleep     = 1'b0;
          ms_woke_flag = 1'b1;
        end
      end

      if (ms_sleep) begin
        ms_tx_byte = 8'hFF;
      end else if (ms_read_glitch) begin
        ms_tx_byte = 8'h99;
      end else if (ms_err) begin
        ms_tx_byte = 8'h80;
      end else if (ms_prev_cmd == 8'h9F) begin
        ms_tx_byte = 8'hA5;
      end else if (ms_prev_cmd == 8'h35) begin
        ms_tx_byte = 8'h50 | {6'd0, ms_prog_done, ms_wren};
      end else begin
        ms_tx_byte = {3'd0, ms_prog_done, ms_woke_flag, 1'b0, ms_wren, 1'b0};
      end

      spi_miso_drv <= ms_tx_byte[7];
      ms_bit_idx   = 4'd0;
      ms_rx_byte   = 8'h00;
    end
  end

  always @(posedge spi_sclk) begin : multispec_rx_blk
    reg [7:0] next_rx;
    reg       is_dut;
    if ((target_mode == MODE_SPI_MULTISPEC) && !spi_cs_n) begin
      next_rx    = {ms_rx_byte[6:0], uio_out[2]};
      ms_rx_byte = next_rx;
      if (ms_bit_idx == 4'd7) begin
        is_dut      = (spi_delay_ns != 0);
        spi_mosi_rx = next_rx;
        ms_woke_flag   = 1'b0;
        ms_read_glitch = 1'b0;

        // D4 (fast_read_corrupt): 0x9F followed quickly (cs_high <= 4 / gap_wait<=1) by 0x35 corrupts read
        if (!ms_sleep && is_dut && (ms_prev_cmd == 8'h9F) && (next_rx == 8'h35) && (ms_last_cs_gap <= 4)) begin
          ms_read_glitch = 1'b1;
        end

        if (!ms_sleep) begin
          if (next_rx == 8'h06) begin          // WREN
            ms_wren = 1'b1;
            ms_err  = 1'b0;
          end else if (next_rx == 8'h04) begin // WRDI
            ms_wren = 1'b0;
            ms_err  = 1'b0;
          end else if (next_rx == 8'hB9) begin // SLEEP
            ms_sleep = 1'b1;
          end else if (next_rx == 8'h02) begin // PROG
            // D3 (prog_wren_timeout): excessive CS_N high (>= 13 cycles / gap_wait>=10) between 0x06 and 0x02 decays WEL in DUT
            if (is_dut && (ms_prev_cmd == 8'h06) && (ms_last_cs_gap >= 13)) begin
              ms_wren = 1'b0;
              ms_err  = 1'b1;
            end else if (ms_wren) begin
              ms_prog_done = 1'b1;
              ms_wren      = 1'b0;
            end else begin
              ms_err = 1'b1;
            end
          end
        end
        ms_prev_cmd = next_rx;
      end
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI_MULTISPEC) && !spi_cs_n) begin
      if (ms_bit_idx < 4'd7) begin
        ms_bit_idx   = ms_bit_idx + 4'd1;
        spi_miso_drv <= ms_tx_byte[7 - ms_bit_idx];
      end
    end
  end

  // MODE_UART: framed 8N1 receiver at fixed 8 clks/bit on uio[0]
  wire uart_tx_line = uio_oe[0] ? uio_out[0] : 1'b1;
  always @(negedge uart_tx_line) begin : uart_mon_blk
    integer k;
    reg [7:0] captured;
    if ((target_mode == MODE_UART) && !uart_mon_busy && uio_oe[0]) begin
      uart_mon_busy = 1'b1;
      repeat (CLKS_PER_BIT / 2) @(posedge clk);
      if (uart_tx_line === 1'b0) begin
        for (k = 0; k < 8; k = k + 1) begin
          repeat (CLKS_PER_BIT) @(posedge clk);
          captured[k] = uart_tx_line;
        end
        repeat (CLKS_PER_BIT) @(posedge clk);
        if (uart_tx_line === 1'b1) begin
          uart_mon_byte = captured;
          uart_mon_seen = 1'b1;
        end
      end
      uart_mon_busy = 1'b0;
    end
  end

  // MODE_I2C & MODE_I2C_STRETCH
  always @(posedge clk) begin
    if ((target_mode == MODE_I2C) || (target_mode == MODE_I2C_STRETCH)) begin
      if ((uio_oe[0] && uio_out[0]) || (uio_oe[1] && uio_out[1])) begin
        i2c_od_fault <= 1'b1;
      end
    end
    if (target_mode == MODE_I2C_STRETCH) begin
      if (scl_ctrl_low) begin
        i2c_st_armed <= (spi_delay_ns > 0);
        i2c_st_cnt   <= spi_delay_ns;
        i2c_scl_low  <= (spi_delay_ns > 0);
      end else if (i2c_st_armed) begin
        if (i2c_st_cnt > 1) begin
          i2c_scl_low <= 1'b1;
          i2c_st_cnt  <= i2c_st_cnt - 1;
        end else begin
          i2c_scl_low  <= 1'b0;
          i2c_st_armed <= 1'b0;
          i2c_st_cnt   <= 0;
        end
      end
    end else begin
      i2c_scl_low  <= 1'b0;
      i2c_st_armed <= 1'b0;
    end
  end

  always @(negedge sda_bus) begin : i2c_blk
    integer b;
    reg [7:0] rx;
    if ((target_mode == MODE_I2C) && (scl_bus === 1'b1)) begin
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
      repeat (tb_bit_clks) @(posedge clk);
      for (bit_i = 0; bit_i < 8; bit_i = bit_i + 1) begin
        ui_in[0] = b[bit_i];
        repeat (tb_bit_clks) @(posedge clk);
      end
      ui_in[0] = 1'b1;
      repeat (tb_bit_clks) @(posedge clk);
    end
  endtask

  task automatic uart_recv_byte_host;
    output [7:0] b;
    output       ok;
    integer bit_i;
    integer timeout;
    integer limit;
    begin
      ok = 1'b0;
      b  = 8'h00;
      timeout = 0;
      limit = 256 * tb_bit_clks;
      while ((uo_out[0] !== 1'b0) && (timeout < limit)) begin
        @(posedge clk);
        timeout = timeout + 1;
      end
      if (uo_out[0] === 1'b0) begin
        ok = 1'b1;
        repeat (tb_bit_clks + (tb_bit_clks / 2)) @(posedge clk);
        for (bit_i = 0; bit_i < 8; bit_i = bit_i + 1) begin
          b[bit_i] = uo_out[0];
          repeat (tb_bit_clks) @(posedge clk);
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
        if ($fscanf(32'h8000_0000, "%x", arg_a) == 1) begin
          ena   = arg_a[0];
          ui_in[0] = 1'b1;
          rst_n = 1'b0;
          uart_mon_seen  = 1'b0;
          i2c_seen       = 1'b0;
          i2c_od_fault   = 1'b0;
          i2c_sda_low    = 1'b0;
          i2c_scl_low    = 1'b0;
          i2c_st_armed   = 1'b0;
          st_wren        = 1'b0;
          st_prev_cmd    = 8'h00;
          st_cs_high_cnt = 100;
          ms_wren        = 1'b0;
          ms_sleep       = 1'b0;
          ms_woke_flag   = 1'b0;
          ms_prog_done   = 1'b0;
          ms_err         = 1'b0;
          ms_read_glitch = 1'b0;
          ms_prev_cmd    = 8'h00;
          ms_cs_high_cnt = 100;
          ms_last_cs_gap = 100;
          cur_tx_delay_ns = spi_delay_ns;
          spi_mosi_rx    = 8'h00;
          spi_miso_drv   = spi_resp_byte[7];
          repeat (6) @(posedge clk);
          rst_n = 1'b1;
          repeat (4) @(posedge clk);
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "s") begin
        if ($fscanf(32'h8000_0000, "%x", arg_a) == 1) begin
          tb_lfsr = (arg_a == 0) ? 32'hCAFE_BEEF : arg_a[31:0];
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "b") begin
        // Set ui_in[2:1] baud selector (0..3) and matching host UART divider
        if ($fscanf(32'h8000_0000, "%d", arg_a) == 1) begin
          ui_in[2:1] = arg_a[1:0];
          case (arg_a[1:0])
            2'd1: tb_bit_clks = 104;
            2'd2: tb_bit_clks = 217;
            2'd3: tb_bit_clks = 434;
            default: tb_bit_clks = CLKS_PER_BIT;
          endcase
          repeat (2) @(posedge clk);
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "c") begin
        if ($fscanf(32'h8000_0000, "%d %d %x %d", arg_a, arg_b, arg_c, arg_d) == 4) begin
          target_mode    = arg_a;
          spi_delay_ns   = arg_b;
          spi_resp_byte  = arg_c[7:0];
          i2c_do_ack     = arg_d[0];
          spi_jitter_ns  = (arg_d > 0) ? arg_d : 0;
          cur_tx_delay_ns = spi_delay_ns;
          spi_miso_drv   = ((target_mode == MODE_SPI) || (target_mode == MODE_SPI_NOISY)) ? spi_resp_byte[7] : 1'b0;
          uart_mon_seen  = 1'b0;
          uart_mon_byte  = 8'h00;
          i2c_seen       = 1'b0;
          i2c_od_fault   = 1'b0;
          i2c_scl_low    = 1'b0;
          i2c_st_armed   = 1'b0;
          st_wren        = 1'b0;
          st_prev_cmd    = 8'h00;
          st_cs_high_cnt = 100;
          ms_wren        = 1'b0;
          ms_sleep       = 1'b0;
          ms_woke_flag   = 1'b0;
          ms_prog_done   = 1'b0;
          ms_err         = 1'b0;
          ms_read_glitch = 1'b0;
          ms_prev_cmd    = 8'h00;
          ms_cs_high_cnt = 100;
          ms_last_cs_gap = 100;
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "f") begin
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
            if (all_ok) begin
              repeat ((tb_bit_clks / 2) + 2) @(posedge clk);
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
