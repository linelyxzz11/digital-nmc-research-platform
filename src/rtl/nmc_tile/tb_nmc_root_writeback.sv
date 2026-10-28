`timescale 1ns/1ps
module tb_nmc_root_writeback;
    logic clk=0, rst_n=0;
    always #5 clk=~clk;
    logic task_valid=0;
    wire task_ready;
    logic [7:0] task_version=1, task_opcode=1, task_tile_id=3, task_result_dst_tile=0;
    logic [15:0] task_tid=9, task_m_local=5, task_k_local=3;
    logic [15:0] task_w_base=0, task_x_base=16'h1000, task_y_base=16'h2004, task_global_row_base=8;
    logic [1:0] task_result_kind=0;
    logic task_last=1;
    logic mem_req_valid=0, mem_req_write=0, mem_rsp_ready=0;
    wire mem_req_ready, mem_rsp_valid, mem_rsp_error;
    logic [15:0] mem_req_addr=0;
    logic [31:0] mem_req_wdata=0;
    logic [3:0] mem_req_wstrb=0;
    wire [31:0] mem_rsp_rdata;
    logic root_wr_valid=0;
    wire root_wr_ready, root_wr_error, trace_root_write;
    logic [15:0] root_wr_addr=0;
    logic [3:0] root_wr_mask=0;
    logic [127:0] root_wr_data=0;
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
    nmc_tile_compute #(.TILE_ID(3)) dut (.*);

    string case_dir, wave_path;
    integer trace_fd, memory_fd, request_fd, meta_fd, vector_fd;
    integer phase=0, cycle=0, replay_cycle=-1;
    integer n, expected_error, case_id, length, stimulus_valid, result_holds, done_holds;
    integer host_stimulus, response_ready_stimulus, expected_ready;
    logic [15:0] address;
    logic [3:0] mask;
    logic [127:0] data;
    logic [31:0] returned;
    bit compute_complete;

    // Public interface observations only; SRAM is always checked through Host reads.
    always @(posedge clk) begin
        $fdisplay(trace_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%h,%h,%0d,%0d,%0d,%0d,%0d,%h,%h,%h,%0d,%0d,%h,%0d,%0d,%0d,%h,%h,%0d,%0d,%0d,%h",
            cycle,phase,replay_cycle,rst_n,root_wr_valid,root_wr_ready,root_wr_error,trace_root_write,
            root_wr_addr,root_wr_mask,root_wr_data,trace_state,task_valid,task_ready,
            mem_req_valid,mem_req_ready,mem_req_addr,mem_req_wdata,mem_req_wstrb,mem_req_write,
            mem_rsp_valid,mem_rsp_rdata,mem_rsp_ready,result_valid,result_ready,result_base_index,
            result_data,local_done_valid,local_done_ready,trace_y_write,trace_y_mask);
        if (!rst_n && (root_wr_ready || root_wr_error || trace_root_write))
            $fatal(1,"Root write activity during reset");
        if (root_wr_valid && (trace_state!=0 || mem_req_valid || mem_rsp_valid) && root_wr_ready)
            $fatal(1,"Root accepted while Compute/Host owns SRAM");
        if (root_wr_valid && task_ready) $fatal(1,"task raced a pending Root write");
        cycle=cycle+1;
    end

    task automatic host_access(input bit writing, input logic [15:0] a,
        input logic [31:0] d, input logic [3:0] strobe, output logic [31:0] value);
        begin
            @(negedge clk);
            mem_req_valid=1; mem_req_write=writing; mem_req_addr=a;
            mem_req_wdata=d; mem_req_wstrb=strobe; mem_rsp_ready=0;
            while (!mem_req_ready) @(negedge clk);
            @(posedge clk);
            @(negedge clk); mem_req_valid=0;
            while (!mem_rsp_valid) @(negedge clk);
            if (mem_rsp_error) $fatal(1,"unexpected Host error");
            value=mem_rsp_rdata; mem_rsp_ready=1;
            @(posedge clk);
            @(negedge clk); mem_rsp_ready=0;
        end
    endtask

    task automatic readback;
        begin
            for (integer word_index=0;word_index<256;word_index=word_index+1) begin
                host_access(0,16'h2000+16'(4*word_index),0,0,returned);
                $fdisplay(memory_fd,"%0d,%h,%h",phase,16'h2000+16'(4*word_index),returned);
            end
        end
    endtask

    task automatic clear_result;
        begin
            for (integer word_index=0;word_index<256;word_index=word_index+1)
                host_access(1,16'h2000+16'(4*word_index),32'h5a5a5a5a,15,returned);
        end
    endtask

    task automatic host_contends(input logic [15:0] a, input bit writing);
        logic [32:0] held;
        begin
            @(negedge clk);
            root_wr_valid=1; root_wr_addr=16'h2200+16'(4*a[3:2]); root_wr_mask=15;
            root_wr_data={4{32'h81234567 ^ {16'b0,a}}}; task_valid=1;
            mem_req_valid=1; mem_req_write=writing; mem_req_addr=a;
            mem_req_wdata=32'h00112233; mem_req_wstrb=5; mem_rsp_ready=0;
            if (!mem_req_ready) $fatal(1,"Host did not win idle arbitration");
            @(posedge clk);
            @(negedge clk); mem_req_valid=0; mem_req_addr=16'hffff;
            while (!mem_rsp_valid) @(negedge clk);
            held={mem_rsp_error,mem_rsp_rdata};
            repeat (5) begin
                @(posedge clk);
                if ({mem_rsp_error,mem_rsp_rdata} !== held || root_wr_ready)
                    $fatal(1,"held Host response changed or Root bypassed it");
                @(negedge clk);
            end
            mem_rsp_ready=1;
            @(posedge clk);
            @(negedge clk); mem_rsp_ready=0;
            if (!root_wr_ready) $fatal(1,"Root did not resume after Host response");
            @(posedge clk);
            if (root_wr_error || !trace_root_write) $fatal(1,"Root write lost after Host");
            @(negedge clk); root_wr_valid=0; task_valid=0;
        end
    endtask

    initial begin
        if (!$value$plusargs("CASE_DIR=%s",case_dir)) $fatal(1,"CASE_DIR required");
        trace_fd=$fopen({case_dir,"/trace.csv"},"w");
        memory_fd=$fopen({case_dir,"/memory.csv"},"w");
        $fdisplay(trace_fd,"cycle,phase,replay_cycle,rst,root_valid,root_ready,root_error,root_commit,root_addr,root_mask,root_data,state,task_valid,task_ready,mem_valid,mem_ready,mem_addr,mem_data,mem_strb,mem_write,rsp_valid,rsp_data,rsp_ready,result_valid,result_ready,result_base,result_data,done_valid,done_ready,compute_write,compute_mask");
        $fdisplay(memory_fd,"phase,address,data");
        if ($value$plusargs("VCD=%s",wave_path)) begin $dumpfile(wave_path); $dumpvars(0,tb_nmc_root_writeback); end
        repeat (2) @(posedge clk);
        @(negedge clk); rst_n=1;
        clear_result();
        host_access(1,16'h0000,32'hdd33bb11,15,returned);
        host_access(1,16'h1000,32'hdd33bb11,15,returned);

        // Consecutive vectors, all bank rotations/masks, and atomic invalid requests.
        phase=1;
        request_fd=$fopen({case_dir,"/requests.txt"},"r");
        @(negedge clk); task_valid=1;
        n=$fscanf(request_fd,"%h %h %h %d\n",address,mask,data,expected_error);
        while (n==4) begin
            root_wr_valid=1; root_wr_addr=address; root_wr_mask=mask; root_wr_data=data;
            #1;
            if (!root_wr_ready) $fatal(1,"idle Root burst stalled");
            @(posedge clk);
            if (root_wr_error !== 1'(expected_error) || trace_root_write === 1'(expected_error))
                $fatal(1,"Root validation/commit mismatch");
            @(negedge clk);
            // Read immediately after every rejection, before a later valid
            // request could hide a partially committed invalid vector.
            if (expected_error!=0) begin
                root_wr_valid=0; task_valid=0;
                readback(); task_valid=1;
            end
            n=$fscanf(request_fd,"%h %h %h %d\n",address,mask,data,expected_error);
        end
        root_wr_valid=0; task_valid=0; $fclose(request_fd);
        readback();

        phase=2;
        host_contends(16'h2100,0); host_contends(16'h2104,1);
        host_contends(16'h1000,0); host_contends(16'h0000,0);
        readback();

        // Load the independent 5x3 example, then park Root throughout the task.
        phase=0;
        host_access(1,16'h0000,32'h0003fe01,15,returned);
        host_access(1,16'h0004,32'h05ff0002,15,returned);
        host_access(1,16'h0008,32'hfe0204ff,15,returned);
        host_access(1,16'h000c,32'h000000ff,15,returned);
        host_access(1,16'h0010,32'h00000002,15,returned);
        host_access(1,16'h0014,32'h00000001,15,returned);
        host_access(1,16'h1000,32'h0003ff02,15,returned);
        @(negedge clk); phase=3; task_valid=1;
        @(posedge clk);
        if (!task_ready) $fatal(1,"Compute task not accepted");
        @(negedge clk); task_valid=0; root_wr_valid=1; root_wr_addr=16'h2300;
        root_wr_mask=15; root_wr_data=128'hdeadbeef_80000000_7fffffff_ffffffff;
        result_holds=0; done_holds=0; compute_complete=0;
        while (!compute_complete) begin
            result_ready=result_holds>=3; local_done_ready=done_holds>=3;
            @(posedge clk);
            if (root_wr_ready) $fatal(1,"Root bypassed an active Compute task");
            if (result_valid) begin
                if (result_ready) result_holds=0; else result_holds=result_holds+1;
            end
            if (local_done_valid && !local_done_ready) done_holds=done_holds+1;
            compute_complete=local_done_valid && local_done_ready;
            @(negedge clk);
        end
        result_ready=0; local_done_ready=0;
        @(posedge clk);
        if (!root_wr_ready || !trace_root_write) $fatal(1,"Root did not resume after Compute");
        @(negedge clk); root_wr_valid=0;
        readback();

        // An accepted vector survives the very next reset edge.
        phase=4;
        @(negedge clk); root_wr_valid=1; root_wr_addr=16'h23fc; root_wr_mask=1;
        root_wr_data=128'h80000000;
        @(posedge clk);
        @(negedge clk); rst_n=0; root_wr_addr=16'h2340; root_wr_mask=15;
        repeat (2) @(posedge clk);
        @(negedge clk); root_wr_valid=0; rst_n=1;
        // Cancel a Root request blocked behind a held Host response.
        @(negedge clk); mem_req_valid=1; mem_req_write=0; mem_req_addr=16'h23fc;
        @(posedge clk);
        @(negedge clk); mem_req_valid=0; root_wr_valid=1; root_wr_addr=16'h2350;
        while (!mem_rsp_valid) @(negedge clk);
        repeat (3) @(posedge clk);
        @(negedge clk); rst_n=0;
        @(posedge clk);
        @(negedge clk); root_wr_valid=0; rst_n=1;
        readback();

        // Replay Python WRITE_RESULT edges with the same local cycle numbering.
        if (wave_path!="") $dumpoff;
        meta_fd=$fopen({case_dir,"/calibration_cases.txt"},"r");
        n=$fscanf(meta_fd,"%d %d\n",case_id,length);
        while (n==2) begin
            phase=0; clear_result(); phase=10+case_id;
            vector_fd=$fopen($sformatf("%s/calibration_%0d.txt",case_dir,case_id),"r");
            for (integer c=0;c<length;c=c+1) begin
                @(negedge clk); replay_cycle=c;
                if ($fscanf(vector_fd,"%d %h %h %h %d %d %d\n",stimulus_valid,address,mask,data,
                            host_stimulus,response_ready_stimulus,expected_ready)!=7)
                    $fatal(1,"short calibration stimulus");
                root_wr_valid=1'(stimulus_valid); root_wr_addr=address;
                root_wr_mask=mask; root_wr_data=data;
                mem_req_valid=1'(host_stimulus); mem_req_write=0; mem_req_addr=16'h2000;
                mem_rsp_ready=1'(response_ready_stimulus);
                @(posedge clk);
                if (root_wr_ready!==1'(expected_ready) || root_wr_error ||
                    trace_root_write!==(root_wr_valid && root_wr_ready))
                    $fatal(1,"Root write replay stalled or failed");
            end
            @(negedge clk); root_wr_valid=0; replay_cycle=-1; mem_req_valid=0; mem_rsp_ready=0;
            $fclose(vector_fd); readback();
            n=$fscanf(meta_fd,"%d %d\n",case_id,length);
        end
        $fclose(meta_fd); $fclose(trace_fd); $fclose(memory_fd);
        $display("ROOT WRITEBACK RTL PASS"); $finish;
    end

    initial begin #10000000; $fatal(1,"Root writeback timeout"); end
endmodule
