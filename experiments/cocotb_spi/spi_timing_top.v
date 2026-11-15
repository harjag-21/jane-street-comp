`timescale 1ns/1ps

// Simulation-only signal shell. Cocotb supplies the master and target models.
module spi_timing_top;
    output reg sclk;
    output reg cs_n;
    output reg mosi;
    output reg miso;
endmodule
