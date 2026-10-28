`timescale 1ns/1ps
module nmc_mesh #(
    parameter integer MESH_X=8, MESH_Y=8, N=MESH_X*MESH_Y, FIFO_DEPTH=4
) (
    input wire clk,rst_n,
    input wire [N-1:0] tx_valid,
    output wire [N-1:0] tx_ready,
    input wire [129:0] tx_flit [0:N-1],
    output wire [N-1:0] rx_valid,
    input wire [N-1:0] rx_ready,
    output wire [129:0] rx_flit [0:N-1],
    output wire idle,error
);
    wire [4:0] iv[0:N-1],ir[0:N-1],ov[0:N-1],orr[0:N-1];
    wire [129:0] idata[0:N-1][0:4],odata[0:N-1][0:4];
    wire [N-1:0] router_idle,router_error;
    for (genvar t=0;t<N;t=t+1) begin: routers
        wire [129:0] local_in[0:4],local_out[0:4];
        for (genvar p=0;p<5;p=p+1) begin: array_bridge
            assign local_in[p]=idata[t][p];
            assign odata[t][p]=local_out[p];
        end
        nmc_router #(.TILE_ID(t),.MESH_X(MESH_X),.FIFO_DEPTH(FIFO_DEPTH)) router(
            .clk(clk),.rst_n(rst_n),.in_valid(iv[t]),.in_ready(ir[t]),.in_flit(local_in),
            .out_valid(ov[t]),.out_ready(orr[t]),.out_flit(local_out),
            .idle(router_idle[t]),.error(router_error[t]));
        assign iv[t][4]=tx_valid[t]; assign idata[t][4]=tx_flit[t]; assign tx_ready[t]=ir[t][4];
        assign rx_valid[t]=ov[t][4]; assign rx_flit[t]=odata[t][4]; assign orr[t][4]=rx_ready[t];
        if (t/MESH_X>0) begin: north_link
            assign iv[t][0]=ov[t-MESH_X][2]; assign idata[t][0]=odata[t-MESH_X][2];
            assign orr[t][0]=ir[t-MESH_X][2];
        end else begin: north_boundary
            assign iv[t][0]=0; assign idata[t][0]=0; assign orr[t][0]=0;
        end
        if (t%MESH_X<MESH_X-1) begin: east_link
            assign iv[t][1]=ov[t+1][3]; assign idata[t][1]=odata[t+1][3]; assign orr[t][1]=ir[t+1][3];
        end else begin: east_boundary
            assign iv[t][1]=0; assign idata[t][1]=0; assign orr[t][1]=0;
        end
        if (t/MESH_X<MESH_Y-1) begin: south_link
            assign iv[t][2]=ov[t+MESH_X][0]; assign idata[t][2]=odata[t+MESH_X][0];
            assign orr[t][2]=ir[t+MESH_X][0];
        end else begin: south_boundary
            assign iv[t][2]=0; assign idata[t][2]=0; assign orr[t][2]=0;
        end
        if (t%MESH_X>0) begin: west_link
            assign iv[t][3]=ov[t-1][1]; assign idata[t][3]=odata[t-1][1]; assign orr[t][3]=ir[t-1][1];
        end else begin: west_boundary
            assign iv[t][3]=0; assign idata[t][3]=0; assign orr[t][3]=0;
        end
    end
    assign idle=&router_idle;
    assign error=|router_error;
endmodule
