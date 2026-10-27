`timescale 1ns/1ps
module tb_nmc_tile_compute;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    logic task_valid=0;
    wire task_ready;
    logic [7:0] task_version, task_opcode, task_tile_id, task_result_dst_tile;
    logic [15:0] task_tid, task_m_local, task_k_local;
    logic [15:0] task_w_base, task_x_base, task_y_base, task_global_row_base;
    logic [1:0] task_result_kind;
    logic task_last;
    logic mem_req_valid=0, mem_req_write=0, mem_rsp_ready=0;
    wire mem_req_ready, mem_rsp_valid, mem_rsp_error;
    logic [15:0] mem_req_addr=0;
    logic [31:0] mem_req_wdata=0;
    logic [3:0] mem_req_wstrb=0;
    wire [31:0] mem_rsp_rdata;
    wire result_valid, result_last, local_done_valid;
    logic result_ready=0, local_done_ready=0;
    wire [15:0] result_tid, result_base_index, local_done_tid;
    wire [7:0] result_dst_tile;
    wire [3:0] result_lane_mask, local_done_status;
    wire [127:0] result_data;
    wire [1:0] result_kind;
    wire [3:0] trace_state, trace_y_mask;
    wire [15:0] trace_group, trace_k, trace_w_addr, trace_x_addr, trace_y_addr;
    wire trace_rd_req, trace_rd_valid, trace_mac, trace_y_write;
    wire [31:0] trace_w_data;
    wire [7:0] trace_x_data;
    wire [127:0] trace_acc;
    nmc_tile_compute dut (.*);

    string case_dir, trace_path, wave_path;
    integer trace_fd, load_fd, read_fd, count, n, cycle, phase;
    integer result_holds, done_holds;
    integer config_words [0:17];
    logic [15:0] load_addr;
    logic [31:0] load_data, read_data;
    logic [3:0] load_strb;
    logic held_result, held_done;
    logic [174:0] result_snapshot;
    logic [19:0] done_snapshot;
    wire [174:0] result_bundle = {result_tid, result_dst_tile, result_base_index,
                                 result_lane_mask, result_data, result_kind, result_last};

    task automatic access_memory(input bit writing, input logic [15:0] address,
        input logic [31:0] data, input logic [3:0] strb,
        input bit expected_error, input integer response_stalls, output logic [31:0] returned);
        logic [32:0] snapshot;
        integer waited;
        begin
            @(negedge clk);
            mem_req_valid=1; mem_req_write=writing; mem_req_addr=address;
            mem_req_wdata=data; mem_req_wstrb=strb; mem_rsp_ready=0;
            waited=0;
            while (!mem_req_ready) begin
                @(negedge clk);
                waited=waited+1;
                if (waited>50) $fatal(1,"memory request timeout");
            end
            @(posedge clk);
            @(negedge clk); mem_req_valid=0;
            waited=0;
            while (!mem_rsp_valid) begin
                @(negedge clk);
                waited=waited+1;
                if (waited>50) $fatal(1,"memory response timeout");
            end
            if (mem_rsp_error !== expected_error) $fatal(1,"memory error mismatch at %h",address);
            snapshot={mem_rsp_error,mem_rsp_rdata};
            repeat (response_stalls) begin
                @(posedge clk); #1;
                if (!mem_rsp_valid || {mem_rsp_error,mem_rsp_rdata} !== snapshot || task_ready || mem_req_ready)
                    $fatal(1,"memory response unstable or task accepted before response");
            end
            returned=mem_rsp_rdata;
            @(negedge clk); mem_rsp_ready=1;
            @(posedge clk);
            @(negedge clk); mem_rsp_ready=0;
        end
    endtask

    task automatic launch_task;
        begin
            @(negedge clk);
            task_version=8'(config_words[0]); task_opcode=8'(config_words[1]);
            task_tid=16'(config_words[2]); task_tile_id=8'(config_words[3]);
            task_m_local=16'(config_words[4]); task_k_local=16'(config_words[5]);
            task_w_base=16'(config_words[6]); task_x_base=16'(config_words[7]);
            task_y_base=16'(config_words[8]); task_global_row_base=16'(config_words[9]);
            task_result_kind=2'(config_words[10]); task_result_dst_tile=8'(config_words[11]);
            task_last=1'(config_words[12]); task_valid=1;
            while (!task_ready) @(negedge clk);
            @(posedge clk);
            @(negedge clk); task_valid=0;
            // Disturb source fields after acceptance to verify descriptor latching.
            task_m_local=16'hffff; task_k_local=0; task_tid=16'hdead;
            while (trace_state != 2 && trace_state != 6) @(negedge clk);
        end
    endtask

    task automatic run_task(input integer run_phase, input bit allow_reset);
        bit complete, aborting;
        begin
            launch_task();
            cycle=0; result_holds=0; done_holds=0; complete=0;
            held_result=0; held_done=0;
            while (!complete) begin
                if (cycle>30000) $fatal(1,"task timeout");
                aborting=allow_reset && cycle==config_words[15];
                result_ready=result_holds>=config_words[13];
                local_done_ready=done_holds>=config_words[14];
                if (result_valid && !result_ready) result_holds=result_holds+1;
                if (local_done_valid && !local_done_ready) done_holds=done_holds+1;
                if (config_words[17]!=0) begin
                    mem_req_valid=1; mem_req_write=1; mem_req_addr=0;
                    mem_req_wdata=32'hffffffff; mem_req_wstrb=15; task_valid=1;
                end
                if (aborting) rst_n=0;
                @(posedge clk);
                if (!aborting) begin
                    if (task_ready || mem_req_ready) $fatal(1,"busy Tile accepted another request");
                    if (held_result && (!result_valid || result_bundle !== result_snapshot))
                        $fatal(1,"ResultBeat changed under backpressure");
                    if (held_done && (!local_done_valid || {local_done_tid,local_done_status} !== done_snapshot))
                        $fatal(1,"LocalDone changed under backpressure");
                    held_result=result_valid && !result_ready; result_snapshot=result_bundle;
                    held_done=local_done_valid && !local_done_ready;
                    done_snapshot={local_done_tid,local_done_status};
                    if (result_valid && (trace_rd_req || trace_mac || trace_y_write))
                        $fatal(1,"next-group work while result is pending");
                    $fwrite(trace_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%h,%h,%h,%h,%h,",
                        run_phase,cycle,trace_state,trace_group,trace_k,trace_rd_req,trace_rd_valid,
                        trace_mac,trace_y_write,trace_w_addr,trace_x_addr,trace_y_addr,trace_y_mask,
                        trace_w_data,trace_x_data,trace_acc);
                    $fwrite(trace_fd,"%0d,%0d,%h,%h,%h,%h,%h,%h,%0d,%0d,%0d,%h,%h,",
                        result_valid,result_ready,result_tid,result_dst_tile,result_base_index,
                        result_lane_mask,result_data,result_kind,result_last,
                        local_done_valid,local_done_ready,local_done_tid,local_done_status);
                    complete=local_done_valid && local_done_ready;
                    if (result_valid && result_ready) result_holds=0;
                end
                #1;
                if (aborting) begin
                    if (result_valid || local_done_valid || trace_state!=0 || trace_acc!==128'b0)
                        $fatal(1,"reset failed to flush task/result/accumulator");
                    complete=1;
                end else $fwrite(trace_fd,"%h\n",trace_acc);
                @(negedge clk);
                cycle=cycle+1;
            end
            mem_req_valid=0; task_valid=0; result_ready=0; local_done_ready=0;
            if (allow_reset && config_words[15]>=0) rst_n=1;
        end
    endtask

    task automatic dump_memory(input integer run_phase);
        begin
            for (integer word_index=0; word_index<256; word_index=word_index+1) begin
                access_memory(0,16'h2000+16'(4*word_index),0,0,0,0,read_data);
                $fdisplay(read_fd,"%0d,%h,%h",run_phase,16'h2000+16'(4*word_index),read_data);
            end
            // These source words detect any illegally accepted busy write.
            access_memory(0,0,0,0,0,0,read_data);
            $fdisplay(read_fd,"%0d,0000,%h",run_phase,read_data);
            access_memory(0,16'h1000,0,0,0,0,read_data);
            $fdisplay(read_fd,"%0d,1000,%h",run_phase,read_data);
        end
    endtask

    initial begin
        if (!$value$plusargs("CASE_DIR=%s",case_dir)) $fatal(1,"missing CASE_DIR");
        $readmemh({case_dir,"/config.hex"},config_words,0,17);
        if ($value$plusargs("VCD=%s",wave_path)) begin $dumpfile(wave_path); $dumpvars(0,dut); end
        trace_fd=$fopen({case_dir,"/trace.csv"},"w");
        read_fd=$fopen({case_dir,"/memory.csv"},"w");
        $fdisplay(trace_fd,"phase,cycle,state,group,k,rd_req,rd_valid,mac,y_write,w_addr,x_addr,y_addr,y_mask,w_data,x_data,acc_before,result_valid,result_ready,result_tid,result_dst,result_base,result_mask,result_data,result_kind,result_last,done_valid,done_ready,done_tid,done_status,acc_after");
        $fdisplay(read_fd,"phase,address,data");
        repeat (2) @(posedge clk);
        @(negedge clk); rst_n=1;
        // Reset cancels an in-flight narrow-port write; committed bytes remain.
        access_memory(1,16'h1000,32'h11223344,15,0,0,read_data);
        @(negedge clk);
        mem_req_valid=1; mem_req_write=1; mem_req_addr=16'h1000;
        mem_req_wdata=32'haabbccdd; mem_req_wstrb=15;
        @(posedge clk);
        @(negedge clk); mem_req_valid=0;
        @(posedge clk); // byte zero commits here
        @(negedge clk); rst_n=0;
        @(posedge clk); #1;
        if (mem_rsp_valid || result_valid || local_done_valid) $fatal(1,"reset left a stale response");
        @(negedge clk); rst_n=1;
        access_memory(0,16'h1000,0,0,0,0,read_data);
        if (read_data!==32'h112233dd) $fatal(1,"reset did not preserve only committed bytes");
        // Masked byte writes, serialized X accesses and response backpressure.
        access_memory(1,0,32'h11223344,15,0,0,read_data);
        access_memory(1,0,32'haabbccdd,5,0,0,read_data);
        access_memory(0,0,0,0,0,3,read_data);
        if (read_data!==32'h11bb33dd) $fatal(1,"Weight byte enables");
        access_memory(1,16'h1000,32'h11223344,15,0,0,read_data);
        access_memory(1,16'h1000,32'haabbccdd,5,0,0,read_data);
        access_memory(0,16'h1000,0,0,0,3,read_data);
        if (read_data!==32'h11bb33dd) $fatal(1,"Input byte enables");
        access_memory(1,16'h2000,32'h11223344,15,0,0,read_data);
        access_memory(1,16'h2000,32'haabbccdd,5,0,0,read_data);
        access_memory(0,16'h2000,0,0,0,3,read_data);
        if (read_data!==32'h11bb33dd) $fatal(1,"Result byte enables");
        access_memory(1,16'h2001,32'hffffffff,15,1,2,read_data);
        access_memory(1,16'h1400,32'hffffffff,15,1,2,read_data);
        access_memory(0,16'h2000,0,0,0,0,read_data);
        if (read_data!==32'h11bb33dd) $fatal(1,"invalid memory write modified SRAM");
        for (integer i=0;i<256;i=i+1)
            access_memory(1,16'h2000+16'(4*i),32'h5a5a5a5a,15,0,0,read_data);
        load_fd=$fopen({case_dir,"/writes.txt"},"r");
        while (!$feof(load_fd)) begin
            n=$fscanf(load_fd,"%h %h %h\n",load_addr,load_data,load_strb);
            if (n==3) access_memory(1,load_addr,load_data,load_strb,0,0,read_data);
            else if (!$feof(load_fd)) $fatal(1,"malformed preload file");
        end
        $fclose(load_fd);
        run_task(0,config_words[15]>=0);
        dump_memory(0);
        if (config_words[15]>=0) begin
            run_task(1,0);
            dump_memory(1);
        end
        $fclose(trace_fd); $fclose(read_fd);
        $display("TILE RTL PASS");
        $finish;
    end
    initial begin #10000000; $fatal(1,"simulation watchdog"); end
endmodule
