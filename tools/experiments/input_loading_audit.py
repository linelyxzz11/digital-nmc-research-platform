#!/usr/bin/env python3
r'''Measure input-loading RTL stages and evaluate Host windows and X write width.

The diagnostic generates temporary testbench/SRAM variants under --output-dir.
It stops before MAC launch and validates all input bytes through SRAM readback.
'''
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import runpy
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    ROOT = Path(__file__).resolve().parents[2]
    OUT = args.output_dir.resolve()
    if (OUT / 'results.json').exists():
        parser.error('output already contains results; choose a new directory')
    OUT.mkdir(parents=True, exist_ok=True)
    runner = runpy.run_path(str(ROOT / 'tools/rtl/run_mesh_rtl.py'))
    records = json.loads(gzip.decompress((ROOT / 'data/results/rtl_gemv_20261031/evidence.json.gz').read_bytes()))
    cases = [r for r in records if r['case'].get('workload_seed') == 20261031]
    tb = (ROOT / 'src/rtl/nmc_mesh/tb_nmc_mesh_system.sv').read_text()
    tb = tb.replace('integer acks=0,', 'integer audit_fd,input_window=1,sent=0,audit_cycles=0;\n    integer acks=0,')
    tb = tb.replace('trace_fd=$fopen', r'''audit_fd=$fopen({case_dir,"/load_events.csv"},"w");
        $fdisplay(audit_fd,"edge,event,node,seq,address");
        if ($value$plusargs("WINDOW=%d",input_window)) begin end
        trace_fd=$fopen''', 1)
    a = tb.index('    task automatic load_inputs;')
    b = tb.index('    initial begin', a)
    original = tb[a:b].replace('task automatic load_inputs;', 'task automatic load_inputs_serial;')
    window = r'''    task automatic load_inputs;
        begin
            if (input_window==1) load_inputs_serial();
            else begin
                inputs_file=$fopen({case_dir,"/inputs.txt"},"r");load_start=cycle;sent=0;
                while (!$feof(inputs_file)) begin
                    code=$fscanf(inputs_file,"%h %h\n",packet_h,packet_p);
                    if(code==2) begin
                        while(sent-acks>=input_window) @(negedge clk);
                        @(negedge clk);host_packet_valid=1;host_header=packet_h;host_payload=packet_p;
                        #1;while(!host_packet_ready) @(negedge clk);
                        @(posedge clk);@(negedge clk);host_packet_valid=0;sent=sent+1;
                    end
                end
                while(acks<sent) @(negedge clk);
                $fclose(inputs_file);
            end
        end
    endtask
'''
    tb = tb[:a] + original + window + tb[b:]
    marker = '            $display("MESH setup: inputs loaded, %0d ACKs",acks);'
    verify = r'''
            @(negedge clk);audit_cycles=cycle-load_start;
            recording=0;
            inputs_file=$fopen({case_dir,"/inputs.txt"},"r");sent=0;
            while(!$feof(inputs_file)) begin
                code=$fscanf(inputs_file,"%h %h\n",packet_h,packet_p);
                if(code==2) begin
                    access_memory(0,int'(packet_h[19:12]),packet_h[59:44],0,0,returned);
                    for(integer j=0;j<4;j=j+1)
                        if(packet_h[60+j] && returned[8*j+:8]!==packet_p[8*j+:8]) $fatal(1,"input SRAM mismatch");
                    sent=sent+1;
                end
            end
            if(sent!=acks) $fatal(1,"ACK count mismatch");
            $display("INPUT_AUDIT_PASS cycles=%0d packets=%0d window=%0d",audit_cycles,acks,input_window);
            $fclose(audit_fd);$finish;
'''
    assert marker in tb
    tb = tb.replace(marker, marker + verify)
    mon = r'''
    always @(posedge clk) if(rst_n && recording && !kernel_recording) begin
        if(host_packet_valid && host_packet_ready)
            $fdisplay(audit_fd,"%0d,send,%0d,%0d,%0d",$time/10,host_header[19:12],host_header[79:64],host_header[59:44]);
        if(host_response_valid && host_response_ready && host_response[3:0]==3)
            $fdisplay(audit_fd,"%0d,ack,%0d,%0d,0",$time/10,host_response[11:4],host_response[59:44]);
    end
    for(genvar a=0;a<N;a=a+1) begin: audit
        always @(posedge clk) if(rst_n && recording && !kernel_recording) begin
            if(dut.nodes[a].receive_fire && dut.nodes[a].head_pending && dut.nodes[a].receive_type==2 && dut.nodes[a].head[3:0]==2)
                $fdisplay(audit_fd,"%0d,tail,%0d,%0d,%0d",$time/10,a,dut.nodes[a].head[79:64],dut.nodes[a].head[59:44]);
            if(dut.nodes[a].req_valid && dut.mem_req_ready[a])
                $fdisplay(audit_fd,"%0d,req,%0d,%0d,%0d",$time/10,a,dut.nodes[a].mem_header[79:64],dut.nodes[a].req_addr);
            if(dut.nodes[a].compute.sram.x_port_write)
                $fdisplay(audit_fd,"%0d,write_slot,%0d,%0d,%0d",$time/10,a,dut.nodes[a].mem_header[79:64],dut.nodes[a].compute.sram.x_port_addr);
            if(dut.mem_rsp_valid[a] && dut.nodes[a].rsp_ready)
                $fdisplay(audit_fd,"%0d,rsp,%0d,%0d,0",$time/10,a,dut.nodes[a].mem_header[79:64]);
            if(dut.nodes[a].packet_valid && dut.nodes[a].packet_ready && dut.nodes[a].choice==2)
                $fdisplay(audit_fd,"%0d,ack_enqueue,%0d,%0d,0",$time/10,a,dut.nodes[a].mem_header[79:64]);
        end
    end
'''
    tb = tb.replace('endmodule', mon + '\nendmodule')
    (OUT / 'tb_input_audit.sv').write_text(tb)
    sram = ROOT / 'src/rtl/nmc_tile/nmc_tile_sram.sv'
    wide = sram.read_text().replace('if (host_strb[host_byte]) input_mem[x_port_addr] <= host_data[8*host_byte +: 8];', "for(integer wb=0;wb<4;wb=wb+1)\n                        if(host_strb[wb]) input_mem[{host_addr[9:2],2'b00}+10'(wb)] <= host_data[8*wb+:8];")
    wide = wide.replace('if (host_byte == 3) begin', 'if (host_byte == 3 || host_write) begin')
    assert wide != sram.read_text()
    (OUT / 'wide_input_sram.sv').write_text(wide)
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in [sram, runner['TB'], *runner['SOURCES']]}
    results = []
    for wide_port in [False, True]:
        exe = OUT / ('wide.vvp' if wide_port else 'narrow.vvp')
        sources = [OUT / 'wide_input_sram.sv' if p == sram and wide_port else p for p in runner['SOURCES']]
        cmd = ['iverilog', '-g2012', '-s', 'tb_nmc_mesh_system', '-o', str(exe), *map(str, sources), str(OUT / 'tb_input_audit.sv')]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        (OUT / ('compile_wide.log' if wide_port else 'compile_narrow.log')).write_text(cp.stdout + cp.stderr)
        if cp.returncode:
            raise RuntimeError(cp.stderr[-3000:])
        for rec in cases:
            for window in [1, 8] if wide_port else [1, 4, 8]:
                c = rec['case']
                folder = OUT / (c['name'] + f'_w{(4 if wide_port else 1)}_q{window}')
                folder.mkdir(exist_ok=True)
                plan, W, x, inputs = runner['prepare'](c, folder)
                (folder / 'weights.txt').write_text('')
                started = time.perf_counter()
                cp = subprocess.run(['vvp', str(exe), f'+CASE_DIR={folder}', f'+WINDOW={window}'], capture_output=True, text=True, timeout=300)
                (folder / 'simulation.log').write_text(cp.stdout + cp.stderr)
                if cp.returncode or 'INPUT_AUDIT_PASS' not in cp.stdout:
                    raise RuntimeError(cp.stdout[-3000:] + cp.stderr)
                cycles = int(re.search('INPUT_AUDIT_PASS cycles=(\\d+)', cp.stdout).group(1))
                if not wide_port and window == 1:
                    assert cycles == rec['result']['rtl_phase_cycles'][0]['input_cycles'], (cycles, rec['case'])
                events = list(csv.DictReader((folder / 'load_events.csv').open()))
                counts = {kind: sum((e['event'] == kind for e in events)) for kind in ['send', 'tail', 'req', 'rsp', 'ack_enqueue', 'ack']}
                assert all((v == len(inputs) for v in counts.values())), counts
                expected = {(p.dst_tile, p.sequence) for p in inputs}
                for kind in counts:
                    assert {(int(e['node']), int(e['seq'])) for e in events if e['event'] == kind} == expected
                r = dict(name=c['name'], m=c['m'], k=c['k'], policy=c['policy'], write_bytes_per_cycle=4 if wide_port else 1, window=window, input_cycles=cycles, bytes=sum((s.k_end - s.k_start for s in plan.slices)), packets=len(inputs), wall_seconds=time.perf_counter() - started, readback_pass=True, path=str(folder.relative_to(OUT)))
                if window == 1:
                    groups = {key: {} for key in expected}
                    for e in events:
                        groups[int(e['node']), int(e['seq'])].setdefault(e['event'], int(e['edge']))
                    pairs = [('forward', 'send', 'tail'), ('dispatch', 'tail', 'req'), ('sram_service', 'req', 'rsp'), ('ack_build', 'rsp', 'ack_enqueue'), ('ack_return', 'ack_enqueue', 'ack')]
                    r['stage_cycles'] = {label: sum((g[b] - g[a] for g in groups.values())) for label, a, b in pairs}
                    r['unallocated_host_boundary_cycles'] = cycles - sum(r['stage_cycles'].values())
                results.append(r)
                (OUT / 'results.json').write_text(json.dumps(results, indent=2))
                print(json.dumps(r), flush=True)
    assert all((hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in hashes.items()))
    (OUT / 'source_hashes.json').write_text(json.dumps(hashes, indent=2))
    print('ALL INPUT AUDITS PASS; repository RTL unchanged', flush=True)

    flat_rows = []
    for result in results:
        baseline = next(row for row in results if row['name'] == result['name']
                        and row['window'] == 1 and row['write_bytes_per_cycle'] == 1)
        row = {key: value for key, value in result.items() if key != 'stage_cycles'}
        row['reduction_pct'] = 100 * (1 - result['input_cycles'] / baseline['input_cycles'])
        row['effective_bytes_per_cycle'] = result['bytes'] / result['input_cycles']
        flat_rows.append(row)
    with (OUT / 'summary.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(
            key for row in flat_rows for key in row)))
        writer.writeheader()
        writer.writerows(flat_rows)
    versions = {}
    for tool in ('iverilog', 'vvp'):
        version = subprocess.run([tool, '-V'], capture_output=True, text=True, check=True)
        versions[tool] = (version.stdout + version.stderr).splitlines()[0]
    provenance = {
        'base_commit': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_sha256': hashes,
        'generated_rtl_sha256': {
            name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
            for name in ('tb_input_audit.sv', 'wide_input_sram.sv')},
        'tools': versions,
        'runs': len(results),
        'simulation_wall_seconds': sum(row['wall_seconds'] for row in results),
        'scope': 'Input-loading-only RTL diagnostic; completed SRAM readback and event checks.'}
    (OUT / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')


if __name__ == '__main__':
    main()
