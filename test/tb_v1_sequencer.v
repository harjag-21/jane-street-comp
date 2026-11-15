`timescale 1ns/1ps
`default_nettype none

module tb_v1_sequencer;
  reg clk = 0;
  reg rst_n = 0;
  reg prog_we = 0;
  reg [5:0] prog_addr = 0;
  reg [15:0] prog_wdata = 0;
  reg prog_valid = 0;
  reg cmd_start = 0;
  reg [11:0] cmd_param = 0;
  reg cmd_halt = 0;
  reg event_pop = 0;

  wire [3:0] event_count;
  wire event_overflow;
  wire [7:0] event_head_tag;
  wire [15:0] event_head_ts;
  wire [1:0] eng_state;
  wire [5:0] pc;
  wire last_sample;
  wire [7:0] sample_reg;
  wire [15:0] cycle_cnt;
  wire [7:0] uio_out, uio_oe;
  wire trial_active;

  // Behavioral bidirectional pin bus with weak pull-ups for open-drain I2C.
  reg [7:0] target_drive_en = 8'h00;
  reg [7:0] target_drive_val = 8'h00;
  wire [7:0] bus_pins;
  genvar gi;
  generate
    for (gi = 0; gi < 8; gi = gi + 1) begin : g_pins
      assign bus_pins[gi] = uio_oe[gi] ? uio_out[gi] :
                            target_drive_en[gi] ? target_drive_val[gi] : 1'b1;
    end
  endgenerate

  protocol_sequencer dut (
    .clk(clk),
    .rst_n(rst_n),
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
    .uio_in(bus_pins),
    .uio_out_o(uio_out),
    .uio_oe_o(uio_oe),
    .trial_active_o(trial_active)
  );

  always #5 clk = ~clk;

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
                   OP_WAIT_PARAM = 4'hC;

  function [15:0] insn;
    input [3:0] op;
    input [11:0] imm;
    begin insn = {op, imm}; end
  endfunction

  integer errors;
  integer a;

  task check;
    input cond;
    input [8*80-1:0] msg;
    begin
      if (!cond) begin
        $display("FAIL: %0s (time=%0t)", msg, $time);
        errors = errors + 1;
      end
    end
  endtask

  task load_word;
    input [5:0] addr;
    input [15:0] word;
    begin
      @(negedge clk);
      prog_we = 1; prog_addr = addr; prog_wdata = word;
      @(posedge clk);
      #1;
      prog_we = 0;
    end
  endtask

  task clear_imem;
    integer k;
    begin
      prog_valid = 0;
      for (k = 0; k < 64; k = k + 1) load_word(k[5:0], 16'h0000);
    end
  endtask

  task start_and_wait;
    input [11:0] param;
    integer guard;
    begin
      @(negedge clk);
      cmd_param = param;
      cmd_start = 1;
      @(posedge clk);
      #1;
      cmd_start = 0;
      guard = 0;
      while (eng_state == 2'd1 && guard < 2000) begin
        @(posedge clk);
        #1;
        guard = guard + 1;
      end
      check(guard < 2000, "sequencer finished before watchdog");
    end
  endtask

  task pop_event;
    output [7:0] tag;
    output [15:0] ts;
    begin
      tag = event_head_tag;
      ts = event_head_ts;
      @(negedge clk);
      event_pop = 1;
      @(posedge clk);
      #1;
      event_pop = 0;
    end
  endtask

  // ---- Test 1: ISA coverage (counters, sample shift/no-shift, FIFO overflow, illegal op) ----
  task test_isa_and_faults;
    reg [7:0] t;
    reg [15:0] ts;
    integer i;
    begin
      clear_imem;
      // Verify start is blocked when prog_valid == 0.
      @(negedge clk); cmd_start = 1; @(posedge clk); #1; cmd_start = 0;
      check(eng_state == 2'd0, "cmd_start ignored when prog_valid=0");

      // Load small test program.
      a = 0;
      load_word(a[5:0], insn(OP_SETCOUNT, 12'h003)); a = a + 1; // 0: cnt0 = 3
      load_word(a[5:0], insn(OP_MARK, 12'h011));     a = a + 1; // 1: mark 0x11 (3 times)
      load_word(a[5:0], insn(OP_LOOP, 12'h001));     a = a + 1; // 2: loop cnt0 -> 1
      load_word(a[5:0], insn(OP_SETCOUNT, 12'h100)); a = a + 1; // 3: cnt1 = 0 (must fall through!)
      load_word(a[5:0], insn(OP_MARK, 12'h022));     a = a + 1; // 4: mark 0x22 (1 time)
      load_word(a[5:0], insn(OP_LOOP, 12'h104));     a = a + 1; // 5: loop cnt1 -> 4 (falls through)
      // Sample pin 2 = 1 twice, pin 3 = 0 once, then no_shift sample pin 3 = 0 -> sample_reg = 3'b110 = 6
      load_word(a[5:0], insn(OP_SAMPLE, 12'h002));   a = a + 1; // 6: shift pin2 (1)
      load_word(a[5:0], insn(OP_SAMPLE, 12'h002));   a = a + 1; // 7: shift pin2 (1)
      load_word(a[5:0], insn(OP_SAMPLE, 12'h003));   a = a + 1; // 8: shift pin3 (0)
      load_word(a[5:0], insn(OP_SAMPLE, 12'h802));   a = a + 1; // 9: no-shift pin2 (1)
      load_word(a[5:0], insn(OP_JZ, 12'h00C));       a = a + 1; // 10: last_sample==1 -> not taken
      load_word(a[5:0], insn(OP_MARK, 12'h100));     a = a + 1; // 11: mark sample_reg (0x06)
      load_word(a[5:0], insn(OP_SAMPLE, 12'h803));   a = a + 1; // 12: no-shift pin3 (0)
      load_word(a[5:0], insn(OP_JZ, 12'h00F));       a = a + 1; // 13: last_sample==0 -> jump 15
      load_word(a[5:0], insn(OP_MARK, 12'h0EE));     a = a + 1; // 14: skipped
      load_word(a[5:0], insn(OP_HALT, 12'h000));     a = a + 1; // 15: halt

      target_drive_en = 8'b0000_1100;
      target_drive_val = 8'b0000_0100; // pin2=1, pin3=0
      prog_valid = 1;
      start_and_wait(12'd0);

      check(eng_state == 2'd2, "ISA test halted cleanly");
      check(sample_reg == 8'h06, "SAMPLE shift and no_shift preserved 0x06");
      check(event_overflow == 1'b0, "no overflow for 5 marks");
      check(event_count == 4'd5, "5 events captured");

      pop_event(t, ts); check(t == 8'h11, "ev0 is 0x11");
      pop_event(t, ts); check(t == 8'h11, "ev1 is 0x11");
      pop_event(t, ts); check(t == 8'h11, "ev2 is 0x11");
      pop_event(t, ts); check(t == 8'h22, "ev3 is 0x22 (cnt=0 did not underflow)");
      pop_event(t, ts); check(t == 8'h06, "ev4 is sample_reg 0x06");
      check(event_count == 4'd0, "FIFO drained");

      // Test FIFO overflow (>8 events) and illegal opcode fault clearing OE.
      clear_imem;
      a = 0;
      load_word(a[5:0], insn(OP_OE, 12'h0FF));       a = a + 1;
      load_word(a[5:0], insn(OP_OUT, 12'h0A5));      a = a + 1;
      load_word(a[5:0], insn(OP_SETCOUNT, 12'h00A)); a = a + 1; // 10 marks
      load_word(a[5:0], insn(OP_MARK, 12'h05A));     a = a + 1;
      load_word(a[5:0], insn(OP_LOOP, 12'h003));     a = a + 1;
      load_word(a[5:0], insn(OP_HALT, 12'h001));     a = a + 1; // keep_oe=1
      prog_valid = 1;
      start_and_wait(12'd0);
      check(event_count == 4'd8 && event_overflow == 1'b1, "FIFO capped at 8 and set overflow");
      check(uio_oe == 8'hFF && uio_out == 8'hA5, "HALT keep_oe=1 preserved outputs");

      // Illegal opcode 0xD clears OE and sets ENG_FAULT.
      load_word(6'd5, insn(4'hD, 12'h000));
      start_and_wait(12'd0);
      check(eng_state == 2'd3, "illegal opcode entered ENG_FAULT");
      check(uio_oe == 8'h00, "illegal opcode disabled uio_oe");
      target_drive_en = 8'h00;
    end
  endtask

  // ---- Test 2: Loaded UART 8N1 TX program on uio[0] (byte 0xA3, bit period 8 cycles) ----
  task test_uart_tx_program;
    reg [7:0] tx_val;
    reg [7:0] rx_byte;
    integer b;
    begin
      tx_val = 8'hA3;
      clear_imem;
      a = 0;
      // Set uio[0] idle high, OE=1, hold 8 cycles
      load_word(a[5:0], insn(OP_OUT, 12'h001));  a = a + 1;
      load_word(a[5:0], insn(OP_OE,  12'h001));  a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd6));   a = a + 1;
      // Start bit (0) for 8 cycles: OUT(1) + WAIT 6 (7) = 8 cycles
      load_word(a[5:0], insn(OP_OUT, 12'h000));  a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd6));   a = a + 1;
      // 8 data bits LSB first
      for (b = 0; b < 8; b = b + 1) begin
        load_word(a[5:0], insn(OP_OUT, {11'd0, tx_val[b]})); a = a + 1;
        load_word(a[5:0], insn(OP_WAIT, 12'd6));             a = a + 1;
      end
      // Stop bit (1) for 8 cycles + halt
      load_word(a[5:0], insn(OP_OUT, 12'h001));  a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd6));   a = a + 1;
      load_word(a[5:0], insn(OP_HALT, 12'h000)); a = a + 1;

      prog_valid = 1;
      @(negedge clk);
      cmd_param = 12'd0;
      cmd_start = 1;
      @(posedge clk); #1;
      cmd_start = 0;

      // Behavioral UART receiver on bus_pins[0]
      @(negedge bus_pins[0]);
      repeat (4) @(posedge clk); #1;
      check(bus_pins[0] == 1'b0, "UART start bit valid at mid-bit");
      for (b = 0; b < 8; b = b + 1) begin
        repeat (8) @(posedge clk); #1;
        rx_byte[b] = bus_pins[0];
      end
      repeat (8) @(posedge clk); #1;
      check(bus_pins[0] == 1'b1, "UART stop bit valid");
      check(rx_byte == 8'hA3, "UART TX program emitted exact 0xA3");

      while (eng_state == 2'd1) @(posedge clk);
      #1;
      check(eng_state == 2'd2, "UART TX program halted");
    end
  endtask

  // ---- Test 3: Loaded SPI Mode-0 Timing-Boundary Sweep (MOSI=0x55, MISO=0xAA, d=23ns, s=2ns) ----
  // Pins: uio[0]=CS_N, uio[1]=SCLK, uio[2]=MOSI, uio[3]=MISO (input)
  reg spi_mon_active = 0;
  reg [7:0] spi_mon_mosi = 0;
  integer spi_mon_bits = 0;
  integer spi_tgt_bit = 6;
  always @(posedge bus_pins[1]) begin
    if (spi_mon_active && bus_pins[0] == 1'b0) begin
      spi_mon_mosi = {spi_mon_mosi[6:0], bus_pins[2]};
      spi_mon_bits = spi_mon_bits + 1;
    end
  end
  always @(negedge bus_pins[1]) begin
    if (spi_mon_active && bus_pins[0] == 1'b0 && spi_tgt_bit >= 0) begin
      target_drive_val[3] <= #23 (8'hAA >> spi_tgt_bit) & 1'b1;
      spi_tgt_bit = spi_tgt_bit - 1;
    end
  end

  task run_spi_trial;
    input [11:0] low_cycles; // >= 2
    input [7:0] mosi_byte;
    output [7:0] got_miso;
    integer bit_i;
    begin
      clear_imem;
      a = 0;
      // Idle state before CS assert: CS_N=1, SCLK=0, MOSI=0; OE=0x07 (CS_N,SCLK,MOSI outputs)
      load_word(a[5:0], insn(OP_OUT, 12'h001)); a = a + 1;
      load_word(a[5:0], insn(OP_OE,  12'h007)); a = a + 1;
      // 8 bits MSB first:
      // Each bit: OUT(CS=0,SCLK=0,MOSI=b) -> WAIT_PARAM(0) -> OUT_SAMPLE(CS=0,SCLK=1,MOSI=b, pin=3) -> NOP(1 cyc high)
      // SCLK high = 2 cycles (OUT_SAMPLE->NOP + next OUT); SCLK low = 1 + (param + 1) = param + 2 = low_cycles
      for (bit_i = 7; bit_i >= 0; bit_i = bit_i - 1) begin
        load_word(a[5:0], insn(OP_OUT, {9'd0, mosi_byte[bit_i], 2'b00})); a = a + 1;
        load_word(a[5:0], insn(OP_WAIT_PARAM, 12'd0)); a = a + 1;
        load_word(a[5:0], insn(OP_OUT_SAMPLE, {1'b0, 3'd3, 5'd0, mosi_byte[bit_i], 2'b10})); a = a + 1;
        load_word(a[5:0], insn(OP_NOP, 12'd0)); a = a + 1;
      end
      // Final SCLK fall, hold CS_N=0 1 cycle, then deassert CS_N=1, MARK sample, HALT
      load_word(a[5:0], insn(OP_OUT, 12'h000));  a = a + 1;
      load_word(a[5:0], insn(OP_OUT, 12'h001));  a = a + 1;
      load_word(a[5:0], insn(OP_MARK, 12'h100)); a = a + 1;
      load_word(a[5:0], insn(OP_HALT, 12'h000)); a = a + 1;

      prog_valid = 1;
      spi_mon_mosi = 8'h00;
      spi_mon_bits = 0;
      spi_tgt_bit = 6;
      spi_mon_active = 1;
      target_drive_en = 8'b0000_1000;  // drive MISO = uio[3]
      target_drive_val = 8'b0000_1000; // preload 0xAA[7] = 1

      start_and_wait(low_cycles - 12'd2);

      spi_mon_active = 0;
      target_drive_en = 8'h00;
      got_miso = sample_reg;
      check(spi_mon_bits == 8 && spi_mon_mosi == mosi_byte, "SPI program transmitted exact MOSI byte");
    end
  endtask

  task test_spi_boundary_sweep;
    reg [7:0] miso_2, miso_3, miso_4;
    begin
      // Analytical boundary for T=10ns, d=23ns, s=2ns (posedge capture): L_min = ceil(25/10) = 3
      run_spi_trial(12'd2, 8'h55, miso_2);
      run_spi_trial(12'd3, 8'h55, miso_3);
      run_spi_trial(12'd4, 8'h55, miso_4);
      check(miso_2 != 8'hAA, "SPI sweep L=2 fails below 23ns boundary");
      check(miso_3 == 8'hAA, "SPI sweep L=3 passes at 30ns >= 23ns+2ns boundary");
      check(miso_4 == 8'hAA, "SPI sweep L=4 passes above boundary");
    end
  endtask

  // ---- Test 4: Loaded I2C Open-Drain Controller (START, 0xA0 write, ACK/NACK check, STOP) ----
  // Pins: uio[0]=SCL, uio[1]=SDA. Open-drain: OUT=0 always, OE=1 pulls low, OE=0 releases to pull-up.
  task run_i2c_byte;
    input [7:0] addr_byte;
    input       do_ack;
    output [7:0] ack_tag;
    reg [15:0]  ev_ts_tmp;
    reg [7:0]   rx_i2c;
    integer     i2c_bit;
    reg [7:0]   oe_mask;
    begin
      clear_imem;
      a = 0;
      // Idle: OUT=0, OE=0 (both SCL=1, SDA=1 via pull-up)
      load_word(a[5:0], insn(OP_OUT, 12'h000)); a = a + 1;
      load_word(a[5:0], insn(OP_OE,  12'h000)); a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd3));  a = a + 1;
      // START: pull SDA low (OE=0x02), wait, pull SCL low (OE=0x03)
      load_word(a[5:0], insn(OP_OE,  12'h002)); a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd3));  a = a + 1;
      load_word(a[5:0], insn(OP_OE,  12'h003)); a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd3));  a = a + 1;
      // 8 bits MSB first: release SDA OE for '1' (OE=0x01), drive SDA OE for '0' (OE=0x03)
      for (i2c_bit = 7; i2c_bit >= 0; i2c_bit = i2c_bit - 1) begin
        oe_mask = addr_byte[i2c_bit] ? 8'h01 : 8'h03;
        load_word(a[5:0], insn(OP_OE, {4'd0, oe_mask}));              a = a + 1; // SCL=0, SDA=bit
        load_word(a[5:0], insn(OP_WAIT, 12'd2));                      a = a + 1;
        load_word(a[5:0], insn(OP_OE, {4'd0, oe_mask & 8'h02}));      a = a + 1; // release SCL=1
        load_word(a[5:0], insn(OP_WAIT, 12'd2));                      a = a + 1;
        load_word(a[5:0], insn(OP_OE, {4'd0, oe_mask}));              a = a + 1; // pull SCL=0
      end
      // 9th clock (ACK/NACK): release SDA (OE=0x01), release SCL (OE=0x00), wait, SAMPLE SDA (no_shift), pull SCL=0
      load_word(a[5:0], insn(OP_OE, 12'h001));     a = a + 1; // SCL=0, SDA released
      load_word(a[5:0], insn(OP_WAIT, 12'd2));     a = a + 1;
      load_word(a[5:0], insn(OP_OE, 12'h000));     a = a + 1; // SCL=1, SDA released
      load_word(a[5:0], insn(OP_WAIT, 12'd2));     a = a + 1;
      load_word(a[5:0], insn(OP_SAMPLE, 12'h801)); a = a + 1; // sample pin 1 (SDA), no_shift
      load_word(a[5:0], insn(OP_OE, 12'h003));     a = a + 1; // pull SCL=0, SDA=0 for STOP prep
      load_word(a[5:0], insn(OP_WAIT, 12'd2));     a = a + 1;
      // Branch on ACK (last_sample == 0): if 0 jump to ACK mark, else mark NACK
      load_word(a[5:0], insn(OP_JZ, a[5:0] + 6'd3)); a = a + 1;
      load_word(a[5:0], insn(OP_MARK, 12'h0F0));   a = a + 1; // NACK tag 0xF0
      load_word(a[5:0], insn(OP_JMP, a[5:0] + 6'd2)); a = a + 1;
      load_word(a[5:0], insn(OP_MARK, 12'h0A0));   a = a + 1; // ACK tag 0xA0
      // STOP: release SCL=1 (OE=0x02), wait, release SDA=1 (OE=0x00), HALT
      load_word(a[5:0], insn(OP_OE, 12'h002));     a = a + 1;
      load_word(a[5:0], insn(OP_WAIT, 12'd2));     a = a + 1;
      load_word(a[5:0], insn(OP_OE, 12'h000));     a = a + 1;
      load_word(a[5:0], insn(OP_HALT, 12'h000));   a = a + 1;

      check(a <= 64, "I2C program fits inside 64 words");
      prog_valid = 1;
      rx_i2c = 8'h00;

      fork
        begin : i2c_target
          // Wait for START (SDA falls while SCL high)
          @(negedge bus_pins[1]);
          check(bus_pins[0] == 1'b1, "I2C START: SDA fell while SCL high");
          for (i2c_bit = 7; i2c_bit >= 0; i2c_bit = i2c_bit - 1) begin
            @(posedge bus_pins[0]);
            rx_i2c[i2c_bit] = bus_pins[1];
          end
          @(negedge bus_pins[0]);
          if (do_ack) begin
            target_drive_en[1]  = 1'b1;
            target_drive_val[1] = 1'b0;
          end
          @(posedge bus_pins[0]); // 9th clock high
          @(negedge bus_pins[0]);
          target_drive_en[1] = 1'b0;
          // Wait for STOP (SDA rises while SCL high)
          @(posedge bus_pins[1]);
          check(bus_pins[0] == 1'b1, "I2C STOP: SDA rose while SCL high");
        end
        begin : i2c_ctrl
          start_and_wait(12'd0);
        end
      join

      check(rx_i2c == addr_byte, "I2C target received exact address byte");
      check(event_count == 4'd1, "I2C recorded 1 ACK/NACK event");
      pop_event(ack_tag, ev_ts_tmp);
    end
  endtask

  task test_i2c_open_drain_program;
    reg [7:0] tag_ack, tag_nack;
    begin
      run_i2c_byte(8'hA0, 1'b1, tag_ack);
      check(tag_ack == 8'hA0, "I2C open-drain controller observed ACK");
      run_i2c_byte(8'hA2, 1'b0, tag_nack);
      check(tag_nack == 8'hF0, "I2C open-drain controller observed NACK");
    end
  endtask

  initial begin
    #500000;
    $display("FAIL: global simulation timeout at time=%0t pc=%0d state=%0d", $time, pc, eng_state);
    $finish_and_return(1);
  end

  initial begin
    errors = 0;
    repeat (4) @(posedge clk);
    rst_n = 1;
    repeat (2) @(posedge clk);

    $display("Running test_isa_and_faults");
    test_isa_and_faults;
    $display("Running test_uart_tx_program");
    test_uart_tx_program;
    $display("Running test_spi_boundary_sweep");
    test_spi_boundary_sweep;
    $display("Running test_i2c_open_drain_program");
    test_i2c_open_drain_program;

    if (errors == 0) $display("PASS: v1 protocol_sequencer ISA + UART + SPI sweep + I2C tests passed");
    else $display("FAIL: %0d checks failed", errors);
    $finish_and_return(errors != 0);
  end
endmodule
`default_nettype wire
