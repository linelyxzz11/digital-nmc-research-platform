`timescale 1ns/1ps
module nmc_mac4 (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         clear,
    input  logic         enable,
    input  logic [3:0]   lane_mask,
    input  logic [31:0]  weights,
    input  logic [7:0]   x,
    output wire  [127:0] accumulators
);
    for (genvar lane = 0; lane < 4; lane = lane + 1) begin : lanes
        logic signed [31:0] acc;
        wire signed [15:0] product = $signed(weights[8*lane +: 8]) * $signed(x);
        assign accumulators[32*lane +: 32] = acc;
        always_ff @(posedge clk) begin
            if (!rst_n) acc <= '0;
            else if (clear) acc <= '0;
            else if (enable && lane_mask[lane])
                acc <= acc + {{16{product[15]}}, product};
        end
    end
endmodule
