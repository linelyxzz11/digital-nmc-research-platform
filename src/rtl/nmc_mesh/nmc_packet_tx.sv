`timescale 1ns/1ps
// One or two flits are accepted atomically into the fixed 4-flit NI queue.
module nmc_packet_tx(
    input wire clk,rst_n,
    input wire packet_valid, packet_two,
    output wire packet_ready,
    input wire [127:0] header,payload,
    output wire flit_valid,
    input wire flit_ready,
    output wire [129:0] flit,
    output wire idle
);
    logic [129:0] fifo[0:3];
    integer count,rd,wr;
    wire accept=packet_valid && packet_ready;
    wire send=flit_valid && flit_ready;
    assign packet_ready=rst_n && count<=(packet_two?2:3);
    assign flit_valid=rst_n && count!=0;
    assign flit=fifo[rd];
    assign idle=count==0;
    always_ff @(posedge clk) begin
        if (!rst_n) begin count<=0;rd<=0;wr<=0;end
        else begin
            count<=count+(accept?(packet_two?2:1):0)-(send?1:0);
            if (send) rd<=(rd+1)%4;
            if (accept) begin
                fifo[wr]<={packet_two?2'b00:2'b11,header};
                if (packet_two) fifo[(wr+1)%4]<={2'b10,payload};
                wr<=(wr+(packet_two?2:1))%4;
            end
        end
    end
endmodule
