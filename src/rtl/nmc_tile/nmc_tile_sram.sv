`timescale 1ns/1ps
module nmc_tile_sram (
    input logic clk, rst_n,
    input logic compute_idle,
    input logic mem_req_valid,
    output wire mem_req_ready,
    input logic mem_req_write,
    input logic [15:0] mem_req_addr,
    input logic [31:0] mem_req_wdata,
    input logic [3:0] mem_req_wstrb,
    output wire [31:0] mem_rsp_rdata,
    output logic mem_rsp_valid,
    input logic mem_rsp_ready,
    output logic mem_rsp_error,
    output wire host_idle,
    input logic rd_enable,
    input logic [15:0] w_address, x_address,
    output logic rd_valid,
    output logic [31:0] w_data,
    output logic [7:0] x_data,
    input logic y_write,
    input logic [15:0] y_address,
    input logic [3:0] y_mask,
    input logic [127:0] y_data
);
    logic [31:0] weight_mem [0:1023];
    logic [7:0] input_mem [0:1023];
    logic host_busy, host_write, host_bad, x_issue_done;
    logic [15:0] host_addr;
    logic [31:0] host_data, response_data;
    logic [3:0] host_strb;
    logic [1:0] host_byte, x_return_byte;
    logic x_return_valid;
    wire host_w = host_addr < 16'h1000;
    wire host_x = host_addr >= 16'h1000 && host_addr < 16'h1400;
    wire host_y = host_addr >= 16'h2000 && host_addr < 16'h2400;
    wire request_region_ok = mem_req_addr < 16'h1000 ||
        (mem_req_addr >= 16'h1000 && mem_req_addr < 16'h1400) ||
        (mem_req_addr >= 16'h2000 && mem_req_addr < 16'h2400);
    assign host_idle = !host_busy && !mem_rsp_valid;
    assign mem_req_ready = rst_n && compute_idle && host_idle;

    // Host and Compute explicitly share ONE read/write service port per Bank.
    // They are mutually exclusive: external loading is an IDLE-only phase.
    wire w_host_en = host_busy && !host_bad && host_w;
    wire w_port_en = rd_enable || w_host_en;
    wire w_port_write = w_host_en && host_write;
    wire [9:0] w_port_addr = rd_enable ? w_address[11:2] : host_addr[11:2];
    wire x_host_en = host_busy && !host_bad && host_x && (host_write || !x_issue_done);
    wire x_port_en = rd_enable || x_host_en;
    wire x_port_write = x_host_en && host_write;
    wire [9:0] x_port_addr = rd_enable ? x_address[9:0] : {host_addr[9:2],host_byte};
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            rd_valid <= 0; w_data <= 0; x_data <= 0;
            x_return_valid <= 0; x_return_byte <= 0;
        end else begin
            rd_valid <= rd_enable;
            x_return_valid <= x_host_en && !host_write;
            if (x_host_en && !host_write) x_return_byte <= host_byte;
            if (w_port_en) begin
                if (w_port_write) begin
                    for (integer b=0;b<4;b=b+1)
                        if (host_strb[b]) weight_mem[w_port_addr][8*b +: 8] <= host_data[8*b +: 8];
                end else w_data <= weight_mem[w_port_addr];
            end
            if (x_port_en) begin
                if (x_port_write) begin
                    if (host_strb[host_byte]) input_mem[x_port_addr] <= host_data[8*host_byte +: 8];
                end else x_data <= input_mem[x_port_addr];
            end
        end
    end

    // One outstanding external request; the narrow X port serializes bytes.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            host_busy <= 0; host_write <= 0; host_bad <= 0; x_issue_done <= 0;
            host_addr <= 0; host_data <= 0; host_strb <= 0; host_byte <= 0;
            mem_rsp_valid <= 0; response_data <= 0; mem_rsp_error <= 0;
        end else begin
            if (mem_rsp_valid && mem_rsp_ready) mem_rsp_valid <= 0;
            if (mem_req_valid && mem_req_ready) begin
                host_busy <= 1;
                host_write <= mem_req_write;
                host_bad <= |mem_req_addr[1:0] || !request_region_ok;
                host_addr <= mem_req_addr; host_data <= mem_req_wdata;
                host_strb <= mem_req_wstrb; host_byte <= 0; x_issue_done <= 0;
                response_data <= 0; mem_rsp_error <= 0;
            end
            if (host_busy) begin
                if (host_bad) begin
                    mem_rsp_error <= 1; host_busy <= 0; mem_rsp_valid <= 1;
                end else if (host_w || host_y) begin
                    host_busy <= 0; mem_rsp_valid <= 1;
                end else if (host_x && !x_issue_done) begin
                    if (host_byte == 3) begin
                        x_issue_done <= 1;
                        if (host_write) begin host_busy <= 0; mem_rsp_valid <= 1; end
                    end else host_byte <= host_byte + 1'b1;
                end
            end
            if (x_return_valid) begin
                response_data[8*x_return_byte +: 8] <= x_data;
                if (x_return_byte == 3) begin host_busy <= 0; mem_rsp_valid <= 1; end
            end
        end
    end

    wire [7:0] y_word = y_address[9:2];
    wire [1:0] host_bank = host_addr[3:2];
    wire [5:0] host_row = host_addr[9:4];
    wire [31:0] host_y_data [0:3];
    for (genvar bank=0;bank<4;bank=bank+1) begin : result_banks
        logic [31:0] mem [0:63];
        logic [31:0] read_data;
        wire [1:0] lane = 2'(bank) - y_word[1:0];
        wire [7:0] word_index = y_word + {6'b0,lane};
        wire compute_write = y_write && y_mask[lane];
        wire host_enable = host_busy && !host_bad && host_y && host_bank==2'(bank);
        wire port_enable = compute_write || host_enable;
        wire port_write = compute_write || (host_enable && host_write);
        wire [5:0] port_addr = compute_write ? word_index[7:2] : host_row;
        wire [31:0] port_data = compute_write ? y_data[32*lane +: 32] : host_data;
        wire [3:0] port_strb = compute_write ? 4'b1111 : host_strb;
        assign host_y_data[bank] = read_data;
        always_ff @(posedge clk) begin
            if (!rst_n) read_data <= 0;
            else if (port_enable) begin
                if (port_write) begin
                    for (integer b=0;b<4;b=b+1)
                        if (port_strb[b]) mem[port_addr][8*b +: 8] <= port_data[8*b +: 8];
                end else read_data <= mem[port_addr];
            end
        end
    end
    assign mem_rsp_rdata = host_write ? response_data :
                          host_w ? w_data : host_y ? host_y_data[host_bank] : response_data;
endmodule
