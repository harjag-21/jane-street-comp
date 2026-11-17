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
//   7 = Held-out Multi-bug stateful SPI (`delay_ns`: 0=Ref, 1=DUT, D1..D4 + held-out D5)
//   8 = Held-out Shape Suite (`delay_ns`: 0=Ref, 1=DUT, 4 distinct failure-shape bugs H1..H4)
//   9 = Independently parameterized 4-slot SPI target configured via stdin `p`/`n`
//   10 = 2D timing-surface SPI target (`delay_ns` = surface_id 0..6, measures SCLK low X & CS high Y)
module sim_explorer_tb;
  localparam integer CLKS_PER_BIT       = 8;
  localparam integer MODE_SPI           = 0;
  localparam integer MODE_UART          = 1;
  localparam integer MODE_I2C           = 2;
  localparam integer MODE_SPI_STATEFUL  = 3;
  localparam integer MODE_I2C_STRETCH   = 4;
  localparam integer MODE_SPI_MULTISPEC = 5;
  localparam integer MODE_SPI_NOISY     = 6;
  localparam integer MODE_SPI_HELD      = 7;
  localparam integer MODE_SPI_SHAPES    = 8;
  localparam integer MODE_SPI_PARAM     = 9;
  localparam integer MODE_SPI_SURFACE   = 10;
  localparam integer MODE_JTAG_TAP      = 11;

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
  reg        ms_cfg_glitch  = 1'b0;
  reg  [7:0] ms_prev_cmd    = 8'h00;
  reg  [7:0] ms_tx_byte     = 8'h00;
  reg  [7:0] ms_rx_byte     = 8'h00;
  reg  [3:0] ms_bit_idx     = 4'd0;
  integer    ms_cs_high_cnt = 100;
  integer    ms_last_cs_gap = 100;

  // Held-out failure-shape target state (MODE_SPI_SHAPES)
  reg        hs_step1       = 1'b0;
  reg        hs_step2       = 1'b0;
  reg  [7:0] hs_latched_err = 8'h00;
  reg  [7:0] hs_prev_cmd    = 8'h00;
  reg  [7:0] hs_tx_byte     = 8'h00;
  reg  [7:0] hs_rx_byte     = 8'h00;
  reg  [3:0] hs_bit_idx     = 4'd0;
  integer    hs_cs_high_cnt = 100;
  integer    hs_last_gap    = 100;

  // Parameterized 4-slot SPI target state (MODE_SPI_PARAM)
  reg  [7:0] pt_op0       [0:3];
  reg  [7:0] pt_op1       [0:3];
  integer    pt_lo        [0:3];
  integer    pt_hi        [0:3];
  reg  [7:0] pt_err       [0:3];
  reg  [7:0] pt_norm_op   [0:3];
  reg  [7:0] pt_latched_err = 8'h00;
  reg  [7:0] pt_prev_cmd    = 8'h00;
  reg  [7:0] pt_tx_byte     = 8'h00;
  reg  [7:0] pt_rx_byte     = 8'h00;
  reg  [3:0] pt_bit_idx     = 4'd0;
  integer    pt_cs_high_cnt = 100;
  integer    pt_last_gap    = 100;

  // 2D surface target state (MODE_SPI_SURFACE)
  reg        sf_in_cmd1     = 1'b0;
  reg  [7:0] sf_tx_byte     = 8'h00;
  reg  [3:0] sf_bit_idx     = 4'd0;
  integer    sf_cs_high_cnt = 100;
  integer    sf_low_cnt     = 0;
  integer    sf_x_latched   = 4;
  integer    sf_y_latched   = 4;
  reg        sf_prev_sclk   = 1'b0;

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

  // Separately instantiated IEEE 1149.1 JTAG TAP controller (MODE_JTAG_TAP = 11)
  wire jtag_tck = (target_mode == MODE_JTAG_TAP) && uio_oe[0] ? uio_out[0] : 1'b0;
  wire jtag_tms = (target_mode == MODE_JTAG_TAP) && uio_oe[1] ? uio_out[1] : 1'b1;
  wire jtag_tdi = (target_mode == MODE_JTAG_TAP) && uio_oe[2] ? uio_out[2] : 1'b0;
  wire jtag_tdo_drv;
  wire [3:0] jtag_state, jtag_ir;
  wire [15:0] jtag_tlr_cnt, jtag_uir_cnt, jtag_udr_cnt;

  jtag_tap_model #(
      .IDCODE_VALUE(32'h1A2B4D6F)
  ) u_jtag_tap (
      .tck              (jtag_tck),
      .tms              (jtag_tms),
      .tdi              (jtag_tdi),
      .tdo_delay_ns     (spi_delay_ns),
      .tdo              (jtag_tdo_drv),
      .tap_state_o      (jtag_state),
      .ir_o             (jtag_ir),
      .tlr_entries_o    (jtag_tlr_cnt),
      .update_ir_count_o(jtag_uir_cnt),
      .update_dr_count_o(jtag_udr_cnt)
  );

  always @(*) begin
    if ((target_mode == MODE_SPI) || (target_mode == MODE_SPI_STATEFUL) ||
        (target_mode == MODE_SPI_MULTISPEC) || (target_mode == MODE_SPI_NOISY) ||
        (target_mode == MODE_SPI_HELD) || (target_mode == MODE_SPI_SHAPES) ||
        (target_mode == MODE_SPI_PARAM) || (target_mode == MODE_SPI_SURFACE)) begin
      uio_in = {4'hF, spi_miso_drv, 3'b111};
    end else if ((target_mode == MODE_I2C) || (target_mode == MODE_I2C_STRETCH)) begin
      uio_in = {6'h3F, sda_bus, scl_bus};
    end else if (target_mode == MODE_JTAG_TAP) begin
      uio_in = {4'hF, jtag_tdo_drv, 3'b111};
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

  // MODE_SPI_MULTISPEC & MODE_SPI_HELD (`spi_delay_ns`: 0=Reference, !=0=Buggy DUT)
  wire is_multispec_mode = (target_mode == MODE_SPI_MULTISPEC) || (target_mode == MODE_SPI_HELD);

  always @(posedge clk) begin
    if (is_multispec_mode) begin
      if (spi_cs_n) begin
        ms_cs_high_cnt <= ms_cs_high_cnt + 1;
      end else begin
        ms_cs_high_cnt <= 0;
      end
    end
  end

  always @(negedge spi_cs_n) begin : multispec_cs_blk
    reg is_dut;
    if (is_multispec_mode) begin
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
      end else if (ms_cfg_glitch) begin
        ms_tx_byte = 8'h66;
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
    if (is_multispec_mode && !spi_cs_n) begin
      next_rx    = {ms_rx_byte[6:0], uio_out[2]};
      ms_rx_byte = next_rx;
      if (ms_bit_idx == 4'd7) begin
        is_dut      = (spi_delay_ns != 0);
        spi_mosi_rx = next_rx;
        ms_woke_flag   = 1'b0;
        ms_read_glitch = 1'b0;
        ms_cfg_glitch  = 1'b0;

        // D4 (fast_read_corrupt): 0x9F followed quickly (cs_high <= 4 / gap_wait<=1) by 0x35 corrupts read
        if (!ms_sleep && is_dut && (ms_prev_cmd == 8'h9F) && (next_rx == 8'h35) && (ms_last_cs_gap <= 4)) begin
          ms_read_glitch = 1'b1;
        end
        // Held-out D5 (cfg_rd_wrdi_race): 0x35 followed by 0x04 in interior window (6<=cs_high<=8 / gap_wait 3..5) latches glitch
        if ((target_mode == MODE_SPI_HELD) && !ms_sleep && is_dut &&
            (ms_prev_cmd == 8'h35) && (next_rx == 8'h04) &&
            (ms_last_cs_gap >= 6) && (ms_last_cs_gap <= 8)) begin
          ms_cfg_glitch = 1'b1;
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
    if (is_multispec_mode && !spi_cs_n) begin
      if (ms_bit_idx < 4'd7) begin
        ms_bit_idx   = ms_bit_idx + 4'd1;
        spi_miso_drv <= ms_tx_byte[7 - ms_bit_idx];
      end
    end
  end

  // MODE_SPI_SHAPES: held-out behavioral suite with distinct timing-failure shapes H1..H4
  always @(posedge clk) begin
    if (target_mode == MODE_SPI_SHAPES) begin
      if (spi_cs_n) begin
        hs_cs_high_cnt <= hs_cs_high_cnt + 1;
      end else begin
        hs_cs_high_cnt <= 0;
      end
    end
  end

  always @(negedge spi_cs_n) begin : shapes_cs_blk
    reg is_dut;
    if (target_mode == MODE_SPI_SHAPES) begin
      is_dut = (spi_delay_ns != 0);
      hs_last_gap = hs_cs_high_cnt;
      if (is_dut && (hs_latched_err != 8'h00)) begin
        hs_tx_byte = hs_latched_err;
      end else if (hs_step2) begin
        hs_tx_byte = 8'h5A;
      end else if (hs_prev_cmd == 8'h9F) begin
        hs_tx_byte = 8'hA5;
      end else if (hs_prev_cmd == 8'h06) begin
        hs_tx_byte = 8'h02;
      end else begin
        hs_tx_byte = 8'h00;
      end
      spi_miso_drv <= hs_tx_byte[7];
      hs_bit_idx   = 4'd0;
      hs_rx_byte   = 8'h00;
    end
  end

  always @(posedge spi_sclk) begin : shapes_rx_blk
    reg [7:0] next_rx;
    reg       is_dut;
    if ((target_mode == MODE_SPI_SHAPES) && !spi_cs_n) begin
      next_rx    = {hs_rx_byte[6:0], uio_out[2]};
      hs_rx_byte = next_rx;
      if (hs_bit_idx == 4'd7) begin
        is_dut         = (spi_delay_ns != 0);
        spi_mosi_rx    = next_rx;
        hs_latched_err = 8'h00;
        hs_step2       = 1'b0;

        // H1 (interior notch): 0xAB -> 0x02 at gap_wait in {8,9} (hs_last_gap in {11,12})
        if (is_dut && (hs_prev_cmd == 8'hAB) && (next_rx == 8'h02) &&
            (hs_last_gap >= 11) && (hs_last_gap <= 12)) begin
          hs_latched_err = 8'h33;
        end
        // H2 (bimodal disconnected bands): 0x04 -> 0x9F at gap_wait in {2,3,12,13} (hs_last_gap 5..6 or 15..16)
        if (is_dut && (hs_prev_cmd == 8'h04) && (next_rx == 8'h9F) &&
            (((hs_last_gap >= 5) && (hs_last_gap <= 6)) || ((hs_last_gap >= 15) && (hs_last_gap <= 16)))) begin
          hs_latched_err = 8'h77;
        end
        // H3 (3-cmd cascade): 0xB9 (quiet) -> 0x35; DUT faults when gap_wait in {4,5,6} (hs_last_gap 7..9)
        if (hs_step1 && (next_rx == 8'h35)) begin
          hs_step2 = 1'b1;
          if (is_dut && (hs_last_gap >= 7) && (hs_last_gap <= 9)) begin
            hs_latched_err = 8'hE1;
          end
        end
        // H4 (upper watchdog tail): 0x05 -> 0x02 at gap_wait in {14,15} (hs_last_gap >= 17)
        if (is_dut && (hs_prev_cmd == 8'h05) && (next_rx == 8'h02) && (hs_last_gap >= 17)) begin
          hs_latched_err = 8'hC4;
        end

        if (next_rx == 8'hB9) begin
          hs_step1 = 1'b1;
        end else if (next_rx != 8'h35) begin
          hs_step1 = 1'b0;
        end
        hs_prev_cmd = next_rx;
      end
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI_SHAPES) && !spi_cs_n) begin
      if (hs_bit_idx < 4'd7) begin
        hs_bit_idx   = hs_bit_idx + 4'd1;
        spi_miso_drv <= hs_tx_byte[7 - hs_bit_idx];
      end
    end
  end

  // MODE_SPI_PARAM: independently parameterized 4-slot SPI target
  always @(posedge clk) begin
    if (target_mode == MODE_SPI_PARAM) begin
      if (spi_cs_n) begin
        pt_cs_high_cnt <= pt_cs_high_cnt + 1;
      end else begin
        pt_cs_high_cnt <= 0;
      end
    end
  end

  always @(negedge spi_cs_n) begin : param_cs_blk
    reg is_dut;
    if (target_mode == MODE_SPI_PARAM) begin
      is_dut = (spi_delay_ns != 0);
      pt_last_gap = pt_cs_high_cnt;
      if (is_dut && (pt_latched_err != 8'h00)) begin
        pt_tx_byte = pt_latched_err;
      end else if ((pt_prev_cmd != 8'h00) && (pt_prev_cmd == pt_norm_op[0])) begin
        pt_tx_byte = 8'h11;
      end else if ((pt_prev_cmd != 8'h00) && (pt_prev_cmd == pt_norm_op[1])) begin
        pt_tx_byte = 8'h22;
      end else if ((pt_prev_cmd != 8'h00) && (pt_prev_cmd == pt_norm_op[2])) begin
        pt_tx_byte = 8'h33;
      end else if ((pt_prev_cmd != 8'h00) && (pt_prev_cmd == pt_norm_op[3])) begin
        pt_tx_byte = 8'h44;
      end else begin
        pt_tx_byte = 8'h00;
      end
      spi_miso_drv <= pt_tx_byte[7];
      pt_bit_idx   = 4'd0;
      pt_rx_byte   = 8'h00;
    end
  end

  always @(posedge spi_sclk) begin : param_rx_blk
    reg [7:0] next_rx;
    reg       is_dut;
    integer   sk;
    if ((target_mode == MODE_SPI_PARAM) && !spi_cs_n) begin
      next_rx    = {pt_rx_byte[6:0], uio_out[2]};
      pt_rx_byte = next_rx;
      if (pt_bit_idx == 4'd7) begin
        is_dut         = (spi_delay_ns != 0);
        spi_mosi_rx    = next_rx;
        pt_latched_err = 8'h00;
        for (sk = 0; sk < 4; sk = sk + 1) begin
          if (is_dut && (pt_err[sk] != 8'h00) &&
              (pt_prev_cmd == pt_op0[sk]) && (next_rx == pt_op1[sk]) &&
              (pt_last_gap >= pt_lo[sk]) && (pt_last_gap <= pt_hi[sk])) begin
            pt_latched_err = pt_err[sk];
          end
        end
        pt_prev_cmd = next_rx;
      end
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI_PARAM) && !spi_cs_n) begin
      if (pt_bit_idx < 4'd7) begin
        pt_bit_idx   = pt_bit_idx + 4'd1;
        spi_miso_drv <= pt_tx_byte[7 - pt_bit_idx];
      end
    end
  end

  // MODE_SPI_SURFACE: measures X=sclk_low_cycles and Y=gap_wait directly from SPI wires
  always @(posedge clk) begin
    if (target_mode == MODE_SPI_SURFACE) begin
      if (spi_cs_n) begin
        sf_cs_high_cnt <= sf_cs_high_cnt + 1;
        sf_low_cnt     <= 0;
      end else begin
        sf_cs_high_cnt <= 0;
        if (spi_sclk && !sf_prev_sclk) begin
          // Capture SCLK low duration measured prior to bit 6..0 rising edge
          if (sf_bit_idx >= 4'd1) begin
            sf_x_latched <= sf_low_cnt;
          end
          sf_low_cnt <= 0;
        end else if (!spi_sclk) begin
          sf_low_cnt <= sf_low_cnt + 1;
        end else begin
          sf_low_cnt <= 0;
        end
      end
      sf_prev_sclk <= spi_sclk;
    end
  end

  always @(negedge spi_cs_n) begin : surface_cs_blk
    integer xx, yy, sid;
    if (target_mode == MODE_SPI_SURFACE) begin
      sf_bit_idx = 4'd0;
      if (!sf_in_cmd1) begin
        sf_tx_byte   = 8'h00;
        sf_in_cmd1   = 1'b1;
      end else begin
        xx  = sf_x_latched;
        yy  = sf_cs_high_cnt - 3;
        sf_y_latched = yy;
        sid = spi_delay_ns;
        sf_tx_byte = 8'hAA;
        case (sid)
          0: begin
            if (xx <= 3) sf_tx_byte = 8'h55;
            else if (yy >= 13) sf_tx_byte = 8'hC4;
            else if ((xx >= 7) && (xx <= 8) && (yy >= 6) && (yy <= 7)) sf_tx_byte = 8'hEE;
          end
          1: begin
            if (xx <= 2) sf_tx_byte = 8'h55;
            else if ((xx >= 4) && (xx <= 6) && (yy >= 2) && (yy <= 3)) sf_tx_byte = 8'h77;
            else if ((xx >= 9) && (xx <= 11) && (yy >= 11) && (yy <= 12)) sf_tx_byte = 8'h88;
          end
          2: begin
            if (yy >= 12) sf_tx_byte = 8'hC4;
            else if ((2 * xx + yy) < 16) sf_tx_byte = 8'h00;
            else if ((xx >= 9) && (xx <= 10) && (yy >= 5) && (yy <= 6)) sf_tx_byte = 8'hEE;
          end
          3: begin
            if (xx <= 2) sf_tx_byte = 8'h55;
            else if ((xx >= 5) && (xx <= 11) && (xx >= (yy + 1)) && (xx <= (yy + 2))) sf_tx_byte = 8'hEE;
            else if (yy <= 1) sf_tx_byte = 8'h00;
          end
          4: begin
            if (yy <= 1) sf_tx_byte = 8'h00;
            else if ((xx >= 4) && (xx <= 5) && (yy >= 11) && (yy <= 12)) sf_tx_byte = 8'h33;
            else if ((xx >= 10) && (xx <= 11) && (yy >= 4) && (yy <= 5)) sf_tx_byte = 8'h44;
          end
          5: begin
            if ((xx >= 8) && (xx <= 9) && (yy >= 8) && (yy <= 9)) sf_tx_byte = 8'hEE;
            else if ((xx <= 5) && (yy <= 6)) sf_tx_byte = 8'h11;
            else if ((xx >= 9) && (yy >= 11)) sf_tx_byte = 8'h22;
          end
          6: begin
            if (xx <= 3) sf_tx_byte = 8'h55;
            else if ((xx >= 6) && (xx <= 9) &&
                     (((yy >= 1) && (yy <= 2)) || ((yy >= 6) && (yy <= 7)) || ((yy >= 11) && (yy <= 12))))
              sf_tx_byte = 8'h66;
          end
          7: begin // S7_new_odd_pinholes
            if (yy == 0) sf_tx_byte = 8'h00;
            else if ((xx >= 5) && (xx <= 6) && (yy >= 3) && (yy <= 4)) sf_tx_byte = 8'h33;
            else if ((xx >= 9) && (xx <= 10) && (yy >= 9) && (yy <= 10)) sf_tx_byte = 8'h44;
          end
          8: begin // S8_new_curved_circle
            if (xx <= 3) sf_tx_byte = 8'h55;
            else if (((xx - 8) * (xx - 8) + (yy - 8) * (yy - 8)) <= 9) sf_tx_byte = 8'hEE;
          end
          9: begin // S9_new_lshape_and_core
            if ((xx <= 5) && (yy >= 9)) sf_tx_byte = 8'h77;
            else if ((xx >= 9) && (yy <= 5)) sf_tx_byte = 8'h88;
            else if ((xx >= 6) && (xx <= 7) && (yy >= 6) && (yy <= 7)) sf_tx_byte = 8'hEE;
          end
          10: begin // S10_new_antidiag_ridge
            if (xx <= 2) sf_tx_byte = 8'h55;
            else if ((xx >= 4) && (xx <= 11) && ((xx + yy) >= 14) && ((xx + yy) <= 16)) sf_tx_byte = 8'hC4;
          end
          11: begin // S11_new_3band_step
            if ((xx <= 4) && (yy <= 4)) sf_tx_byte = 8'h11;
            else if ((xx >= 7) && (yy >= 5) && (yy <= 7)) sf_tx_byte = 8'h33;
            else if (yy >= 13) sf_tx_byte = 8'h22;
          end
          12: begin // S12_new_tri_islands
            if (yy == 15) sf_tx_byte = 8'hC4;
            else if ((xx >= 3) && (xx <= 4) && (yy >= 11) && (yy <= 13)) sf_tx_byte = 8'h33;
            else if ((xx >= 10) && (xx <= 12) && (yy >= 10) && (yy <= 11)) sf_tx_byte = 8'h44;
            else if ((xx >= 6) && (xx <= 7) && (yy >= 2) && (yy <= 4)) sf_tx_byte = 8'h66;
          end
          default: sf_tx_byte = 8'hAA;
        endcase
      end
      spi_miso_drv <= sf_tx_byte[7];
    end
  end

  always @(posedge spi_sclk) begin
    if ((target_mode == MODE_SPI_SURFACE) && !spi_cs_n) begin
      spi_mosi_rx = {spi_mosi_rx[6:0], uio_out[2]};
    end
  end

  always @(negedge spi_sclk) begin
    if ((target_mode == MODE_SPI_SURFACE) && !spi_cs_n) begin
      if (sf_bit_idx < 4'd7) begin
        sf_bit_idx   = sf_bit_idx + 4'd1;
        spi_miso_drv <= sf_tx_byte[7 - sf_bit_idx];
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
  integer arg_a, arg_b, arg_c, arg_d, arg_e, arg_f;
  integer i;
  reg [7:0] tx_buf [0:7];
  reg [7:0] rx_buf [0:7];
  reg       rx_ok;
  reg       all_ok;

  initial begin
    for (i = 0; i < 4; i = i + 1) begin
      pt_op0[i]     = 8'h00;
      pt_op1[i]     = 8'h00;
      pt_lo[i]      = 0;
      pt_hi[i]      = 0;
      pt_err[i]     = 8'h00;
      pt_norm_op[i] = 8'h00;
    end
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
          ms_cfg_glitch  = 1'b0;
          ms_prev_cmd    = 8'h00;
          ms_cs_high_cnt = 100;
          ms_last_cs_gap = 100;
          hs_step1       = 1'b0;
          hs_step2       = 1'b0;
          hs_latched_err = 8'h00;
          hs_prev_cmd    = 8'h00;
          hs_cs_high_cnt = 100;
          hs_last_gap    = 100;
          pt_latched_err = 8'h00;
          pt_prev_cmd    = 8'h00;
          pt_cs_high_cnt = 100;
          pt_last_gap    = 100;
          sf_in_cmd1     = 1'b0;
          sf_tx_byte     = 8'h00;
          sf_bit_idx     = 4'd0;
          sf_cs_high_cnt = 100;
          sf_low_cnt     = 0;
          sf_x_latched   = 4;
          sf_y_latched   = 4;
          sf_prev_sclk   = 1'b0;
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
          ms_cfg_glitch  = 1'b0;
          ms_prev_cmd    = 8'h00;
          ms_cs_high_cnt = 100;
          ms_last_cs_gap = 100;
          hs_step1       = 1'b0;
          hs_step2       = 1'b0;
          hs_latched_err = 8'h00;
          hs_prev_cmd    = 8'h00;
          hs_cs_high_cnt = 100;
          hs_last_gap    = 100;
          pt_latched_err = 8'h00;
          pt_prev_cmd    = 8'h00;
          pt_cs_high_cnt = 100;
          pt_last_gap    = 100;
          sf_in_cmd1     = 1'b0;
          sf_tx_byte     = 8'h00;
          sf_bit_idx     = 4'd0;
          sf_cs_high_cnt = 100;
          sf_low_cnt     = 0;
          sf_x_latched   = 4;
          sf_y_latched   = 4;
          sf_prev_sclk   = 1'b0;
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "p") begin
        if ($fscanf(32'h8000_0000, "%d %x %x %d %d %x", arg_a, arg_b, arg_c, arg_d, arg_e, arg_f) == 6) begin
          pt_op0[arg_a[1:0]] = arg_b[7:0];
          pt_op1[arg_a[1:0]] = arg_c[7:0];
          pt_lo[arg_a[1:0]]  = arg_d;
          pt_hi[arg_a[1:0]]  = arg_e;
          pt_err[arg_a[1:0]] = arg_f[7:0];
          $display("OK");
          $fflush(32'h8000_0001);
        end
      end else if (cmd_ch == "n") begin
        if ($fscanf(32'h8000_0000, "%x %x %x %x", arg_a, arg_b, arg_c, arg_d) == 4) begin
          pt_norm_op[0] = arg_a[7:0];
          pt_norm_op[1] = arg_b[7:0];
          pt_norm_op[2] = arg_c[7:0];
          pt_norm_op[3] = arg_d[7:0];
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
      end else if (cmd_ch == "x") begin
        if ($fscanf(32'h8000_0000, "%d", arg_a) == 1) begin
          arg_b = 0;
          while ((arg_b < arg_a) && (uo_out[7] != 1'b1)) begin
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
