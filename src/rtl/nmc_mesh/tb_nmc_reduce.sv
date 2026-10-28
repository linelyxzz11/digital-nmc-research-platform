`timescale 1ns/1ps
module tb_nmc_reduce;
    logic clk=0,rst_n=0;
    always #5 clk=~clk;
    logic enabled=1,reduce_mode=1,forward_result=0;
    logic [7:0] parent=0;
    logic [15:0] tid=9,output_base=5,output_count=5;
    logic [63:0] source_mask=64'h8000000080000000;
    logic mask_cfg_valid=0;
    logic [7:0] mask_cfg_index=0;
    logic [63:0] mask_cfg_sources=64'h8000000080000000;
    logic rx_valid=0,write_ready=0,tx_ready=0,done_ready=0;
    logic [127:0] rx_header=0,rx_payload=0;
    wire rx_ready,write_valid,tx_valid,done_valid,idle,error;
    wire [15:0] write_addr;
    wire [3:0] write_mask,trace_state;
    wire [127:0] write_data,tx_header,tx_payload;
    wire [1:0] trace_rx_count;
    nmc_reduce #(.TILE_ID(3)) dut(.*);
    string directory;
    integer fd,cycle=0,sent=0,negative_cases=0;
    logic recording=0;
    logic [127:0] packets_h[0:3],packets_p[0:3];
    function automatic [127:0] header(input integer source,input integer base,input logic [3:0] mask,input bit last);
        reg [127:0] h;
        begin
            h=0;h[3:0]=1;h[11:4]=8'(source);h[19:12]=3;h[35:20]=9;
            h[59:44]=16'(base);h[63:60]=mask;h[64]=1;h[65]=last;header=h;
        end
    endfunction
    task automatic reset_and_configure;
        begin
            @(negedge clk);rst_n=0;rx_valid=0;
            repeat (2) @(negedge clk);rst_n=1;
            for (integer i=5;i<10;i=i+1) begin
                mask_cfg_index=8'(i);mask_cfg_valid=1;
                @(posedge clk);@(negedge clk);
            end
            mask_cfg_valid=0;
        end
    endtask
    task automatic send(input logic [127:0] h,input logic [127:0] p);
        begin
            @(negedge clk);rx_header=h;rx_payload=p;rx_valid=1;
            #1;while (!rx_ready) @(negedge clk);
            @(posedge clk);@(negedge clk);rx_valid=0;
        end
    endtask
    always @(posedge clk) if (rst_n && recording) begin
        $fdisplay(fd,"%0d,%0d,%0d,%032h,%032h,%0d,%0d,%0d,%0d,%04h,%x,%032h,%0d,%0d",cycle,
            rx_valid,rx_ready,rx_header,rx_payload,trace_state,trace_rx_count,write_valid,write_ready,
            write_addr,write_mask,write_data,done_valid,done_ready);
        cycle=cycle+1;
        if (error) $fatal(1,"unexpected reduction protocol error");
    end
    initial begin
        if (!$value$plusargs("CASE_DIR=%s",directory)) $fatal(1,"CASE_DIR required");
        fd=$fopen({directory,"/trace.csv"},"w");
        $fdisplay(fd,"cycle,rx_valid,rx_ready,header,payload,state,queued,write_valid,write_ready,address,mask,data,done_valid,done_ready");
        packets_h[0]=header(31,5,15,0);packets_p[0]={32'hfffffffc,32'd256,32'hffffff80,32'h7fffffff};
        packets_h[1]=header(63,5,15,0);packets_p[1]={32'd5,32'hfffffefc,32'd127,32'd5};
        packets_h[2]=header(31,9,1,1);packets_p[2]=7;
        packets_h[3]=header(63,9,1,1);packets_p[3]=128'h000000000000000000000000fffffff8;
        reset_and_configure();recording=1;
        while (!(done_valid && cycle>40)) begin
            rx_valid=sent<4;
            if (sent<4) begin rx_header=packets_h[sent];rx_payload=packets_p[sent];end
            write_ready=cycle>=20 && cycle%7!=0;
            @(posedge clk);
            if (rx_valid && rx_ready) sent=sent+1;
            @(negedge clk);
            if (cycle>100) $fatal(1,"reduction service timeout");
        end
        repeat (3) @(negedge clk);done_ready=1;
        @(posedge clk);@(negedge clk);recording=0;
        if (!idle) $fatal(1,"reduction failed to drain");
        $fclose(fd);
        // Each malformed packet must terminate atomically before service.
        for (integer bad=0;bad<5;bad=bad+1) begin
            reset_and_configure();rx_header=header(31,5,1,0);rx_payload=1;
            case (bad)
                0: rx_header[11:4]=2;
                1: rx_header[64]=0;
                2: rx_header[59:44]=260;
                3: rx_payload[63:32]=1;
                4: begin send(rx_header,rx_payload);end
            endcase
            send(rx_header,rx_payload);
            if (!error || write_valid) $fatal(1,"malformed reduction packet accepted: %0d",bad);
            negative_cases=negative_cases+1;
        end
        $display("REDUCE RTL PASS: %0d malformed cases",negative_cases);$finish;
    end
    initial begin #100000; $fatal(1,"endpoint timeout");end
endmodule
