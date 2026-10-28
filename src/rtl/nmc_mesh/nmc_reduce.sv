`timescale 1ns/1ps
// Single transaction; two RX packet slots, one active vector, one held output.
// Default four-lane READ/ADD/UPDATE/WRITE stages mirror TimedRoot's services.
module nmc_reduce #(
    parameter integer TILE_ID=0, TILE_COUNT=64
) (
    input wire clk,rst_n,
    input wire enabled, reduce_mode, forward_result,
    input wire [7:0] parent,
    input wire [15:0] tid,output_base,output_count,
    input wire [63:0] source_mask,
    input wire mask_cfg_valid,
    input wire [7:0] mask_cfg_index,
    input wire [63:0] mask_cfg_sources,
    input wire rx_valid,
    output wire rx_ready,
    input wire [127:0] rx_header,rx_payload,
    output wire write_valid,
    input wire write_ready,
    output logic [15:0] write_addr,
    output logic [3:0] write_mask,
    output logic [127:0] write_data,
    output wire tx_valid,
    input wire tx_ready,
    output wire [127:0] tx_header,tx_payload,
    output logic done_valid,
    input wire done_ready,
    output wire idle,
    output logic error,
    output wire [3:0] trace_state,
    output wire [1:0] trace_rx_count
);
    localparam [3:0] IDLE=0,READ=1,ADD=2,UPDATE=3,WRITE=4,PACKET_DONE=5,CREATE_DONE=6;
    logic [3:0] state;
    logic [127:0] headers[0:1],payloads[0:1],active_header,active_payload;
    logic [1:0] count;
    logic rd,wr;
    logic [63:0] expected[0:255],accepted[0:255],received[0:255];
    logic [31:0] accumulator[0:255];
    logic [63:0] accepted_last,last_seen;
    logic [31:0] read_value[0:3],added_value[0:3];
    logic [63:0] read_mask[0:3],added_mask[0:3];
    logic [3:0] complete_mask;
    logic [15:0] completed_words;
    logic completed,output_valid,output_last;
    logic [127:0] output_header,output_payload;
    integer active_base,active_source;
    logic [3:0] active_mask;
    wire accept=rx_valid && rx_ready;
    wire dispatch=enabled && state==IDLE && count!=0;
    wire write_fire=write_valid && write_ready;
    wire output_space=!forward_result || !output_valid;
    wire output_fire=tx_valid && tx_ready;
    logic bad_rx;
    integer incoming_base,incoming_source,first_lane;
    logic [3:0] incoming_mask;
    wire [15:0] next_completed=completed_words+16'($countones(write_mask));

    assign rx_ready=rst_n && enabled && count<2 && !error && !completed;
    assign write_valid=rst_n && state==WRITE && complete_mask!=0 && output_space;
    assign tx_valid=rst_n && output_valid && (!output_last || completed);
    assign tx_header=output_header;
    assign tx_payload=output_payload;
    assign idle=count==0 && state==IDLE && !output_valid && !done_valid;
    assign trace_state=state;
    assign trace_rx_count=count;

    always @* begin
        incoming_base=int'(rx_header[59:44]);
        incoming_source=int'(rx_header[11:4]);
        incoming_mask=rx_header[63:60];
        bad_rx=rx_header[3:0]!=1 || rx_header[19:12]!=8'(TILE_ID) ||
               rx_header[35:20]!=tid || rx_header[43:36]!=0 ||
               rx_header[64]!=reduce_mode || |rx_header[127:66] || incoming_mask==0 ||
               incoming_source>=TILE_COUNT || !source_mask[incoming_source%64] ||
               accepted_last[incoming_source%64];
        for (integer lane=0;lane<4;lane=lane+1) begin
            if (incoming_mask[lane]) begin
                if (incoming_base+lane<int'(output_base) ||
                    incoming_base+lane>=int'(output_base)+int'(output_count) || incoming_base+lane>255)
                    bad_rx=1;
                else if (!expected[incoming_base+lane][incoming_source%64] ||
                         accepted[incoming_base+lane][incoming_source%64] ||
                         (!reduce_mode && accepted[incoming_base+lane]!=0)) bad_rx=1;
            end else if (rx_payload[32*lane +: 32]!=0) bad_rx=1;
        end
        active_base=int'(active_header[59:44]);
        active_source=int'(active_header[11:4]);
        active_mask=active_header[63:60];
        first_lane=0;
        for (integer lane=3;lane>=0;lane=lane-1)
            if (complete_mask[lane]) first_lane=lane;
        write_addr=16'h2000+16'(4*(active_base+first_lane-int'(output_base)));
        write_mask=complete_mask >> first_lane;
        write_data=0;
        for (integer lane=0;lane<4;lane=lane+1)
            if (complete_mask[lane]) write_data[32*(lane-first_lane) +: 32]=added_value[lane];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state<=IDLE;count<=0;rd<=0;wr<=0;error<=0;
            active_header<=0;active_payload<=0;accepted_last<=0;last_seen<=0;
            completed_words<=0;completed<=0;complete_mask<=0;
            done_valid<=0;output_valid<=0;output_last<=0;output_header<=0;output_payload<=0;
            for (integer i=0;i<256;i=i+1) begin
                expected[i]<=0;accepted[i]<=0;received[i]<=0;accumulator[i]<=0;
            end
            for (integer lane=0;lane<4;lane=lane+1) begin
                read_value[lane]<=0;added_value[lane]<=0;read_mask[lane]<=0;added_mask[lane]<=0;
            end
        end else begin
            if (mask_cfg_valid) expected[mask_cfg_index]<=mask_cfg_sources;
            if (done_valid && done_ready) done_valid<=0;
            if (output_fire) output_valid<=0;
            case ({accept,dispatch})
                2'b10: count<=count+1'b1;
                2'b01: count<=count-1'b1;
                default: count<=count;
            endcase
            if (accept) begin
                if (bad_rx) error<=1;
                else begin
                    headers[wr]<=rx_header;payloads[wr]<=rx_payload;wr<=!wr;
                    for (integer lane=0;lane<4;lane=lane+1)
                        if (incoming_mask[lane])
                            accepted[incoming_base+lane]<=accepted[incoming_base+lane] | (64'b1<<incoming_source);
                    if (rx_header[65]) accepted_last<=accepted_last | (64'b1<<incoming_source);
                end
            end
            if (dispatch) begin
                active_header<=headers[rd];active_payload<=payloads[rd];rd<=!rd;
                complete_mask<=reduce_mode?4'b0:headers[rd][63:60];
                for (integer lane=0;lane<4;lane=lane+1)
                    added_value[lane]<=payloads[rd][32*lane +: 32];
                state<=reduce_mode?READ:WRITE;
            end else case (state)
                READ: begin
                    for (integer lane=0;lane<4;lane=lane+1) if (active_mask[lane]) begin
                        read_value[lane]<=accumulator[active_base+lane];
                        read_mask[lane]<=received[active_base+lane];
                    end
                    state<=ADD;
                end
                ADD: begin
                    for (integer lane=0;lane<4;lane=lane+1) if (active_mask[lane]) begin
                        added_value[lane]<=read_value[lane]+active_payload[32*lane +: 32];
                        added_mask[lane]<=read_mask[lane] | (64'b1<<active_source);
                    end
                    state<=UPDATE;
                end
                UPDATE: begin
                    for (integer lane=0;lane<4;lane=lane+1) if (active_mask[lane]) begin
                        accumulator[active_base+lane]<=added_value[lane];
                        received[active_base+lane]<=added_mask[lane];
                        complete_mask[lane]<=added_mask[lane]==expected[active_base+lane];
                    end
                    // The branch uses freshly computed masks, not last cycle's complete_mask.
                    state<=PACKET_DONE;
                    for (integer lane=0;lane<4;lane=lane+1)
                        if (active_mask[lane] && added_mask[lane]==expected[active_base+lane]) state<=WRITE;
                end
                WRITE: if (write_fire) begin
                    completed_words<=next_completed;
                    if (forward_result) begin
                        output_valid<=1;output_last<=next_completed==output_count;
                        output_header<=0;
                        output_header[3:0]<=1;output_header[11:4]<=8'(TILE_ID);output_header[19:12]<=parent;
                        output_header[35:20]<=tid;output_header[59:44]<=16'(active_base+first_lane);
                        output_header[63:60]<=write_mask;
                        output_header[64]<=parent!=0; // Host/Root is fixed at physical Tile 0.
                        output_header[65]<=next_completed==output_count;
                        output_payload<=write_data;
                    end
                    complete_mask<=0;
                    state<=PACKET_DONE;
                end
                PACKET_DONE: begin
                    if (active_header[65]) last_seen<=last_seen | (64'b1<<active_source);
                    if (completed_words==output_count &&
                        (last_seen | (active_header[65]?(64'b1<<active_source):64'b0))==source_mask)
                        state<=CREATE_DONE;
                    else state<=IDLE;
                end
                CREATE_DONE: begin
                    completed<=1;
                    if (!forward_result) done_valid<=1;
                    state<=IDLE;
                end
                default: state<=IDLE;
            endcase
        end
    end
endmodule
