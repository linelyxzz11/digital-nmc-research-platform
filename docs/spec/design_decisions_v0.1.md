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
