`timescale 1ns/1ps
// Five 4-flit input FIFOs, XY routing, RR at packet boundaries, held grants.
module nmc_router #(
    parameter integer TILE_ID=0, MESH_X=8, FIFO_DEPTH=4
) (
    input wire clk, rst_n,
    input wire [4:0] in_valid,
    output wire [4:0] in_ready,
    input wire [129:0] in_flit [0:4],
    output logic [4:0] out_valid,
    input wire [4:0] out_ready,
    output logic [129:0] out_flit [0:4],
    output logic idle,
    output logic error
);
    // NORTH=0 EAST=1 SOUTH=2 WEST=3 LOCAL=4; flit[129:128] is type.
    logic [129:0] fifo [0:4][0:FIFO_DEPTH-1];
    integer count [0:4], rd [0:4], wr [0:4];
    integer locked [0:4], held [0:4], rr [0:4];
    integer desired [0:4], selected [0:4];
    logic [4:0] pop;
    function automatic integer route(input integer destination);
        if (destination%MESH_X > TILE_ID%MESH_X) route=1;
        else if (destination%MESH_X < TILE_ID%MESH_X) route=3;
        else if (destination/MESH_X > TILE_ID/MESH_X) route=2;
        else if (destination/MESH_X < TILE_ID/MESH_X) route=0;
        else route=4;
    endfunction
    for (genvar p=0;p<5;p=p+1) begin: input_ready
        assign in_ready[p]=rst_n && count[p]<FIFO_DEPTH;
    end
    always @* begin
        idle=1;
        for (integer p=0;p<5;p=p+1) begin
            desired[p]=-1;
            if (count[p]!=0) begin
                idle=0;
                desired[p]=route(int'(fifo[p][rd[p]][19:12]));
                for (integer o=0;o<5;o=o+1)
                    if (locked[o]==p) desired[p]=o;
            end
        end
        for (integer o=0;o<5;o=o+1) begin
            selected[o]=-1;
            out_valid[o]=0;
            out_flit[o]=0;
            if (held[o]>=0) selected[o]=held[o];
            else if (locked[o]>=0) begin
                if (count[locked[o]]!=0) selected[o]=locked[o];
            end else begin
                for (integer step=0;step<5;step=step+1)
                    if (selected[o]<0 && desired[(rr[o]+step)%5]==o)
                        selected[o]=(rr[o]+step)%5;
            end
            if (selected[o]>=0) begin
                out_valid[o]=rst_n && count[selected[o]]!=0;
                out_flit[o]=fifo[selected[o]][rd[selected[o]]];
            end
        end
    end
    // Ready only controls state updates. Keep it out of the valid/data cone.
    always @* begin
        pop=0;
        for (integer o=0;o<5;o=o+1)
            if (out_valid[o] && out_ready[o]) pop[selected[o]]=1;
    end
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            error<=0;
            for (integer p=0;p<5;p=p+1) begin
                count[p]<=0; rd[p]<=0; wr[p]<=0;
                locked[p]<=-1; held[p]<=-1; rr[p]<=0;
            end
        end else begin
            for (integer p=0;p<5;p=p+1) begin
                case ({in_valid[p] && in_ready[p],pop[p]})
                    2'b10: count[p]<=count[p]+1;
                    2'b01: count[p]<=count[p]-1;
                    default: count[p]<=count[p];
                endcase
                if (in_valid[p] && in_ready[p]) begin
                    fifo[p][wr[p]]<=in_flit[p];
                    wr[p]<=(wr[p]+1)%FIFO_DEPTH;
                end
                if (pop[p]) rd[p]<=(rd[p]+1)%FIFO_DEPTH;
            end
            for (integer o=0;o<5;o=o+1) begin
                if (out_valid[o] && !out_ready[o]) held[o]<=selected[o];
                else if (out_valid[o] && out_ready[o]) begin
                    held[o]<=-1;
                    case (out_flit[o][129:128])
                        2'b00: locked[o]<=selected[o];
                        2'b10: begin
                            if (locked[o]!=selected[o]) error<=1;
                            locked[o]<=-1; rr[o]<=(selected[o]+1)%5;
                        end
                        2'b11: begin
                            if (locked[o]>=0) error<=1;
                            rr[o]<=(selected[o]+1)%5;
                        end
                        default: if (locked[o]!=selected[o]) error<=1;
                    endcase
                end else held[o]<=-1;
            end
        end
    end
endmodule
