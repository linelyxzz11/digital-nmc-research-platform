# Centralized 与 NMC 的第一轮受控对照

状态：Python 架构实验；新增流式端点未经 RTL 校准。关联 Issue #18。

## 问题与比较范围

在同一 8×8 Mesh、同一组 32 个权重存储 Tile、同一份 INT8 GEMV 上，
把 32 组四 Lane MAC 从各存储节点搬到一个集中端点，观察权重远程供给对总时间的影响。
这是固定 ROW 分块、单个集中网络端口的受控实验，不是所有集中加速器的最优上界，也不是 AiM/GPU 比较。

复用既有 `Mesh2x2` 的可配置 8×8 XY Router、包锁、轮询仲裁与有限 FIFO，以及
`PortScheduledCompute` 的请求级 SRAM/MAC/结果时序。新模型入口为
[central_comparison.py](../../src/simulator/system/central_comparison.py)。
旧系统 runner、生产 RTL 和既有 Packet 格式不变；本入口使用独立 `StreamCodec`。

## 固定预算

- 64 个 Router，4 Flit 输入 FIFO、4 Flit NI；Flit 数据宽度 128 bit。
- 相同 32 个存储节点（排除 Host 0 和中心候选 36），按距 Host 的曼哈顿距离确定，并显式传给 mapper。
- 每存储节点 4 KiB W SRAM，4 B/周期读取，一周期同步读延迟。
- 共 128 MAC，分成 32 组、每组 4 Lane。集中版也可让不同组独立前进，没有串行化整个 MAC 阵列。
- 每计算组保留 1 KiB X、1 KiB Y、两个操作数槽位、4 个 INT32 累加器。
  集中版将这些 X/Y 存储和 MAC 迁至集中端点；权重存储留在原处。
- X 端口每组 1 B/周期；每组 Y 使用原有四 Bank 写回。Host 另有相同 1 KiB 输出区和 16 B/周期写口。
- 两边均预留每组 8×16 B 权重预取空间、16 B 数据包组装与 4 B 在途读数据空间。
  NMC 当前直接读 W，未启用这些预取资源。
- 输入 RX 每组两包；Host 结果 RX 两包；集中结果 TX 最多 32 包；信用返回队列最多 32×8 条。
  相同总容量预算在两种架构下均预留，队列和元数据不计入原先 4/1/1 KiB SRAM。
- 上述是容量和端口预算，未做等面积、等 Fmax、功耗或布局布线证明。

## 数据流与控制

输入 x 在 Host 初始就绪，按 16 B 分块给每组计算簇各发送一份。
NMC 的目的地是 Worker，Central 的目的地是集中节点；两边都经有限 NI/Router，
收到数据后经真实模型的 1 B/周期 X 写口提交。集中版没有逐次 MAC 重新访问远端输入。

所有输入缓冲就绪后同时启动计算。NMC 直接使用既有本地端口模型；
Central 的 W 端口替换为有界远程预取 FIFO，其 X/Y、计算控制器和算术保持相同。
每个 Bank 独立以 4 B/周期发起 W 读、下一周期返回，收集 16 B 后发送两 Flit 数据包。
预取从计时开始即可进行，允许与输入加载和计算重叠。

每源初始八个包信用，预留覆盖正在组装、网络在途和目的 FIFO；
目的 MAC 消耗完一个 16 B 包后才通过网络返回一 Flit 信用。
信用不会零延迟回到源，也不需要逐字读请求/ACK往返。
源数据按计划顺序 DMA 推送，描述符在计时前就绪；这是一种有利于 Central 的已知访问流预取。
集中 NI 在信用、结果和（共址时的）输入之间轮询；没有无限入口带宽。

每组 MAC 仍先写自己的 Y 再发出结果。两种方案的结果包都交付 Host 0，
经同一个每周期最多四 INT32 的结果写口提交；每个输出索引只能出现一次。
无 K 切分，因此只需结果收集，不人为增加 READ/ADD 归约服务。
数值 Golden 来自原始 W/x 的独立点积与 INT32 回绕。

## 计时和带宽

- `total_cycles`：从输入已在 Host、开始分发/权重预取，到 Host 输出已写好且全部计算、信用和网络排空。
- `host_output_ready_cycles`：最后一个输出在 Host 提交的边沿。
- `input_ready_cycle`：所有组 X 就绪并开始计算的周期。
- `kernel_to_host_cycles = total_cycles - input_ready_cycle`：包含计算、通信、输出写入和排空，不是纯 MAC 时间。
- 不计权重从外部 DRAM 到各 Bank 的初始化、任务配置下发、Host 初始读取 x、后续量化。
  `total_cycles` 仅为上述 warm-resident 算子边界，不能称完整模型推理时间。
- `link_interval`=1/2/4：网络每 1/2/4 个核心时钟提供一次 Flit 传输机会，
  对应每条有向链路 16/8/4 B/核心周期的原始带宽。
  所有网络端口包括集中入口均采用相同配置，Bank/MAC 时钟不变。
  两 Flit 数据包中一 Flit 是 Header，不能把原始带宽当作有效权重带宽。
- Router stall 以网络 tick 计；MAC 等待以 cluster-cycle 计；各计数有重叠，不能相加成总周期。

## 实验与局限

配置：[round1.json](../../ini/central_comparison/round1.json)。
三个形状 128×128、128×512、256×512；九个 NMC 点和十八个 Central 点。
Central 分别位于 Host 0 或内部节点 36，检查角落位置是否决定结果。
每组对照固定相同 Worker、权重、输入、精度与最终输出位置。
相同 seed 的随机 INT8 数据只用于数值验证，不代表真实模型量化精度。

集中端单 NI 构成明确的物理收敛带宽瓶颈。当前结果不能外推到多入口集中阵列、
更宽互连、原生广播、大突发/侧带信用、GEMM 批内权重复用或 DRAM 冷加载场景。
后续优先检查集中入口扩展及传输粒度，而非只放大矩阵。
新增数据流时序属于模型假设；复用 Router/MAC 并不等于新系统整体已有 RTL 证据。

## 复现

```bash
python -m pytest -q -p no:cacheprovider src/tests/test_central_comparison.py
python tools/experiments/central_comparison.py --output-dir dump/central_round1
python tools/experiments/plot_central_comparison.py --input-dir dump/central_round1
```

结果保留逐点数值、周期、有限资源上界、通信量、输入哈希及源码哈希。
使用与历史实验不同的目录，避免混用新流式输入和旧 stop-and-wait 输入周期。
