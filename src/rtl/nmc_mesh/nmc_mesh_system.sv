`timescale 1ns/1ps
// Integrated default-hardware GEMV execution. Descriptor/configuration and
// weight preload are sideband setup; MEM_WRITE/ACK, RESULT and Done use the NoC.
// Root/Host is Tile 0; one transaction per reset, at most 256 output words.
module nmc_mesh_system #(
    parameter integer MESH_X=8,MESH_Y=8,N=MESH_X*MESH_Y
) (
    input wire clk,rst_n,
    input wire [N-1:0] task_valid,
    output wire [N-1:0] task_ready,
    input wire [15:0] local_m[0:N-1],local_k[0:N-1],row_base[0:N-1],
    input wire [7:0] result_dst[0:N-1],
    input wire [N-1:0] result_psum,
    input wire [N-1:0] endpoint_enable,endpoint_reduce,endpoint_forward,
    input wire [7:0] endpoint_parent[0:N-1],
    input wire [15:0] endpoint_base[0:N-1],endpoint_count[0:N-1],
    input wire [63:0] endpoint_sources[0:N-1],
    input wire mask_cfg_valid,
    input wire [7:0] mask_cfg_tile,mask_cfg_index,
    input wire [63:0] mask_cfg_sources,
    input wire host_packet_valid,host_packet_two,
    output wire host_packet_ready,
    input wire [127:0] host_header,host_payload,
    output wire host_response_valid,
    input wire host_response_ready,
    output wire [127:0] host_response,
    input wire [N-1:0] eject_enable,
    input wire memory_select,memory_valid,memory_write,memory_response_ready,
    input wire [7:0] memory_tile,
    input wire [15:0] memory_address,
    input wire [31:0] memory_data,
    input wire [3:0] memory_strb,
    output wire memory_ready,memory_response_valid,memory_error,
    output wire [31:0] memory_response,
    output wire [N-1:0] local_done,
    output wire idle,error,
    output wire [N-1:0] trace_rx_valid,trace_rx_ready,
    output wire [129:0] trace_rx_flit[0:N-1],
    output wire [N-1:0] trace_result_valid,trace_result_ready
);
    wire [N-1:0] tx_valid,tx_ready,rx_valid,rx_ready,ni_idle,endpoint_idle,endpoint_error;
    wire [129:0] tx_flit[0:N-1],rx_flit[0:N-1];
    wire mesh_idle,mesh_error;
    wire [N-1:0] result_valid,result_ready,done_valid;
    wire [127:0] result_data[0:N-1];
    wire [15:0] result_tid[0:N-1],result_base[0:N-1];
    wire [7:0] result_destination[0:N-1];
    wire [3:0] result_mask[0:N-1],done_status[0:N-1];
    wire [1:0] result_kind[0:N-1];
    wire [N-1:0] result_last;
    wire [N-1:0] mem_req_ready,mem_rsp_valid,mem_rsp_error;
    wire [31:0] mem_rsp_data[0:N-1];
    wire [N-1:0] node_idle,node_error;
    logic [N-1:0] done_seen;
    assign local_done=done_seen;
    assign trace_rx_valid=rx_valid;assign trace_rx_ready=rx_ready;
    assign trace_result_valid=result_valid;assign trace_result_ready=result_ready;
    for (genvar t=0;t<N;t=t+1) begin: nodes
        logic [127:0] head,mem_header,mem_payload;
        logic head_pending;
        logic [1:0] mem_state;
        logic local_error;
        wire ext_mem=memory_select && memory_tile==8'(t);
        wire req_valid=ext_mem?memory_valid:mem_state==1;
        wire req_write=ext_mem?memory_write:1'b1;
        wire [15:0] req_addr=ext_mem?memory_address:mem_header[59:44];
        wire [31:0] req_data=ext_mem?memory_data:mem_payload[31:0];
        wire [3:0] req_strb=ext_mem?memory_strb:mem_header[63:60];
        wire rsp_ready=ext_mem?memory_response_ready:mem_state==2;
        wire root_write,root_ready,root_error;
        wire [15:0] root_addr;
        wire [3:0] root_mask;
        wire [127:0] root_data;
        wire endpoint_rx_ready,endpoint_rx_valid,endpoint_tx_valid,endpoint_tx_ready;
        wire [127:0] endpoint_tx_header,endpoint_tx_payload;
        wire endpoint_done,endpoint_done_ready;
        logic packet_valid,packet_two;
        wire packet_ready;
        logic [127:0] packet_header,packet_payload;
        logic [2:0] choice;
        logic receive_ready;
        wire receive_fire=rx_valid[t] && rx_ready[t];
        wire [1:0] receive_type=rx_flit[t][129:128];
        wire [127:0] receive_data=rx_flit[t][127:0];
        assign trace_rx_flit[t]=rx_flit[t];
        assign endpoint_rx_valid=receive_fire && head_pending && receive_type==2 && head[3:0]==1;
        assign node_idle[t]=!head_pending && mem_state==0;
        assign node_error[t]=local_error || root_error || endpoint_error[t];

        always @* begin
            receive_ready=0;
            if (rx_valid[t]) begin
                if (head_pending) begin
                    receive_ready=(head[3:0]==1)?endpoint_rx_ready:mem_state==0;
                end else if (receive_type==0) begin
                    if (receive_data[3:0]==1) receive_ready=endpoint_rx_ready;
                    else if (receive_data[3:0]==2) receive_ready=mem_state==0;
                    else receive_ready=1;
                end else if (receive_type==3) begin
                    receive_ready=(t==0)?host_response_ready:1'b1;
                end else receive_ready=1;
            end
        end
        assign rx_ready[t]=rst_n && eject_enable[t] && receive_ready;
        always_ff @(posedge clk) begin
            if (!rst_n) begin
                head<=0;head_pending<=0;mem_state<=0;mem_header<=0;mem_payload<=0;
                local_error<=0;done_seen[t]<=0;
            end else begin
                if (done_valid[t]) begin
                    done_seen[t]<=1;
                    if (done_status[t]!=0 || done_seen[t]) local_error<=1;
                end
                if (receive_fire) begin
                    if (receive_data[19:12]!=8'(t) && !head_pending) local_error<=1;
                    if (head_pending) begin
                        if (receive_type!=2) local_error<=1;
                        if (head[3:0]==2) begin
                            mem_header<=head;mem_payload<=receive_data;mem_state<=1;
                            if (receive_data[127:32]!=0) local_error<=1;
                        end else if (head[3:0]!=1) local_error<=1;
                        head_pending<=0;
                    end else if (receive_type==0) begin
                        head<=receive_data;head_pending<=1;
                        if (receive_data[3:0]!=1 && receive_data[3:0]!=2) local_error<=1;
                    end else if (receive_type!=3 || t!=0 ||
                                 (receive_data[3:0]!=3 && receive_data[3:0]!=4)) local_error<=1;
                end
                if (!ext_mem && mem_state==1 && mem_req_ready[t]) mem_state<=2;
                if (!ext_mem && mem_state==2 && mem_rsp_valid[t]) begin
                    mem_state<=3;
                    if (mem_rsp_error[t]) local_error<=1;
                end
                if (packet_valid && packet_ready && choice==2) mem_state<=0;
            end
        end
        // Sources share one finite NI. Priority is only considered at an
        // atomic packet enqueue; an accepted packet cannot be interleaved.
        always @* begin
            packet_valid=0;packet_two=0;packet_header=0;packet_payload=0;choice=0;
            if (t==0 && host_packet_valid) begin
                choice=1;packet_valid=1;packet_two=host_packet_two;
                packet_header=host_header;packet_payload=host_payload;
            end else if (mem_state==3) begin
                choice=2;packet_valid=1;
                packet_header[3:0]=3;packet_header[11:4]=8'(t);
                packet_header[19:12]=mem_header[11:4];packet_header[35:20]=mem_header[35:20];
                packet_header[59:44]=mem_header[79:64];
            end else if (endpoint_done) begin
                choice=3;packet_valid=1;
                packet_header[3:0]=4;packet_header[11:4]=8'(t);
                packet_header[35:20]=1;packet_header[59:44]=endpoint_count[t];
                packet_header[123:60]=endpoint_sources[t];
            end else if (endpoint_tx_valid) begin
                choice=4;packet_valid=1;packet_two=1;
                packet_header=endpoint_tx_header;packet_payload=endpoint_tx_payload;
            end else if (result_valid[t]) begin
                choice=5;packet_valid=1;packet_two=1;
                packet_header[3:0]=1;packet_header[11:4]=8'(t);
                packet_header[19:12]=result_destination[t];packet_header[35:20]=result_tid[t];
                packet_header[59:44]=result_base[t];packet_header[63:60]=result_mask[t];
                packet_header[64]=result_kind[t][0];packet_header[65]=result_last[t];
                packet_payload=result_data[t];
            end
        end
        assign result_ready[t]=packet_ready && choice==5;
        assign endpoint_tx_ready=packet_ready && choice==4;
        assign endpoint_done_ready=packet_ready && choice==3;
        if (t==0) begin: host_ni
            assign host_packet_ready=packet_ready && choice==1;
        end
        nmc_packet_tx ni(.clk(clk),.rst_n(rst_n),.packet_valid(packet_valid),.packet_two(packet_two),
            .packet_ready(packet_ready),.header(packet_header),.payload(packet_payload),
            .flit_valid(tx_valid[t]),.flit_ready(tx_ready[t]),.flit(tx_flit[t]),.idle(ni_idle[t]));
        nmc_reduce #(.TILE_ID(t),.TILE_COUNT(N)) endpoint(
            .clk(clk),.rst_n(rst_n),.enabled(endpoint_enable[t]),.reduce_mode(endpoint_reduce[t]),
            .forward_result(endpoint_forward[t]),.parent(endpoint_parent[t]),.tid(16'd1),
            .output_base(endpoint_base[t]),.output_count(endpoint_count[t]),.source_mask(endpoint_sources[t]),
            .mask_cfg_valid(mask_cfg_valid && mask_cfg_tile==8'(t)),.mask_cfg_index(mask_cfg_index),
            .mask_cfg_sources(mask_cfg_sources),.rx_valid(endpoint_rx_valid),.rx_ready(endpoint_rx_ready),
            .rx_header(head),.rx_payload(receive_data),.write_valid(root_write),.write_ready(root_ready),
            .write_addr(root_addr),.write_mask(root_mask),.write_data(root_data),
            .tx_valid(endpoint_tx_valid),.tx_ready(endpoint_tx_ready),
            .tx_header(endpoint_tx_header),.tx_payload(endpoint_tx_payload),
            .done_valid(endpoint_done),.done_ready(endpoint_done_ready),
            .idle(endpoint_idle[t]),.error(endpoint_error[t]),.trace_state(),.trace_rx_count());
        nmc_tile_compute #(.TILE_ID(t),.TILE_COUNT(N)) compute(
            .clk(clk),.rst_n(rst_n),.task_valid(task_valid[t]),.task_ready(task_ready[t]),
            .task_version(8'd1),.task_opcode(8'd1),.task_tile_id(8'(t)),.task_tid(16'd1),
            .task_m_local(local_m[t]),.task_k_local(local_k[t]),.task_w_base(16'd0),
            .task_x_base(16'h1000),.task_y_base(16'h2000),.task_global_row_base(row_base[t]),
            .task_result_kind({1'b0,result_psum[t]}),.task_result_dst_tile(result_dst[t]),.task_last(1'b1),
            .mem_req_valid(req_valid),.mem_req_ready(mem_req_ready[t]),.mem_req_write(req_write),
            .mem_req_addr(req_addr),.mem_req_wdata(req_data),.mem_req_wstrb(req_strb),
            .mem_rsp_valid(mem_rsp_valid[t]),.mem_rsp_ready(rsp_ready),.mem_rsp_rdata(mem_rsp_data[t]),
            .mem_rsp_error(mem_rsp_error[t]),.root_wr_valid(root_write),.root_wr_ready(root_ready),
            .root_wr_addr(root_addr),.root_wr_mask(root_mask),.root_wr_data(root_data),.root_wr_error(root_error),
            .trace_root_write(),.result_valid(result_valid[t]),.result_ready(result_ready[t]),
            .result_tid(result_tid[t]),.result_dst_tile(result_destination[t]),.result_base_index(result_base[t]),
            .result_lane_mask(result_mask[t]),.result_data(result_data[t]),.result_kind(result_kind[t]),
            .result_last(result_last[t]),.local_done_valid(done_valid[t]),.local_done_ready(1'b1),
            .local_done_tid(),.local_done_status(done_status[t]),.trace_state(),.trace_group(),.trace_k(),
            .trace_rd_req(),.trace_rd_valid(),.trace_mac(),.trace_y_write(),.trace_w_addr(),.trace_x_addr(),
            .trace_y_addr(),.trace_w_data(),.trace_x_data(),.trace_y_mask(),.trace_acc());
    end
    wire [$clog2(N)-1:0] memory_index=$clog2(N)'(memory_tile);
    wire memory_id_ok=int'(memory_tile)<N;
    assign memory_ready=memory_select && memory_id_ok && mem_req_ready[memory_index];
    assign memory_response_valid=memory_select && memory_id_ok && mem_rsp_valid[memory_index];
    assign memory_response=mem_rsp_data[memory_index];
    assign memory_error=memory_select && (!memory_id_ok || mem_rsp_error[memory_index]);
    assign host_response_valid=rx_valid[0] && rx_flit[0][129:128]==3 && eject_enable[0];
    assign host_response=rx_flit[0][127:0];
    assign idle=mesh_idle && (&ni_idle) && (&node_idle) && (&endpoint_idle);
    assign error=mesh_error || (|node_error);
    nmc_mesh #(.MESH_X(MESH_X),.MESH_Y(MESH_Y)) mesh(
        .clk(clk),.rst_n(rst_n),.tx_valid(tx_valid),.tx_ready(tx_ready),.tx_flit(tx_flit),
        .rx_valid(rx_valid),.rx_ready(rx_ready),.rx_flit(rx_flit),.idle(mesh_idle),.error(mesh_error));
endmodule
