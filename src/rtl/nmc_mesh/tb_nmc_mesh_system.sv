`timescale 1ns/1ps
module tb_nmc_mesh_system;
    localparam integer N=64;
    logic clk=0,rst_n=0;
    always #5 clk=~clk;
    initial begin #100000000; $fatal(1,"global RTL setup/execution timeout"); end
    logic [N-1:0] task_valid=0,compute_mask=0,result_psum=0;
    wire [N-1:0] task_ready,local_done;
    logic [15:0] local_m[0:N-1],local_k[0:N-1],row_base[0:N-1];
    logic [7:0] result_dst[0:N-1],endpoint_parent[0:N-1];
    logic [N-1:0] endpoint_enable=0,endpoint_reduce=0,endpoint_forward=0;
    logic [15:0] endpoint_base[0:N-1],endpoint_count[0:N-1];
    logic [63:0] endpoint_sources[0:N-1];
    logic mask_cfg_valid=0;
    logic [7:0] mask_cfg_tile=0,mask_cfg_index=0;
    logic [63:0] mask_cfg_sources=0;
    logic host_packet_valid=0,host_packet_two=1,host_response_ready=1;
    wire host_packet_ready,host_response_valid;
    logic [127:0] host_header=0,host_payload=0;
    wire [127:0] host_response;
    logic [N-1:0] eject_enable='1;
    logic memory_select=0,memory_valid=0,memory_write=0,memory_response_ready=0;
    logic [7:0] memory_tile=0;
    logic [15:0] memory_address=0;
    logic [31:0] memory_data=0;
    logic [3:0] memory_strb=0;
    wire memory_ready,memory_response_valid,memory_error;
    wire [31:0] memory_response;
    wire idle,error;
    wire [N-1:0] trace_rx_valid,trace_rx_ready,trace_result_valid,trace_result_ready;
    wire [129:0] trace_rx_flit[0:N-1];
    nmc_mesh_system dut(.*);
    string case_dir,wave_path;
    integer config_file,weights_file,masks_file,inputs_file,trace_fd,summary_fd,read_fd;
    integer code,tile,m,k,base,destination,psum,forwarding,reducing,enabled,outputs;
    integer cycle=0,load_start=0,compute_start=0,finish_cycle=0,phase=0;
    integer config_words[0:3],stall_until=0,abort_cycle=-1;
    integer acks=0,dones=0,phase_done_count=0;
    logic recording=0;
    logic [63:0] mask;
    logic [15:0] address;
    logic [31:0] data,returned;
    logic [3:0] strb;
    logic [127:0] packet_h,packet_p;
    wire abort_now=recording && phase==0 && abort_cycle>=0 && cycle-compute_start==abort_cycle;

    for (genvar t=0;t<N;t=t+1) begin: checks
        logic held=0;
        logic [174:0] snapshot;
        wire [174:0] bundle={dut.result_tid[t],dut.result_destination[t],dut.result_base[t],
                             dut.result_mask[t],dut.result_data[t],dut.result_kind[t],dut.result_last[t]};
        logic out_held=0;
        logic [129:0] out_snapshot;
        always @(posedge clk) begin
            if (!rst_n) begin held<=0;out_held<=0;end
            else begin
                if (held && (!trace_result_valid[t] || bundle!==snapshot))
                    $fatal(1,"Tile %0d ResultBeat changed while blocked",t);
                held<=trace_result_valid[t] && !trace_result_ready[t];snapshot<=bundle;
                if (out_held && (!trace_rx_valid[t] || trace_rx_flit[t]!==out_snapshot))
                    $fatal(1,"Tile %0d routed flit changed while blocked",t);
                out_held<=trace_rx_valid[t] && !trace_rx_ready[t];out_snapshot<=trace_rx_flit[t];
                for (integer p=0;p<5;p=p+1)
                    if (dut.mesh.routers[t].router.count[p]<0 || dut.mesh.routers[t].router.count[p]>4)
                        $fatal(1,"router FIFO bound violated");
                if (dut.nodes[t].ni.count<0 || dut.nodes[t].ni.count>4)
                    $fatal(1,"NI bound violated");
                if (dut.nodes[t].endpoint.count>2) $fatal(1,"endpoint queue bound violated");
            end
        end
    end
    always @(posedge clk) if (rst_n && recording) begin
        if (error) $fatal(1,"integrated protocol error at cycle %0d",cycle);
        for (integer t=0;t<N;t=t+1) if (trace_rx_valid[t] && trace_rx_ready[t])
            $fdisplay(trace_fd,"%0d,%0d,%0d,%0d,%032h",phase,cycle,t,trace_rx_flit[t][129:128],trace_rx_flit[t][127:0]);
        if (host_response_valid && host_response_ready) begin
            if (host_response[3:0]==3) acks=acks+1;
            else if (host_response[3:0]==4) begin dones=dones+1;phase_done_count=phase_done_count+1;end
        end
        cycle=cycle+1;
        if (cycle>250000) $fatal(1,"integrated timeout");
    end

    task automatic access_memory(input bit writing,input integer node,input logic [15:0] addr,
                                 input logic [31:0] value,input logic [3:0] byte_mask,
                                 output logic [31:0] result);
        begin
            @(negedge clk);memory_select=1;memory_valid=1;memory_tile=8'(node);
            memory_write=writing;memory_address=addr;memory_data=value;memory_strb=byte_mask;
            memory_response_ready=0;
            #1; // Let the selected-node ready mux settle before its first edge.
            while (!memory_ready) @(negedge clk);
            @(posedge clk);@(negedge clk);memory_valid=0;
            while (!memory_response_valid) @(negedge clk);
            if (memory_error) $fatal(1,"memory error Tile %0d address %h",node,addr);
            result=memory_response;
            memory_response_ready=1;
            @(posedge clk);@(negedge clk);memory_response_ready=0;memory_select=0;
        end
    endtask
    task automatic configure_masks;
        begin
            masks_file=$fopen({case_dir,"/masks.txt"},"r");
            while (!$feof(masks_file)) begin
                code=$fscanf(masks_file,"%d %d %h\n",tile,base,mask);
                if (code==3) begin
                    @(negedge clk);mask_cfg_valid=1;mask_cfg_tile=8'(tile);mask_cfg_index=8'(base);mask_cfg_sources=mask;
                    @(posedge clk);
                end
            end
            @(negedge clk);mask_cfg_valid=0;$fclose(masks_file);
        end
    endtask
    task automatic load_inputs;
        begin
            inputs_file=$fopen({case_dir,"/inputs.txt"},"r");
            load_start=cycle;
            while (!$feof(inputs_file)) begin
                code=$fscanf(inputs_file,"%h %h\n",packet_h,packet_p);
                if (code==2) begin
                    @(negedge clk);host_packet_valid=1;host_header=packet_h;host_payload=packet_p;
                    #1;
                    while (!host_packet_ready) @(negedge clk);
                    @(posedge clk);@(negedge clk);host_packet_valid=0;
                    while (!host_response_valid) @(negedge clk);
                    if (host_response[3:0]!=3 || host_response[11:4]!=packet_h[19:12] ||
                        host_response[35:20]!=packet_h[35:20] || host_response[59:44]!=packet_h[79:64])
                        $fatal(1,"unexpected input ACK");
                    @(posedge clk);@(negedge clk);
                end
            end
            $fclose(inputs_file);
        end
    endtask
    initial begin
        if (!$value$plusargs("CASE_DIR=%s",case_dir)) $fatal(1,"CASE_DIR required");
        if ($value$plusargs("VCD=%s",wave_path)) begin $dumpfile(wave_path);$dumpvars(0,tb_nmc_mesh_system);end
        $readmemh({case_dir,"/config.hex"},config_words);
        outputs=config_words[0];stall_until=config_words[1];abort_cycle=config_words[2];
        trace_fd=$fopen({case_dir,"/packets.csv"},"w");
        $fdisplay(trace_fd,"phase,cycle,node,type,data");
        summary_fd=$fopen({case_dir,"/cycles.csv"},"w");
        $fdisplay(summary_fd,"phase,input_cycles,kernel_cycles,acks,dones,aborted");
        for (integer t=0;t<N;t=t+1) begin
            local_m[t]=0;local_k[t]=0;row_base[t]=0;result_dst[t]=0;endpoint_parent[t]=0;
            endpoint_base[t]=0;endpoint_count[t]=0;endpoint_sources[t]=0;
        end
        config_file=$fopen({case_dir,"/nodes.txt"},"r");
        while (!$feof(config_file)) begin
            code=$fscanf(config_file,"%d %d %d %d %d %d %d %d %d %d %h\n",tile,m,k,base,destination,psum,enabled,reducing,forwarding,outputs,mask);
            if (code==11) begin
                local_m[tile]=16'(m);local_k[tile]=16'(k);row_base[tile]=16'(base);
                result_dst[tile]=8'(destination);result_psum[tile]=1'(psum);compute_mask[tile]=m!=0;
                endpoint_enable[tile]=1'(enabled);endpoint_reduce[tile]=1'(reducing);
                endpoint_forward[tile]=1'(forwarding);endpoint_parent[tile]=8'(destination);
                endpoint_base[tile]=16'(base);endpoint_count[tile]=16'(outputs);endpoint_sources[tile]=mask;
            end
        end
        $fclose(config_file);outputs=config_words[0];
        repeat (3) @(negedge clk);rst_n=1;
        $display("MESH setup: reset complete");
        weights_file=$fopen({case_dir,"/weights.txt"},"r");
        while (!$feof(weights_file)) begin
            code=$fscanf(weights_file,"%d %h %h\n",tile,address,data);
            if (code==3) access_memory(1,tile,address,data,15,returned);
        end
        $fclose(weights_file);
        $display("MESH setup: weights loaded");
        for (phase=0;phase<(abort_cycle>=0?2:1);phase=phase+1) begin
            configure_masks();acks=0;dones=0;phase_done_count=0;recording=1;
            $display("MESH setup: masks loaded, phase %0d",phase);
            load_inputs();
            $display("MESH setup: inputs loaded, %0d ACKs",acks);
            @(negedge clk);task_valid=compute_mask;compute_start=cycle;
            if ((task_ready & compute_mask)!==compute_mask) $fatal(1,"Tile not ready for launch");
            @(posedge clk);@(negedge clk);task_valid=0;
            while (!(phase_done_count==1 && (local_done & compute_mask)==compute_mask && idle) && !abort_now) begin
                if (cycle-compute_start<stall_until) eject_enable=~endpoint_enable;
                else eject_enable='1;
                @(negedge clk);
            end
            finish_cycle=cycle;
            if (abort_now) begin
                $fdisplay(summary_fd,"%0d,%0d,%0d,%0d,%0d,1",phase,compute_start-load_start,finish_cycle-compute_start,acks,dones);
                rst_n=0;recording=0;eject_enable='1;
                repeat (3) @(negedge clk);rst_n=1;
                @(negedge clk);
                if (!idle || local_done!=0) $fatal(1,"reset failed to flush transaction");
            end else begin
                if (phase_done_count!=1) $fatal(1,"wrong completion count");
                $fdisplay(summary_fd,"%0d,%0d,%0d,%0d,%0d,0",phase,compute_start-load_start,finish_cycle-compute_start,acks,dones);
                recording=0;
                read_fd=$fopen({case_dir,"/memory.csv"},"w");$fdisplay(read_fd,"index,data");
                for (integer index=0;index<outputs;index=index+1) begin
                    access_memory(0,0,16'h2000+16'(4*index),0,0,returned);
                    $fdisplay(read_fd,"%0d,%08h",index,returned);
                end
                $fclose(read_fd);
            end
        end
        $fclose(trace_fd);$fclose(summary_fd);
        $display("MESH RTL PASS");$finish;
    end
endmodule
