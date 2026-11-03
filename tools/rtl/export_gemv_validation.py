"""Build the Chinese reports and a compact, hash-verifiable RTL evidence bundle."""
from __future__ import annotations
import argparse,csv,difflib,gzip,hashlib,io,json,os,shutil,subprocess,tarfile,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def load(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def table(rows,fields):
    return '| '+' | '.join(fields)+' |\n| '+' | '.join('---' for _ in fields)+' |\n'+'\n'.join('| '+' | '.join(str(r.get(f,'')) for f in fields)+' |' for r in rows)+'\n'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),*args],cwd=ROOT)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input-dir',type=Path,required=True);p.add_argument('--summary-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--prepared-date',default='2026-11-02');a=p.parse_args()
    O=a.input_dir.resolve();D=a.summary_dir.resolve();out=a.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    s=load(D/'summary.json');primary=[r for r in s['rows'] if r['seed']==20261031];pairs=[r for r in s['paired'] if r['seed']==20261031]
    manifest=load(ROOT/'ini/rtl_gemv_20261031.json');versions=load(O/'provenance/tool_versions.json');resources=load(O/'provenance/resources.json');initial=load(O/'provenance/initial.json')
    assert len(primary)==4 and all(r['numeric_protocol_and_resource_audit'] for r in s['rows'])
    legacy=load(O/'mesh_legacy/results.json');tile=load(O/'tile_actual/results.json');root=load(O/'root_user/results.json')
    assert legacy['passed']==legacy['cases']==9 and tile['passed']==47 and root['calibration_cases']==51
    xml=(O/'rtl_pytest.xml').read_text(encoding='utf-8');assert 'failures="0"' in xml and 'skipped="0"' in xml
    assert '5 passed' in (O/'rtl_pytest.log').read_text(encoding='utf-8')
    prod=load(O/'provenance/production_rtl_unchanged.json')
    assert all(sha(ROOT/name)==value for name,value in prod.items())
    old=load(ROOT/'data/results/mesh_rtl_20261028/results.json');old_by_name={r['name']:r for r in old['results']}
    parity=[]
    for r in legacy['results']:
        previous=old_by_name[r['name']]
        parity.append(dict(case=r['name'],local_rtl_phases=r['rtl_phase_cycles'],historical_rtl_phases=previous['rtl_phase_cycles'],same_cycles=r['rtl_phase_cycles']==previous['rtl_phase_cycles']))
    (D/'legacy_reproduction.json').write_text(json.dumps(parity,indent=2),encoding='utf-8')
    basics=[dict(shape=f"{r['m']}x{r['k']}",policy=r['policy'],workers=r['workers'],reducers=r['reducers'],RTL_input=r['rtl_input'],RTL_kernel=r['rtl_kernel'],RTL_total=r['rtl_total'],Py_input=r['python_input'],Py_kernel=r['python_kernel'],Py_total=r['python_total']) for r in primary]
    pairtable=[dict(shape=f"{r['m']}x{r['k']}",S_total=f"{r['S_total']:.6f}",total_reduction=f"{r['total_reduction_percent']:.3f}%",S_kernel=f"{r['S_kernel']:.6f}",kernel_reduction=f"{r['kernel_reduction_percent']:.3f}%",input_saved=r['input_saved'],kernel_extra=r['kernel_cost'],total_saved=r['total_saved']) for r in pairs]
    report=f'''# GEMV 在 8×8 Mesh 上的真实 RTL 仿真与模型对照

报告整理日期：{a.prepared_date}（Asia/Shanghai）。campaign 路径沿用任务指定的 rtl_validation_20261031，目录名不代替实际执行时间。所有 RTL 数字来自本机 Icarus/vvp 的本轮运行文件；GitHub 历史数据只用于独立对照。

## 结论与验收

**GEMV 已实际在 64 个真实 Tile 的 8×8 Mesh RTL 中执行。** 原九个集成用例 9/9 通过；四组核心映射各用两个固定种子运行，共 8/8 通过；另两个 signed INT8 尾部/跨类型/高 ID 边界案例 2/2 通过。此前五个跳过的 RTL pytest 本轮 **5 passed、0 skipped**，新增 Runner 测试 **3 passed**。单 Tile 47 用例/53 段任务和 Root 218 请求/51 组校准另有实际运行结果。

两个形状上，v0.4 的 16-worker 选解在**总计时窗口**仍优于 32-worker 强基线，但 **kernel＋归约＋完成阶段变慢**。收益来自输入加载路径节省超过 kernel 代价，不能宣称 16-worker 计算本身更快或所有 GEMV 都更快。

'''+table(basics,['shape','policy','workers','reducers','RTL_input','RTL_kernel','RTL_total','Py_input','Py_kernel','Py_total'])+'\n'+table(pairtable,['shape','S_total','total_reduction','S_kernel','kernel_reduction','input_saved','kernel_extra','total_saved'])+f'''
S_total=RTL_total_baseline/RTL_total_v04；S_kernel=RTL_kernel_baseline/RTL_kernel_v04。周期减少=(baseline−v04)/baseline；负数代表退化。四组主实验 seed=20261031，附加 seed=20261101 的数值不同、周期相同，只是正确性重复，不能当成独立性能样本或统计显著性证据。

| 验收项 | 本轮证据 |
|---|---|
| 实际 64 Tile RTL | nmc_mesh_system 的 64 组真实 Compute/SRAM/MAC、NI、Reducer 与 64 Router；四核心案例均有逐节点 0–63 的实际统计 |
| 原集成回归 | mesh_legacy/results.json；九例全部通过，与历史逐例周期比较另存 legacy_reproduction.json |
| 四映射、八次种子运行 | core_*/results.json、workload.json、plan.json、simulation.log |
| Root SRAM 与独立 Golden | memory.csv、golden_check.json；root_writes.csv 逐索引只提交一次，完整覆盖全部 M |
| Packet/ACK/Done | 完整 ejection flit trace，经独立解码后与期望包多重集比较；来源/目的/tid/index/lane/last/数据均匹配 |
| 容量与反压 | 实测峰值及 TB 断言：Router≤4、NI≤4、RX≤2；Tile ResultBeat 与所有 Router 输出端口 blocked 时保持 valid/payload |
| 完成条件 | 单全局 Done；Root completed_words、last_seen、逐输出 accepted/expected 来源覆盖均在 Done 时检查 |
| 可复现与报告 | 命令、版本、SHA、补丁、原始数据、四组 PNG/SVG 与本报告/组会总结 |

## 仓库、材料与代码保护

基点完整 SHA：**{initial['base_commit']}**。原 checkout 位于 <local-path-omitted>，分支 {initial['old_branch']}；其改动状态、tracked patch SHA256 和所有未提交文件字节哈希保存于 provenance/initial.json。本轮独立 worktree 为 <local-path-omitted>，分支 sim/rtl-validation-20261031。没有 reset、清理旧数据、自动提交、push、merge 或远端 PR。

已读取实际 Tile MAC/SRAM/Compute、Mesh/Router/NI/Reducer 顶层、TB、Runner、Grid Mapper、搜索模型、Python evaluator、规范、旧协议及逐候选记录。生产 RTL 每个 .sv 均与 HEAD 原文核对，哈希在 production_rtl_unchanged.json；仅改 TB、Runner 和 Windows 入口/路径适配。源码 snapshot 与 changes.patch 可定位本次所有未提交代码。

core 方案从上轮 default-161x97.json 与 default-251x127.json 的预算 1 记录取回，未硬编码预期加速比。selected_history.json 保存来源文件 SHA、完整 Plan SHA 和模型周期；ini/rtl_gemv_20261031.json 固定方案、种子和契约。主种子严格复用旧 W/x 的 SHA256，任何 Plan/workload 不一致会拒绝执行。

## 环境与工具安装

Windows 11，{resources['cpu']}，{resources['logical_cpus']} 逻辑 CPU；环境检查时总内存 {resources['memory_total']/2**30:.2f} GiB，可用 {resources['memory_available']/2**30:.2f} GiB，磁盘空闲 {resources['disk_free']/2**30:.2f} GiB。Python 使用原 checkout 的隔离 .venv（3.12.14）；精确版本输出见 provenance/tool_versions.json。

WSL 未安装/不可用，没有修改系统功能或安装发行版。采用 [YosysHQ 官方 Windows OSS CAD Suite 2025-10-31](https://github.com/YosysHQ/oss-cad-suite-build/releases/tag/2025-10-31)，下载文件 SHA256 **f00c5b44774fa91633b24c766891b6c557e1cc9a65c48779463a6781dc5d5f17**，与官方资产列表一致。没有用不可下载的当前日期版本冒充已安装；原版本查询 403 和当前日期资产 404 均未当作成功。

实际工具：Icarus/vvp 13.0 devel（s20251012-33-ga7502173d），Verilator 5.041 devel（v5.040-201-g21dbdbf69），Yosys 0.58+94（4011d7265）。Icarus/vvp 真正执行仿真，Verilator 执行生产模块 lint，Yosys 执行单 Tile SRAM 结构检查；不声称做了两种仿真器交叉运行或完整 64 Tile 芯片综合。

Windows 适配层调用真实二进制，逐字转发输出及原 exit code：Icarus 用 -B 指向便携 lib/ivl，vvp 用 -M 指向 VPI，Verilator 转 native verilator_bin.exe。C# 小启动器由本机 .NET Framework csc 编译，不制造通过输出。run_tile_rtl 的 Yosys 输出路径改为正斜杠，避免命令字符串转义；没有改数据断言或 SRAM 几何。

初始沙箱运行 Yosys/vvp 因 Windows 路径解析 API 失败，表现为 GetShortPathName failed/Missing first \\ in exe path；同一已校验工具以用户权限运行后通过。旧失败日志保留，不能把环境失败当硬件错误。工具解压与启动脚本仅改进程环境；无管理员级 WSL/系统修改。

## 实际硬件与数据流

每个物理 Tile 具有四 Lane signed INT8 MAC、INT32 回绕累加、4 KiB W、1 KiB X、1 KiB Y；Mesh 固定 8×8，XY 路由、每输入四 Flit FIFO、包级锁与轮询；NI 四 Flit 原子接收一/两 Flit 包。每节点归约端点有两 RX 包槽、一活动包、256 INT32 部分和及 64-bit 来源表、输出保持寄存器；端点元数据在 6 KiB Compute SRAM 预算之外，不隐藏资源。

W 与配置/来源 mask 经 sideband 预加载；本轮计时不包含此阶段。X 通过真实 NoC MEM_WRITE 写入一字节 SRAM 端口，逐包 ACK 后统一 task_valid 启动真实 MAC。Leaf 发 PSUM/FINAL，经 NI、Router、树节点归约，最终 Root0 写回 Y SRAM，产生一个全局 Done；还等待 leaf LocalDone、所有端点/网络 idle。最后通过实际 SRAM 读接口读取 M 个结果。

ROW 按 M 划分，每个 leaf 完整计算其输出行；K 按输入轴拆分，需要部分和；二维同时划分 M/K。逻辑 4×8/4×4 不等于物理 Mesh 尺寸。compact 按到 Root0 的 Manhattan 距离与 Tile ID 选位置。强基线为 32 leaf、12 中间归约 Tile；v0.4 为 16 leaf、4 中间归约 Tile；两者仍有同样的 64 个物理 Tile 和端点能力，余下节点空闲，未删减硬件。Root/Host=0、tid=1、单事务、M≤256 的限制保留。

## 数值与通信验证

独立 Golden 使用 Python 宽整数逐乘加，再显式 ((sum+2**31)%2**32)−2**31，未调用 DUT 的结果或回绕实现。每个输入值验证在 [-128,127]；边界用例包含 -128、127、0、-1、非四 Lane 整除、ROW 与 K、spread 高 Tile ID。两核心形状本身含尾 Lane；附加种子不同但配对策略使用相同 W/x。没有缩减矩阵、裁剪输出或以子块冒充整层。

全部实际 ejection flit 被解码为包，按完整字段作 Counter 比较，不依赖固定包到达顺序：核对 MEM_WRITE/ACK 身份、RESULT/PSUM 的源/目的/索引/有效 Lane/last/数据、全局 Done 的数量、tid、输出数与来源 mask。多余/缺少包均失败。Root SRAM 读回索引必须恰为 0..M−1，逐元素与 Golden 相同；实际 Root 写日志也逐索引完整且不重复，最后写回先于全局 Done。

逐节点观测为真正 TB 采样：FIFO/NI/RX 峰值，MAC group steps、Router output blocked port-cycles、有向链路 Flit 握手数、NI 输出停顿、端点接包与非 IDLE 周期、Root 写握手。所有 Router≤4、NI≤4、RX≤2；本轮四主案例峰值分别为 4/2/2。逐路径 RTL link-flit 总数与 Python 同一 XY 路径的 flit-hop 一致，作为流量守恒核对。

## 周期定义、模型差异与结果

RTL input 从首次输入加载前标记到 task launch，kernel 从 task launch 到唯一 Done、所有 LocalDone 与 idle 都满足。只报告两个实测窗口，total=input+kernel。kernel 包含归约和完成排空，当前不伪造独立 completion_drain。Python 单独保存 input/compute_and_gather/completion_drain；对照时后两项合并为 kernel。

Python evaluator 的输入功能提交、RTL 的窄 X 写端口、MEM 状态机、注册 NI 及 TB 握手协议不同。以下 delta=RTL−Python，相对值以 Python 为分母；输入差值是不同服务契约的对照，不应解释为同一个周期精确实现的误差。

'''
    report+=table([dict(shape=f"{r['m']}x{r['k']}",policy=r['policy'],input_delta=r['input_delta'],kernel_delta=r['kernel_delta'],kernel_rel=f"{100*r['kernel_relative_vs_python']:.3f}%",total_delta=r['total_delta'],total_rel=f"{100*r['total_relative_vs_python']:.3f}%") for r in primary],['shape','policy','input_delta','kernel_delta','kernel_rel','total_delta','total_rel'])
    report+='''
两个配对的输入额外开销分别都是 901 与 1153 周期，实测等于 9×输入请求数+1。这是本 Runner/默认协议的观测关系，包含真实 SRAM 服务、MEM/ACK/NI 和驱动握手差异，不能把所有九周期都归因于单独 SRAM 硬件或推广到任意驱动。kernel 差异仅 +5/+6 周期，符合任务接受/VALIDATE、注册队列与完成流水影响的范围；不据局部对齐声称全系统周期等价。

## 性能原因与负结果

两个策略在每个形状的输入请求数分别同为 100 和 128，ACK 相同，有效 X 数据均复制四个 ROW 分区。因此本轮收益**不是输入复制字节数减少**。16-worker compact 的目的 Tile 更靠近 Root；同一 stop-and-wait 驱动下，输入节省 292/376 周期，与 Python 两方案的输入差完全相同。

代价是每个 leaf 的 K_local 从约 13/16 增到约 25/32，而 M 的四分区不变，MAC 链更长。真实 kernel 增加 56/214 周期，即 18.79%/52.07% 的退化。总窗口分别仍节省 292−56=236、376−214=162 周期。

'''
    report+=table([dict(shape=f"{r['m']}x{r['k']}",policy=r['policy'],writes=r['input_requests'],ACK=r['acks'],PSUM=r['result_psum'],FINAL=r['result_final'],Done=r['done'],link_flits=r['rtl_link_flits'],router_blocked=r['router_blocked_port_cycles'],NI_stall=r['ni_stall_tile_cycles'],endpoint_nonidle=r['endpoint_service_tile_cycles'],root_nonidle=r['root_service_cycles']) for r in primary],['shape','policy','writes','ACK','PSUM','FINAL','Done','link_flits','router_blocked','NI_stall','endpoint_nonidle','root_nonidle'])
    report+='''
v0.4 的归约层级更浅，RESULT/PSUM 包数和链路传输显著降低，Router blocked port-cycles 也更少；但这不等于 kernel 更快，因为减少计算并行度的代价超过这些阶段中的通信节省。NI 输出 stall 与 Root SRAM write stall 在四主案例均为零，不将它们臆测为当前关键瓶颈。最终 Root 的 FINAL 包量和非 IDLE 周期对每个配对相同；主要权衡在输入目的地距离、Leaf MAC 时长与中间归约树。

端点非 IDLE 计数定义为 state!=IDLE 的边沿采样，不含 IDLE 状态上发生的 DISPATCH。Router 端口周期、Tile 周期和服务忙周期彼此并行/重叠，不能与 input/kernel 柱相加，也不能只用相关性证明因果。本轮结论基于同输入、同硬件的受控两方案配对和明确阶段差；没有在 RTL 中扫遍整个候选目录，不能声称 RTL 全局最优。

## 回归、失败记录与未执行增量

原九例由未修改的 frozen worktree 实际运行（包含 backpressure 波形），通过手算、尾部 tree、ROW32、二维16/32、K32 central/tree、长期反压和在途 reset replay。五项 RTL pytest 包含单 Tile、Root 写回、独立归约逐服务边沿（31/63 号来源、人工 PSUM 回绕、五类非法包）、集成 Mesh、Mapper→Tile bridge，未强制跳过。原 9/9 与 pytest 内再次执行的九例属于重复验证，不冒充新的独立性能形状。8 核心执行与 2 边界执行也分别列出，不混计为某一个 pytest 数量。

环境失败保留：缺工具、Perl 入口、Icarus 内置 /yosyshq 路径、CMD 正反斜杠、Windows sandbox 的路径解析限制；真实执行成功后未删原日志。适配脚本曾在并发复制一个已使用的 exe 时失败，后改为按源码哈希缓存，实际通过的运行使用稳定适配器。

报告整理阶段又拟增加显式 NI flit 和 Root SRAM write 的 held 检查；其两次执行请求均被自动审批系统因“审批未在期限内完成”拒绝，**没有启动**。该增量单独保存在 unexecuted_optional，最终生产交付代码恢复为哈希匹配、实际通过全部运行的 instrumented_v1。不能把这两条额外断言计为已通过；已执行的 payload 保持断言覆盖 Tile ResultBeat 和所有 Router 输出端口，Root 写握手另由已有写回回归验证。自动审批超时不表示硬件有错误。

pytest 临时目录的 ACL 在当前沙箱中不可读取，文件在本机保留；交付其 stdout 和 JUnit，核心十例、原九例、独立 Tile/Root 运行的可访问原始 trace 均打包，不把不可访问的 pytest 内部临时 trace 声称已复制。

## 限制与课程报告用法

可以将上述数值、协议、周期与配对总延迟收益写入课程报告，但必须称为本项目默认硬件下的真实 RTL 仿真，注明权重/配置预加载不计时、四个映射点、Root/Host0、tid1、单事务、全部 64 物理 Tile。16/32 是活跃 leaf 数量。不同种子证明数据正确性，不证明统计意义上的性能显著性。

未做全芯片 PPA、工艺 SRAM/频率校准、多事务、任意 Root、全部 Bank/端点参数，也未在 RTL 中重新运行映射搜索。报告 cycles，不换算真实纳秒/TOPS/W；TB 的 10ns 时钟只是仿真驱动。单 Tile SRAM 的结构结果不代表完整网络面积、频率或功耗。

## 图表、文件与复现

四组图均从 comparison.csv/summary.json 的实际数据生成 PNG 与 SVG：01 配对总延迟和 speedup；02 实测 input/kernel 分解；03 Python 与 RTL 分阶段对照；04 RTL 链路 Flit、包数和 Router backpressure。没有把 Python 诊断混画为 RTL 测量，没有用预期数值填图。

主要新增/修改文件：tools/rtl/run_mesh_rtl.py、src/rtl/nmc_mesh/tb_nmc_mesh_system.sv、tools/rtl/run_tile_rtl.py（路径）、windows_oss_environment.ps1、windows_tool_launcher.cs、summarize_gemv_rtl.py、export_gemv_validation.py、ini/rtl_gemv_20261031.json、src/tests/test_rtl_gemv_runner.py。完整代码/补丁、原始日志、配置、GridPlan、Golden、Root 读写、包 trace、统计、版本、生产 RTL 未改证明及哈希清单均在交付包。

每个数字通过 name/run folder→Plan SHA/workload SHA→真实 cycles/packets/observations→CSV/summary→图表追溯。复现命令见 Reproduce.zh-CN.md，源代码 snapshot 保留实际输入文件；上轮 Python 结果和旧 ZIP 没有改写。
'''
    meeting=f'''# 组会总结：GEMV 在 8×8 Mesh 上的真实 RTL 验证

整理：{a.prepared_date}；campaign 名称沿用 20261031。

**完成内容：** 已补齐 Windows 工具链并真正运行 64 Tile RTL。原九个集成用例 9/9，通过五项原先跳过的 RTL pytest（5 passed、0 skipped）；四核心映射×两种子=8/8，另两个 INT8 边界/尾 Lane/高 ID 的 ROW/K 案例 2/2。Root SRAM 逐元素等于独立宽整数 Golden，包字段、ACK、唯一 Done、来源完成和资源上界检查均通过。

**真实总周期：**

'''+table([dict(shape=r['shape'],baseline=next(v['RTL_total'] for v in basics if v['shape']==r['shape'] and v['policy']=='unindp-compact'),v04=next(v['RTL_total'] for v in basics if v['shape']==r['shape'] and v['policy']=='noc-v04'),speedup=r['S_total'],reduction=r['total_reduction']) for r in pairtable],['shape','baseline','v04','speedup','reduction'])+'''
**为什么更快：** 同为 64 物理 Tile，32-worker compact/tree 与 16-worker compact/tree 使用相同 W/x。两方案每形状输入请求数相同；16-worker 目的地更近，输入少 292/376 周期。代价是 K_local 更长，kernel 多 56/214 周期（慢 18.79%/52.07%），总计仍少 236/162 周期。通信包和链路占用也减少，但不能因此声称 kernel 更快。

**模型对照：** RTL 使用真实一字节 X SRAM 写口和注册 NI；Python 功能输入提交不同。输入各配对共同多 901/1153 周期，kernel 只差 5–6 周期。模型收益方向成立，总加速幅度被共同加载开销稀释。

**可写课程报告：** 可以，限定为默认硬件、Root0/tid1/单事务、预加载不计时的四映射点 RTL 仿真；不是 FPGA/芯片实测，不是 PPA 或任意工作负载最优结论。无自动提交/推送。

**限制：** 两条后加的 NI/Root 额外保持断言因自动审批超时未运行，未计为通过；交付采用已经完整运行的版本。细节、全部原始数据、四组图和复现步骤见完整报告及交付包。
'''
    reproduce=r'''# RTL 验证复现（PowerShell / Linux）

先在独立 checkout 检出 f128f237a5393dbc1fadb8e47278e10d82d81f86，并应用 changes.patch。不要对原工作区 reset、清理或覆盖原结果。源码快照可定位补丁与全部依赖；不需要 commit 或 push。

## Windows 用户权限

本轮实际使用官方固定版本，已核对下载 SHA：
```powershell
$tools = Join-Path $PWD 'work/rtl-tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
curl.exe -L --fail 'https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2025-10-31/oss-cad-suite-windows-x64-20251031.exe' -o "$tools/suite.exe"
$actualHash=(Get-FileHash "$tools/suite.exe" -Algorithm SHA256).Hash.ToLower()
if ($actualHash -ne 'f00c5b44774fa91633b24c766891b6c557e1cc9a65c48779463a6781dc5d5f17') { throw 'tool archive mismatch' }
$extract=Join-Path $tools 'extracted'
Start-Process -FilePath "$tools/suite.exe" -ArgumentList @('-y',"-o$extract") -WindowStyle Hidden -Wait
. tools/rtl/windows_oss_environment.ps1 -SuiteRoot "$extract/oss-cad-suite"
# 使用已有 Python 3.12 虚拟环境，或新建 .venv 并安装 requirements.txt。
# 本机实际复用的解释器如下；干净机器可改用自己的 Python 3.12 .venv。
$py=(Resolve-Path .venv/Scripts/python.exe).Path
# 新环境可先执行：python -m venv .venv
# 然后：.venv\Scripts\python.exe -m pip install -r requirements.txt
iverilog -V
verilator --version
yosys -V
& $py --version
```
在普通用户权限运行。若受限沙箱报告 GetShortPathName/Missing first slash，则需要允许本地仿真工具访问正常用户路径；不需要修改生产 RTL，不应靠强制跳过 pytest。

Windows helper 为便携真实二进制设置路径，不实现硬件；需要 Windows .NET Framework csc。只改当前进程 PATH，按源码哈希缓存三个小入口，不复制虚拟环境或修改系统注册表。

## 原回归（实际已执行）
```powershell
& $py tools/rtl/run_tile_rtl.py --output-dir dump/my_rtl_run/tile
& $py tools/rtl/run_root_writeback_rtl.py --output-dir dump/my_rtl_run/root
& $py tools/rtl/run_mesh_rtl.py --output-dir dump/my_rtl_run/mesh --wave
& $py -m pytest -q -p no:cacheprovider -ra src/tests/test_tile_rtl.py src/tests/test_root_writeback_rtl.py src/tests/test_reduce_rtl.py src/tests/test_mesh_rtl.py src/tests/test_grid_mapper_rtl.py
& $py -m pytest -q -p no:cacheprovider -ra src/tests/test_rtl_gemv_runner.py
```
本轮单 Tile 47 用例/53 phases，Root 218 requests/51 calibration，集成 9/9，RTL pytest 5 passed/0 skipped，新增 Runner pytest 3 passed。

## 四核心映射、附加种子与边界
```powershell
& $py tools/rtl/run_mesh_rtl.py --case-manifest ini/rtl_gemv_20261031.json --simulation-timeout 1200 --output-dir dump/my_rtl_run/new
```
默认 nine cases 不因增加 manifest 改变；显式 --case-manifest 才选择新十个执行。--simulation-timeout 仅为 vvp 进程的墙钟期限，不改变 RTL 内部周期限制或模型时序。

本轮为并行独立案例分成如下五个目录，命令的完整 argv 在 logs/commands.json。可按本轮目录重跑，以直接调用汇总器：
```powershell
foreach ($m in @(161,251)) {
  foreach ($policy in @('unindp-compact','noc-v04')) {
    $k=if($m -eq 161){97}else{127}
    & $py tools/rtl/run_mesh_rtl.py --case-manifest ini/rtl_gemv_20261031.json --cases "gemv_${m}x${k}_${policy}_s20261031" "gemv_${m}x${k}_${policy}_s20261101" --simulation-timeout 1200 --output-dir "dump/my_rtl_run/core_${m}_${policy}"
  }
}
& $py tools/rtl/run_mesh_rtl.py --case-manifest ini/rtl_gemv_20261031.json --cases signed_tail_k_high_id signed_tail_row_high_id --output-dir dump/my_rtl_run/boundary
& $py tools/rtl/summarize_gemv_rtl.py --input-dir dump/my_rtl_run --output-dir data/results/my_rtl_summary
```
输出包含 case/workload/Golden/Plan、真实 Flit trace、Root SRAM 读写、cycles.csv、observations.csv、独立包比较摘要、Python 同配置对照和源代码 SHA。汇总器会重新审计保存数据，拒绝重复/缺少案例、来源漂移、错误结果、包不匹配、重复输出及超容量，不从预期收益造图。

Linux 上无需 Windows 适配器；安装 Icarus/vvp、Verilator、Yosys 和 Python 依赖后，将上述 `$py` 换为实际 python。PowerShell 变量语法不能原样复制到 Bash。不要把真实 RTL input/kernel 与 Python 内部不同服务的阶段强行要求总周期相等。

完整原回归还在未修改 frozen worktree 上先执行一次，原始结果在 raw/mesh_legacy。本轮有观察接口的版本又由 pytest 实际跑过同九例。二者都属于本轮运行，不拿仓库旧 data/results/mesh_rtl_20261028 替代。
'''
    for name,text in [('RTL_GEMV_8x8_Validation_Report.zh-CN.md',report),('Meeting_Summary.zh-CN.md',meeting),('Reproduce.zh-CN.md',reproduce)]:
        (out/name).write_text(text,encoding='utf-8')
    shutil.copytree(D,out/'data',dirs_exist_ok=True)
    # Preserve actual, nonduplicate runs and startup failures, not tool/runtime caches.
    allowed={'.json','.csv','.txt','.hex','.log','.xml','.sv','.patch','.md','.sha256'}
    skipped=[]
    for folder in ['core_161_unindp-compact','core_161_noc-v04','core_251_unindp-compact','core_251_noc-v04','boundary','mesh_legacy','tile_actual','root_user','instrumentation_probe','provenance','unexecuted_optional']:
        source=O/folder
        for path in source.rglob('*'):
            if not path.is_file() or path.suffix not in allowed:continue
            dest=out/'raw'/path.relative_to(O);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    for path in O.iterdir():
        if path.is_file() and path.suffix in allowed:
            target=out/'logs'/path.name;target.parent.mkdir(exist_ok=True);shutil.copy2(path,target)
    wave=O/'mesh_legacy/backpressure/wave.vcd'
    if wave.exists():
        target=out/'waveforms/backpressure.vcd.gz';target.parent.mkdir(exist_ok=True)
        with wave.open('rb') as source,gzip.open(target,'wb') as destination:shutil.copyfileobj(source,destination)
    changed=git('diff','--name-only').decode().splitlines();new=git('ls-files','--others','--exclude-standard').decode().splitlines()
    code=[p for p in new if not p.startswith('data/results/')]
    patch=git('diff','--binary')
    for name in sorted(code):
        lines=(ROOT/name).read_text(encoding='utf-8').splitlines(keepends=True)
        if lines and not lines[-1].endswith('\n'):lines[-1]+='\n'
        patch+=(f'diff --git a/{name} b/{name}\nnew file mode 100644\n'+''.join(difflib.unified_diff([],lines,fromfile='/dev/null',tofile='b/'+name))).encode()
    (out/'changes.patch').write_bytes(patch)
    for name in changed+code:
        destination=out/'repository_files'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,destination)
    (out/'changed_files.json').write_text(json.dumps(changed+new,indent=2),encoding='utf-8')
    snapshot=set(changed+code+['requirements.txt','pyproject.toml','src/hardware.py'])
    for base in ['src/compiler','src/simulator','src/rtl/nmc_tile','src/rtl/nmc_mesh','src/tests','tools/rtl']:
        snapshot.update(p.relative_to(ROOT).as_posix() for p in (ROOT/base).rglob('*') if p.suffix in ('.py','.sv','.cs','.ps1'))
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name in sorted(snapshot):
            content=(ROOT/name).read_bytes();info=tarfile.TarInfo(name);info.size=len(content);info.mtime=0;archive.addfile(info,io.BytesIO(content))
    (out/'source_snapshot.tar.gz').write_bytes(gzip.compress(buffer.getvalue(),mtime=0))
    (out/'provenance.json').write_text(json.dumps(dict(base_commit=initial['base_commit'],patch_sha256=sha(out/'changes.patch'),source_snapshot_sha256=sha(out/'source_snapshot.tar.gz'),production_rtl_unchanged=prod,prepared_date=a.prepared_date,manifest_sha256=s['manifest_sha256']),indent=2),encoding='utf-8')
    files={p.relative_to(out).as_posix():sha(p) for p in out.rglob('*') if p.is_file() and p.name not in ('manifest_sha256.json','RTL_GEMV_8x8_Validation_20261031.zip')}
    (out/'manifest_sha256.json').write_text(json.dumps(files,indent=2),encoding='utf-8')
    archive=out/'RTL_GEMV_8x8_Validation_20261031.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in sorted(files):z.write(out/name,name)
        z.write(out/'manifest_sha256.json','manifest_sha256.json')
    print('reports and bundle',len(files),'files',archive.stat().st_size,'bytes')
if __name__=='__main__':main()
