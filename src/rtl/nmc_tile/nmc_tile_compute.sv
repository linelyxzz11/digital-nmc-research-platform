`timescale 1ns/1ps
module nmc_tile_compute #(
    parameter integer TILE_ID = 1
) (
    input logic clk, rst_n,
    input logic task_valid,
    output wire task_ready,
    input logic [7:0] task_version, task_opcode, task_tile_id,
    input logic [15:0] task_tid, task_m_local, task_k_local,
    input logic [15:0] task_w_base, task_x_base, task_y_base, task_global_row_base,
    input logic [1:0] task_result_kind,
    input logic [7:0] task_result_dst_tile,
    input logic task_last,
    input logic mem_req_valid,
    output wire mem_req_ready,
    input logic mem_req_write,
    input logic [15:0] mem_req_addr,
    input logic [31:0] mem_req_wdata,
    input logic [3:0] mem_req_wstrb,
    output wire mem_rsp_valid,
    input logic mem_rsp_ready,
    output wire [31:0] mem_rsp_rdata,
    output wire mem_rsp_error,
    input logic root_wr_valid,
    output wire root_wr_ready,
    input logic [15:0] root_wr_addr,
    input logic [3:0] root_wr_mask,
    input logic [127:0] root_wr_data,
    output wire root_wr_error,
    output wire trace_root_write,
    output logic result_valid,
    input logic result_ready,
    output logic [15:0] result_tid,
    output logic [7:0] result_dst_tile,
    output logic [15:0] result_base_index,
    output logic [3:0] result_lane_mask,
    output logic [127:0] result_data,
    output logic [1:0] result_kind,
    output logic result_last,
    output wire local_done_valid,
    input logic local_done_ready,
    output wire [15:0] local_done_tid,
    output logic [3:0] local_done_status,
    output wire [3:0] trace_state,
    output wire [15:0] trace_group, trace_k,
    output wire trace_rd_req, trace_rd_valid, trace_mac, trace_y_write,
    output wire [15:0] trace_w_addr, trace_x_addr, trace_y_addr,
    output wire [31:0] trace_w_data,
    output wire [7:0] trace_x_data,
    output wire [3:0] trace_y_mask,
    output wire [127:0] trace_acc
);
    localparam [3:0] IDLE=0, VALIDATE=1, INIT_ACC=2, RUN=3,
                     WRITE_RESULT=4, EMIT_RESULT=5, LOCAL_DONE=6;
    logic [3:0] state;
    logic [7:0] version_q, opcode_q, tile_q, dst_q;
    logic [15:0] tid_q, m_q, k_q, w_q, x_q, y_q, global_q;
    logic [1:0] kind_q;
    logic last_q;
    logic [15:0] group_q, issue_k, consume_k;
    logic [3:0] lane_mask;
    logic [3:0] validation_status;
    logic [31:0] groups_needed, weight_end, input_end, result_end, global_end;
    wire host_idle, rd_valid;
    wire [31:0] w_data;
    wire [7:0] x_data;
    wire [127:0] accumulators;
    wire rd_enable = rst_n && state == RUN && issue_k < k_q;
    wire mac_enable = rst_n && state == RUN && rd_valid;
    wire [15:0] w_offset = 16'd4 * (group_q * k_q + issue_k);
    wire [15:0] w_address = w_q + w_offset;
    wire [15:0] x_address = x_q + issue_k;
    wire [15:0] y_address = y_q + (group_q << 4);
    wire final_group = ({16'b0, group_q} + 1) * 4 >= {16'b0, m_q};

    assign task_ready = rst_n && state == IDLE && host_idle && !mem_req_valid && !root_wr_valid;
    assign local_done_valid = rst_n && state == LOCAL_DONE;
    assign local_done_tid = tid_q;
    assign trace_state = state;
    assign trace_group = group_q;
    assign trace_k = consume_k;
    assign trace_rd_req = rd_enable;
    assign trace_rd_valid = rd_valid;
    assign trace_mac = mac_enable;
    assign trace_y_write = rst_n && state == WRITE_RESULT;
    assign trace_w_addr = w_address;
    assign trace_x_addr = x_address;
    assign trace_y_addr = y_address;
    assign trace_w_data = w_data;
    assign trace_x_data = x_data;
    assign trace_y_mask = lane_mask;
    assign trace_acc = accumulators;

    always @* begin
        groups_needed = ({16'b0, m_q} + 3) >> 2;
        weight_end = {16'b0, w_q} + 4 * groups_needed * {16'b0, k_q};
        input_end = {16'b0, x_q} + {16'b0, k_q};
        result_end = {16'b0, y_q} + 4 * {16'b0, m_q};
        global_end = {16'b0, global_q} + {16'b0, m_q} - 1;
        validation_status = 0;
        if (opcode_q != 1) validation_status = 1;
        else if (version_q != 1 || kind_q > 1) validation_status = 2;
        else if (m_q == 0 || m_q > 256 || k_q == 0 || k_q > 1024) validation_status = 3;
        else if (tile_q != 8'(TILE_ID) || tile_q >= 4 || dst_q >= 4) validation_status = 4;
        else if (global_end > 65535) validation_status = 2;
        else if (|w_q[1:0] || |y_q[1:0]) validation_status = 6;
        else if (weight_end > 4096 || x_q < 16'h1000 || input_end > 32'h1400 ||
                 y_q < 16'h2000 || result_end > 32'h2400) validation_status = 5;
        for (integer lane = 0; lane < 4; lane = lane + 1)
            lane_mask[lane] = ({16'b0, group_q} * 4 + 32'(lane)) < {16'b0, m_q};
    end

    nmc_mac4 mac (
        .clk(clk), .rst_n(rst_n), .clear(state == INIT_ACC),
        .enable(mac_enable), .lane_mask(lane_mask), .weights(w_data), .x(x_data),
        .accumulators(accumulators)
    );
    nmc_tile_sram sram (
        .clk(clk), .rst_n(rst_n), .compute_idle(state == IDLE),
        .mem_req_valid(mem_req_valid), .mem_req_ready(mem_req_ready),
        .mem_req_write(mem_req_write), .mem_req_addr(mem_req_addr),
        .mem_req_wdata(mem_req_wdata), .mem_req_wstrb(mem_req_wstrb),
        .mem_rsp_valid(mem_rsp_valid), .mem_rsp_ready(mem_rsp_ready),
        .mem_rsp_rdata(mem_rsp_rdata), .mem_rsp_error(mem_rsp_error), .host_idle(host_idle),
        .root_wr_valid(root_wr_valid), .root_wr_ready(root_wr_ready),
        .root_wr_addr(root_wr_addr), .root_wr_mask(root_wr_mask), .root_wr_data(root_wr_data),
        .root_wr_error(root_wr_error), .root_wr_commit(trace_root_write),
        .rd_enable(rd_enable), .w_address(w_address), .x_address(x_address),
        .rd_valid(rd_valid), .w_data(w_data), .x_data(x_data),
        .y_write(trace_y_write), .y_address(y_address), .y_mask(lane_mask), .y_data(accumulators)
    );

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= IDLE;
            version_q <= 0; opcode_q <= 0; tile_q <= 0; dst_q <= 0;
            tid_q <= 0; m_q <= 0; k_q <= 0; w_q <= 0; x_q <= 0;
            y_q <= 0; global_q <= 0; kind_q <= 0; last_q <= 0;
            group_q <= 0; issue_k <= 0; consume_k <= 0;
            result_valid <= 0; result_tid <= 0; result_dst_tile <= 0;
            result_base_index <= 0; result_lane_mask <= 0; result_data <= 0;
            result_kind <= 0; result_last <= 0; local_done_status <= 0;
        end else begin
            case (state)
                IDLE: if (task_valid && task_ready) begin
                    version_q <= task_version; opcode_q <= task_opcode; tile_q <= task_tile_id;
                    tid_q <= task_tid; m_q <= task_m_local; k_q <= task_k_local;
                    w_q <= task_w_base; x_q <= task_x_base; y_q <= task_y_base;
                    global_q <= task_global_row_base; kind_q <= task_result_kind;
                    dst_q <= task_result_dst_tile; last_q <= task_last;
                    state <= VALIDATE;
                end
                VALIDATE: begin
                    local_done_status <= validation_status;
                    if (validation_status == 0) begin
                        group_q <= 0;
                        state <= INIT_ACC;
                    end else state <= LOCAL_DONE;
                end
                INIT_ACC: begin
                    issue_k <= 0;
                    consume_k <= 0;
                    state <= RUN;
                end
                RUN: begin
                    if (rd_enable) issue_k <= issue_k + 1'b1;
                    if (mac_enable) begin
                        consume_k <= consume_k + 1'b1;
                        if (consume_k == k_q - 1'b1) state <= WRITE_RESULT;
                    end
                end
                WRITE_RESULT: begin
                    result_valid <= 1;
                    result_tid <= tid_q;
                    result_dst_tile <= dst_q;
                    result_base_index <= global_q + (group_q << 2);
                    result_lane_mask <= lane_mask;
                    result_data <= accumulators;
                    result_kind <= kind_q;
                    result_last <= last_q && final_group;
                    state <= EMIT_RESULT;
                end
                EMIT_RESULT: if (result_valid && result_ready) begin
                    result_valid <= 0;
                    if (final_group) state <= LOCAL_DONE;
                    else begin
                        group_q <= group_q + 1'b1;
                        state <= INIT_ACC;
                    end
                end
                LOCAL_DONE: if (local_done_ready) state <= IDLE;
                default: begin
                    result_valid <= 0;
                    local_done_status <= 7;
                    state <= LOCAL_DONE;
                end
            endcase
        end
    end
endmodule
