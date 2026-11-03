# v0.1 设计决定与歧义审计

合并原 ADR-0001、ADR-0002 与两份审计记录（2026-09-20）。字段、编码和参数以[计算规范](compute_architecture_v0.1.md)、[通信规范](communication_architecture_v0.1.md)为准；本文记录选择理由和验证边界。

<a id="compute"></a>

## 计算边界：已冻结

Mapper、功能模型、事件模型、RTL 与 NI 共用 TileTask、字节地址、Weight Word、ResultBeat 和 LocalDone 语义。采用 4-Lane output-stationary INT8 MAC、INT32 回绕、little-endian、深度 1 的 Result FIFO；同步 SRAM 读延迟为一周期，无阻塞参考时间为 `G*(K+4)+1`。

| 取舍 | 原因与后果 |
|---|---|
| 固定 `(group,k,lane)` 权重布局 | 避免 Mapper、Python、RTL 各自解释地址 |
| Compute 只输出 ResultBeat 和 LocalDone | Packet/Flit、Router、VC 与归约拓扑由通信层负责，不绑定 MAC 核心 |
| LocalDone 与全局完成分离 | 防止 PSUM 未归约、Packet 未到达时提前完成 |
| 同时规定功能与参考时序 | 后续事件模型与 RTL 才能逐周期比较 |

数据类型、Lane 数、地址语义或 Compute–NI 接口改变时，须提升规范版本并检查双方实现。Python 功能测试不能替代事件模型或 RTL。

<a id="compute-audit"></a>

### 计算歧义关闭记录

| # | 原歧义 | 冻结决定 | 自动证据或后续边界 |
|---:|---|---|---|
| 1 | descriptor enum、boolean 和位宽非法时没有处理边界 | 无法编码的逻辑任务在握手前抛出构造错误；已编码的 version/enum 使用 `BAD_FIELD` | 测试覆盖 tid 越界、非 boolean、version 和 result kind |
| 2 | `result_dst_tile` 合法范围未定义 | 必须满足 `0 <= dst < TILE_COUNT`，否则 `BAD_TILE` | Demo 0D 目标 Tile 越界用例 |
| 3 | 多个错误同时出现时结果不确定 | 固定 `BAD_OPCODE > BAD_FIELD > BAD_SHAPE > BAD_TILE > BAD_FIELD(index) > ADDR_ALIGN > ADDR_RANGE` | Demo 0D opcode 与 shape 冲突用例 |
| 4 | ResultBeat Lane 与展开总线位序未定义 | Lane i 对应 base+i，展开为 `[32*i +: 32]`，无效 Lane 数据固定 0 | Demo 0A/0B 的 mask 和 data 检查 |
| 5 | LocalDone 反压行为未定义 | valid 和 payload 保持稳定，握手前 task_ready 保持 0 | Demo 0C 连续三周期 done stall |
| 6 | `task_last=0` 是否仍产生 LocalDone 不清楚 | 每个 TileTask 恰好一个 LocalDone；task_last 只决定最后一拍的 result_last | 规范第 10、11 节 |
| 7 | FETCH/MAC 是否流水会改变周期 | 事件模型与参考 RTL 固定同步读一周期，填充后每周期一个 k | 5×3 Trace 与 `T=G*(K+4)+1` |
| 8 | 多 byte 数据的 byte order 未定义 | 全部采用 little-endian，明确 wstrb 到 byte lane 的映射 | Weight Word 表和 Result SRAM 读取测试 |
| 9 | reset 极性及 SRAM 行为缺失 | `rst_n` 低有效同步复位；清控制状态，不要求清 SRAM | RTL 阶段必须新增 reset test |
| 10 | global output index 可能溢出 16 bit | `global_row_base + m_local - 1 <= 0xFFFF` | Demo 0D index overflow 用例 |

计算审计没有遗留阻塞实现的开放项。但 `ResultEmitter` 只验证事件顺序和稳定性；reset、SRAM 同步读与精确流水仍需事件模型及 RTL 验证。本次冻结不证明性能或可扩展性，FPGA 不是完成条件。

<a id="comm"></a>

## 通信边界：实现草案

基线采用 2×2 Mesh、确定性先 X 后 Y、单 VC、128-bit Flit 与 2-bit 类型边带、每 Router 输入 4-Flit FIFO、wormhole 输出锁与逐输出轮询仲裁。输入分发由源端复制单播，FINAL 收集和 PSUM 归约在端点完成，每 NI 一个活动 transaction。

XY 的通道依赖无环，为此基线提供网络路由层的死锁依据；它不自动证明应用依赖和端点等待无死锁。单 VC 的队头阻塞、源端复制开销保留为后续实验的基线。3×3 图仅作扩展性示意，不是 v0.1 参数。

<a id="comm-audit"></a>

### 通信歧义关闭记录

| Topic | v0.1 decision | Why it is now unambiguous |
|---|---|---|
| Executable topology | 2×2, Tile 0–3 | Uses the same tile-id equation as Compute v0.1 |
| Diagram topology | 3×3 only as a scalability illustration | It is no longer an implementation parameter |
| Virtual channels | One | Replaces the earlier figure note that assumed two |
| Routing | Deterministic X then Y | Every valid source/destination has one path |
| Flit width | 128-bit data plus 2-bit type sideband | Header and payload bit positions are fixed |
| Buffering | Four flits per router input; four at NI injection/ejection | Backpressure tests can use exact occupancy limits |
| Packet switching | Wormhole with output lock through TAIL | Packet flits cannot interleave |
| Arbitration | Per-output round robin | Contention order and fairness can be tested |
| Input distribution | Source-replicated MEM_WRITE unicasts | Router multicast is outside v0.1 |
| Reduction location | Destination endpoint | Router never performs arithmetic in v0.1 |
| Reduction identity | `(tid, output index, source tile)` | Duplicate sources cannot falsely satisfy completion |
| Arithmetic | Signed INT32 wrap | Matches Compute v0.1 |
| Local versus global done | LocalDone after Compute-to-NI handoff; TRANSACTION_DONE after all final outputs | Prevents early completion |
| Remote-write commit | Accepted local memory write handshake | Does not invent an undefined write-response rule |
| Missing-config error target | Fixed management Tile 0 | Error routing does not depend on a missing config |

### 后续扩展

These are research extensions rather than v0.1 ambiguities:

- multi-VC allocation and class separation;
- router multicast;
- router or in-network reduction;
- adaptive routing;
- more than one in-flight transaction per NI;
- DMA/compute/communication overlap;
- retry, CRC, and fault tolerance;
- FPGA physical link and clock-domain crossing.

### 通信冻结前仍须完成的证据

1. Exhaustive packet pack/unpack tests for every field boundary;
2. Comm 0A–0C event-model tests;
3. row-partition Demo 1 and K-partition Demo 2 end-to-end tests;
4. valid/ready stability assertions under randomized backpressure;
5. a malformed-packet recovery test;
6. event-model and RTL trace equivalence for at least one RESULT, one MEM_WRITE, and one reduction transaction.

Until these checks pass, the specification remains `IMPLEMENTATION DRAFT v0.1` even though its encoding decisions are fixed for initial coding.

2026-10-06 已有 Packet 与 Comm 0A–0C 的 10 项 Python 测试记录，见[验证指南](../validation.md#comm)。这不等于上述冻结条件全部满足；Demo 1/2、随机反压、错误恢复及 RTL 对照仍需相应证据。

## 2026-10-28：Root 最终写回的内部宽接口

Result SRAM 的四个 Bank 经显式向量复用器接受 Compute 或 Root 的一项最多四字写入，再与 Host 32-bit 口复用。Compute 在任务期间独占；IDLE 优先级为未完成/新 Host 请求、Root、新任务。固定优先级避免同时驱动 Bank，但不保证饥饿自由；现有分阶段调度不依赖争用公平性。

合法 Root 请求在 ready/valid 握手的同一上升沿原子提交，错误通过同一边沿的 `root_wr_error` 返回，不增加响应缓冲或气泡。保留非 16-byte 对齐和稀疏 Lane，全部有效地址先校验，避免低位 Bank 寻址掩盖越界。连续全宽写验证每周期四字，支持默认 W=1；SRAM 仍为六块单端口、49,152 bit。

Python `write_request()` 与 `step(write_ready=...)` 对接同一合同，端口停顿期间不提交输出或推进 Done。51 组对照包含真实 Host 占用造成的六周期写等待。此项选择仅校准最终写口；Root 归约存储、ALU、Packet/Done 控制及器件时序仍需后续实现。[详细合同和证据](root_writeback_rtl_v0.1.md)。

## 64 Tile 扩展：保留校准合同，扩展计划和端点图

v0.2 复用原 TileTask、SRAM 布局、Compute 周期模型、有限 Root 和 Router 仲裁，拓扑/codec 改为实例参数，v0.1 默认线格式不变。二维分块按输出范围配置直接来源表；中间端点使用一项输出寄存器，最后一组等待本地来源完成。树归约使用同一 64 Tile 物理预算中的闲置 Tile，端点能力预算对所有策略一致。

主实验为 8×8，RTL 继续做局部校准。三台电脑分担独立完整案例，公共清单固定源码/计划哈希，结果记录实际 commit；完整性、数值和版本校验通过后才能合并。此选择避免多份仿真代码及跨机器逐周期同步。[v0.2 合同](scalable_execution_v0.2.md)。

## v0.3：有限预算自动映射与集成 RTL

共同候选范围保持不变，新增 predictor→top-K→精确执行的自动选择。UniNDP 适配保留可核对的平坦 PU 划分/剪枝/分配与搜索流程，SRAM 代价替换 DRAM 后端；通信感知策略增加实际单播距离和 XY 链路资源预留。两者共享有限 Tile/Root/NoC evaluator，精确预算相同，事后最优只用作质量参考，不能把启发式或移植子集声称为完整 UniNDP。[搜索合同](mapping_search_v0.3.md)。

新增 64 Tile 集成 RTL，计算与树节点共享固定物理预算。NI 注册队列、真实窄 X 写入、任务接受/VALIDATE 使系统周期与旧 Python 功能输入提交有差别；数值/包协议必须完全一致，周期偏差按输入与 kernel 单独保存。默认四 Lane 归约端点直接对齐 TimedRoot 的每个服务边沿；不把集成仿真通过说成芯片 PPA 或所有参数已校准。[RTL 范围](../../src/rtl/nmc_mesh/README.md)。

## v0.4：修复输入开销与有界反压预测

输入 stop-and-wait 的参考协议为每包 `2*d+5`，原 NoC 预测多算两周期。仅修正常数仍会低估长输出 K 切分的拥塞，因此改用有限 credit 的整包逐跳近似，并把源结果释放与 NI 握手关联；加载留下的 RR 状态也恢复。它复用端点服务合同，但不执行真实矩阵计算或完整 Flit Mesh，不叫精确 evaluator。保留旧预测与强资源基线，显式报告每候选包级推演开销，并以冻结后的新形状验证。[合同与复现](bounded_prediction_v0.4.md)。
