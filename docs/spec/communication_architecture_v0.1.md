# Digital NMC Communication Architecture v0.1

状态：`IMPLEMENTATION DRAFT v0.1`
适用范围：NI、Packet/Flit、2×2 Mesh Router、端点归约、事件模型与通信验证
上游边界：`compute_architecture_v0.1.md` 中冻结的 ResultBeat 与 LocalDone
版本目标：定义第一条能由事件模型和 RTL 共同执行的 Tile 间通信语义

## 1. 规范目标

本规范解决一个具体问题：Compute、Mapper、NI、Router 和验证环境必须使用同一套消息、路由、反压、归约和完成定义。

v0.1 必须保证：

1. Compute 只产生 ResultBeat，不感知 Packet 和 Router；
2. NI 能无歧义地把 FINAL、PSUM 和远程存储写请求转换为 Packet；
3. Router 在有限 FIFO 下不丢包、不重复、不改写数据；
4. 行切分通过输入分发和 FINAL 收集完成；
5. K 维切分通过 PSUM 传输和目标端归约完成；
6. `LocalDone` 与全局 `TRANSACTION_DONE` 保持不同语义；
7. 同一个 Packet Trace 能驱动功能模型、事件模型和 RTL Testbench。

本文中的“必须”为外部可见的架构行为。“可以”为实现选择，但不得改变外部结果。

## 2. v0.1 范围

### 2.1 本版支持

- 2×2 Mesh、4 个同构 Tile；
- 每个 Router 五个端口：North、East、South、West、Local；
- 128-bit Flit 数据字段；
- 单虚通道；
- 每输入端口 4-Flit FIFO；
- 确定性 XY 路由；
- Wormhole Packet 转发；
- 每输出端口 Round-Robin 仲裁；
- valid/ready 逐跳反压；
- 源端复制的输入分发；
- FINAL 结果收集；
- 目标端 INT32 PSUM 归约；
- 每个 NI 同时一个活动 transaction；
- 通信性能计数器。

### 2.2 本版不支持

- Router 原生组播；
- Router 内归约；
- 自适应路由；
- 多虚通道和 QoS；
- 多 transaction 并发；
- Packet 重传、链路 CRC 和容错路由；
- DMA、计算与通信重叠；
- Cache coherence；
- FPGA 专用物理链路。

这些项目属于后续研究变量，不得以隐藏字段加入 v0.1。

## 3. 固定参数

| 参数 | v0.1 值 | 说明 |
|---|---:|---|
| `MESH_X` | 2 | 横向 Router 数 |
| `MESH_Y` | 2 | 纵向 Router 数 |
| `TILE_COUNT` | 4 | Tile 与 Router 数 |
| `PORT_COUNT` | 5 | N/E/S/W/Local |
| `FLIT_DATA_W` | 128 | Flit 数据字段位宽 |
| `VC_COUNT` | 1 | 单虚通道基线 |
| `INPUT_FIFO_DEPTH` | 4 | 每个 Router 输入 FIFO 深度 |
| `NI_INJECT_FIFO_DEPTH` | 4 | 每个 NI 注入 FIFO 深度 |
| `NI_EJECT_FIFO_DEPTH` | 4 | 每个 NI 接收 FIFO 深度 |
| `TID_W` | 16 | transaction ID 位宽 |
| `TILE_ID_W` | 8 | 与 Compute ResultBeat 一致 |
| `LOCAL_ADDR_W` | 16 | 远程写入目标 Tile 的 byte address |
| `MANAGEMENT_TILE` | 0 | 无可用配置时接收本地故障的固定管理 Tile |

坐标和 Tile ID 必须与计算规范一致：

```text
tile_id = tile_y * MESH_X + tile_x

(0,0) -> Tile 0    (1,0) -> Tile 1
(0,1) -> Tile 2    (1,1) -> Tile 3
```

本文规定 `x` 向 East 增大，`y` 向 South 增大。

## 4. 分层边界

```text
Mapper / Host Runtime
        │ TileTask + TransactionConfig + MEM_WRITE
        ▼
┌──────────────────────────── Tile ────────────────────────────┐
│ Compute ── ResultBeat/LocalDone ──► NI / Endpoint Reduction │
│                                         │                    │
│                              Packetize / Depacketize         │
└─────────────────────────────────────────┼────────────────────┘
                                          │ Flit valid/ready
                                          ▼
                              2-D Mesh Router Network
```

责任边界：

| 模块 | 负责 | 不负责 |
|---|---|---|
| Compute | 本地 GEMV、ResultBeat、LocalDone | Packet、路由、全局完成 |
| NI | Packet 化、注入/接收、远程写、配置匹配 | 路由选择和 MAC |
| Router | XY 路由、仲裁、FIFO、反压 | 数值归约和 transaction 语义 |
| Endpoint Reduction | FINAL 收集、PSUM 匹配与 INT32 归约 | 计算本地 GEMV |
| Completion | 判断全局输出和全部源是否完成 | 推断缺失的 Mapper 配置 |

## 5. 链路与 Flit 接口

每个单向链路使用：

```systemverilog
flit_valid
flit_ready
flit_type[1:0]
flit_data[127:0]
```

一次 Flit 只在上升沿满足下式时发生传输：

```text
flit_valid && flit_ready
```

发送方在 `valid=1 && ready=0` 时必须保持 `flit_type` 和 `flit_data` 稳定。接收方不得把未握手的 Flit 写入 FIFO。

### 5.1 Flit 类型

| 编码 | 名称 | 含义 |
|---:|---|---|
| `00` | `HEAD` | 多 Flit Packet 的首 Flit |
| `01` | `BODY` | 中间 Flit；v0.1 保留 |
| `10` | `TAIL` | 多 Flit Packet 的末 Flit |
| `11` | `HEADTAIL` | 单 Flit Packet |

`flit_type` 是链路 sideband，不占用 `flit_data[127:0]`。性能统计中的一个 Flit 指一次完整的 type、data、valid/ready 握手。

## 6. Packet 格式

### 6.1 公共 Header

所有 HEAD 或 HEADTAIL 的低 44 bit 均使用以下布局：

| Bits | 字段 | 说明 |
|---|---|---|
| `[3:0]` | `packet_type` | Packet 类型 |
| `[11:4]` | `src_tile` | 源 Tile |
| `[19:12]` | `dst_tile` | 目标 Tile |
| `[35:20]` | `tid` | transaction ID |
| `[43:36]` | `status` | 正常 Packet 必须为 `OK=0` |
| `[127:44]` | `type_specific` | 各 Packet 类型定义 |

保留位发送时必须置 0，接收时不得用于推断语义。多 byte 字段采用 little-endian 数值语义；位布局以上表为准。

### 6.2 Packet 类型与长度

| 编码 | 类型 | 长度 | 用途 |
|---:|---|---:|---|
| `0x1` | `RESULT` | 2 Flits | FINAL 或 PSUM ResultBeat |
| `0x2` | `MEM_WRITE` | 2 Flits | 向远端 Tile 本地 SRAM 写 32-bit word |
| `0x3` | `MEM_ACK` | 1 Flit | 远端写入完成或失败 |
| `0x4` | `TRANSACTION_DONE` | 1 Flit | 全局 transaction 完成 |
| `0xF` | `ERROR` | 1 Flit | Compute、NI、Reduction 或协议错误 |

### 6.3 RESULT Packet

Header 的 type-specific 字段：

| Bits | 字段 |
|---|---|
| `[59:44]` | `base_index` |
| `[63:60]` | `lane_mask` |
| `[64]` | `result_kind`：0=`FINAL`，1=`PSUM` |
| `[65]` | `result_last` |
| `[127:66]` | 保留，置 0 |

TAIL 的数据布局固定为：

```text
flit_data[ 31:  0] = result_data[0]
flit_data[ 63: 32] = result_data[1]
flit_data[ 95: 64] = result_data[2]
flit_data[127: 96] = result_data[3]
```

无效 Lane 数据必须为 0。RESULT Packet 必须恰好为一个 HEAD 和一个 TAIL，不得出现 BODY。

### 6.4 MEM_WRITE Packet

Header 的 type-specific 字段：

| Bits | 字段 |
|---|---|
| `[59:44]` | `local_addr`，目标 Tile byte address |
| `[63:60]` | `wstrb` |
| `[79:64]` | `sequence`，由发送 NI 分配 |
| `[127:80]` | 保留，置 0 |

TAIL：

```text
flit_data[31:0]   = wdata
flit_data[127:32] = 0
```

接收 NI 先验证地址、对齐和 `wstrb`，再通过 Compute 规范的本地存储接口提交写请求。v0.1 把 `mem_req_valid && mem_req_ready` 的写握手视为写入提交点，不等待读响应通道；握手后才返回相同 `(tid, sequence)` 的 OK MEM_ACK。请求在提交前检查失败时返回带错误状态的 MEM_ACK，且不得修改 SRAM。

v0.1 的“广播”是源端复制：发送方为每个目标 Tile 生成一份相同数据、不同 `dst_tile` 的 MEM_WRITE。Router 不复制 Packet。Host 必须收到全部 MEM_ACK 后才可启动依赖这些数据的 TileTask。

### 6.5 单 Flit 控制 Packet

`MEM_ACK`：

```text
[59:44] sequence
[127:60] reserved = 0
```

`TRANSACTION_DONE`：

```text
[59:44] completed_element_count
[63:60] completed_source_mask
[127:64] reserved = 0
```

`ERROR`：

```text
[59:44] error_detail
[127:60] reserved = 0
```

ERROR 的 `status` 必须非 0。TRANSACTION_DONE 的 `status` 必须为 0。

### 6.6 统一状态码

| 值 | 状态 |
|---:|---|
| 0 | `OK` |
| 1 | `BAD_OPCODE` |
| 2 | `BAD_FIELD` |
| 3 | `BAD_SHAPE` |
| 4 | `BAD_TILE` |
| 5 | `ADDR_RANGE` |
| 6 | `ADDR_ALIGN` |
| 7 | `PROTOCOL_ERROR` |
| 8 | `BAD_PACKET` |
| 9 | `CONFIG_MISS` |
| 10 | `DUPLICATE_RESULT` |
| 11 | `KIND_MISMATCH` |
| 12 | `MEM_ERROR` |

Compute LocalDone 的状态 1–7 必须原样映射到 ERROR Packet。通信状态 8–12 由 NI 或 Reduction 产生。

## 7. Compute—NI 接口规则

### 7.1 ResultBeat Packet 化

NI 必须把一个 ResultBeat 原子地转换为两个 Flit。只有注入 FIFO 至少有两个空槽时，NI 才能令 `result_ready=1`。

在一次 ResultBeat 握手后：

1. 先压入 RESULT HEAD；
2. 再压入 RESULT TAIL；
3. 两个 Flit 之间不得插入另一个 Packet 的 Flit；
4. `src_tile` 为本地 Tile，`dst_tile` 来自 ResultBeat；
5. `tid`、`base_index`、`lane_mask`、`kind`、`last` 和四个 Lane 数据逐位保持一致。

NI 不得在只剩一个空槽时先接受 ResultBeat 再等待第二个槽位。这条规则避免 Compute 已认为结果被接收，而 NI 只保存了一半 Packet。

### 7.2 LocalDone

成功的 LocalDone 不产生独立 Packet。来源是否结束由最后一个 RESULT Packet 的 `result_last=1` 表示。

错误 LocalDone 不产生 RESULT；NI 必须根据当前 TransactionConfig 的 `root_tile` 生成一个 ERROR Packet，并使用 LocalDone 的 `tid` 和状态。

若错误 LocalDone 的 tid 没有匹配配置，NI 向固定的 `MANAGEMENT_TILE=0` 发送 `CONFIG_MISS` ERROR，并在本地错误寄存器中记录原始 LocalDone 状态。

## 8. TransactionConfig

Mapper/Host 在启动 TileTask 前，必须为参与 transaction 的 NI 写入同一份逻辑配置：

```yaml
version: 1
tid: 16-bit unsigned
mode: GATHER_FINAL | REDUCE_PSUM
root_tile: 8-bit unsigned
host_tile: 8-bit unsigned
source_mask: 4-bit bitset
output_base: 16-bit unsigned
output_count: 16-bit unsigned
```

TransactionConfig 通过 Host 到各 NI 的本地配置端口写入，不封装为 v0.1 NoC Packet。配置握手也采用 valid/ready；生产者在等待 ready 时必须保持全部字段稳定。

约束：

- `root_tile`、`host_tile` 必须小于 4；
- `source_mask` 非 0，且只能使用 bit 0–3；
- `1 <= output_count <= 256`；
- `output_base + output_count - 1` 不得溢出 16 bit；
- 每个 NI 同时最多保存一个活动配置；
- 新配置只能在当前配置 inactive 后接受；
- 所有 RESULT 的 tid、目标、kind、源 Tile 和输出索引必须与配置匹配。

来源 NI 在成功发送 `result_last=1` 后可把本地 source 状态置为 terminal。Root NI 在产生 TRANSACTION_DONE 或 ERROR 后把 root 状态置为 terminal。terminal 配置可以被下一份配置覆盖。

## 9. Endpoint 收集与归约

### 9.1 GATHER_FINAL

`mode=GATHER_FINAL` 时：

- 只接受 `result_kind=FINAL`；
- 每个全局输出索引必须恰好到达一次；
- 结果写入 Root 的 Result SRAM 或等价结果缓冲；
- 重复索引产生 `DUPLICATE_RESULT`；
- 输出索引越界产生 `BAD_FIELD`；
- FINAL Packet 不执行跨 Tile加法。

### 9.2 REDUCE_PSUM

`mode=REDUCE_PSUM` 时，Root 为每个输出索引保存：

```text
acc32
received_source_mask[3:0]
complete
```

收到有效 PSUM 元素 `(tid, index, src_tile, value)` 后：

```text
if source bit already set:
    error DUPLICATE_RESULT
else:
    acc32 = int32_wrap(acc32 + value)
    set received_source_mask[src_tile]

if received_source_mask == source_mask:
    complete = 1
    write final acc32 to result storage
```

归约顺序由 Packet 到达顺序决定。由于 INT32 二进制补码加法按 32-bit 回绕，所有到达顺序必须得到相同的最终 32-bit 结果。

Root 不得用“已收到 Packet 数”代替 source mask。否则重复来源会被错误地计为完成。

### 9.3 全局完成

Root 只有同时满足以下条件才能产生一次 TRANSACTION_DONE：

1. `[output_base, output_base + output_count)` 内每个输出元素均 complete；
2. `source_mask` 中每个来源均已收到一个 `result_last=1`；
3. transaction 未产生 ERROR；
4. 所有最终结果已写入结果存储。

TRANSACTION_DONE 发往 `host_tile`，包含 `output_count` 和 `source_mask`。它只产生一次。

`LocalDone(OK)` 仅说明结果进入源 NI；`TRANSACTION_DONE` 才说明 Root 已完成全部收集或归约。

## 10. Router 架构

### 10.1 输入缓冲

每个输入端口拥有独立的 4-Flit FIFO。Local 注入端口与四个方向端口使用相同 FIFO 和仲裁规则。

上游 `ready` 仅在对应输入 FIFO 有空位且 Router 不在复位时为 1。FIFO full 必须逐跳传播反压，不允许丢弃 Flit。

### 10.2 XY 路由

Router 只根据 HEAD/HEADTAIL 中的 `dst_tile` 计算输出端口：

```text
if dst_x > current_x: EAST
else if dst_x < current_x: WEST
else if dst_y > current_y: SOUTH
else if dst_y < current_y: NORTH
else: LOCAL
```

BODY/TAIL 沿用所属 Packet 在 HEAD 时锁定的输出端口。目标超出 0–3 时，不得送入某个方向端口；Router 丢弃该完整 Packet、增加错误计数并向本地 NI 报告 `BAD_TILE`。

### 10.3 Wormhole 与输出锁

- HEAD 赢得某输出后，该输出锁定到对应输入；
- 直到该 Packet 的 TAIL 握手后才释放；
- HEADTAIL 在同一次握手后立即释放；
- 被锁输入暂时没有下一个 Flit时，输出保持保留，不允许其他 Packet 穿插；
- malformed flit sequence 产生 `BAD_PACKET`，并清除到下一个 TAIL/HEADTAIL 以恢复边界。

### 10.4 仲裁

每个输出端口独立 Round-Robin。只有请求同一输出且没有被现有 Packet 锁占的输入参加仲裁。

一次 Packet 完成后，优先级指针移动到获胜输入的下一个输入。持续请求者在其他有限长度 Packet 完成后必须获得服务。

### 10.5 单 VC 的依据

v0.1 的 XY 路由先完成 X 维，再进入 Y 维，不会从 Y 维返回 X 维，因此通道依赖图无环。单 VC 足以保证这一固定路由基线的网络级死锁自由。

端点不得在持有 Router 通道时等待 MEM_ACK 或 TRANSACTION_DONE；完整 Packet 必须先进入 NI FIFO，再进入协议等待。因此请求—响应依赖不会形成新的通道环。

单 VC 会产生 head-of-line blocking，这正是 v0.1 事件模型要测量的现象，不在本版通过额外 VC 隐藏。

## 11. NI 接收规则

NI 先按 Flit 类型重组完整 Packet，再向本地消费者提交。它必须检查：

- Packet 类型与长度匹配；
- HEAD、TAIL 顺序合法；
- 保留位为 0；
- `dst_tile` 等于本地 Tile；
- tid 和 TransactionConfig 匹配；
- RESULT kind、source 和 index 合法；
- MEM_WRITE 地址、对齐和 wstrb 合法。

接收 FIFO 或本地消费者阻塞时，NI 必须降低 Local ejection `ready`，让反压传播到 Router。不得丢弃一个格式正确但暂时无法消费的 Packet。

## 12. 复位与错误恢复

- 所有 Router、NI 和 Reduction RTL 使用与 Compute 相同的低有效同步复位 `rst_n`；
- 复位清空 FIFO、输出锁、Packet 重组状态、活动配置、归约 mask 和未完成事件；
- 复位不要求清空 Tile SRAM；
- 复位后的未完成 transaction 视为已取消，软件必须重新加载配置和数据；
- 一个 transaction 的首个 ERROR 终止该 transaction；
- 终止后不得再产生 TRANSACTION_DONE；
- 后续同 tid Packet 可以被排空，但不得修改最终结果。

## 13. 强制性能计数器

事件模型和 RTL 至少暴露：

| 位置 | 计数器 |
|---|---|
| 每条方向链路 | `flit_count`、`busy_cycles` |
| 每个 Router 输入 | `fifo_full_cycles`、`max_occupancy` |
| 每个 Router 输出 | `arbitration_wait_cycles` |
| 每个 NI | `injected_packets`、`ejected_packets`、`inject_stall_cycles` |
| Root Reduction | `psum_elements`、`duplicate_errors`、`reduction_wait_cycles` |
| Transaction | `start_cycle`、`done_cycle`、`makespan` |

派生指标统一定义：

```text
flit_hops = 所有方向链路 flit_count 之和
link_utilization = busy_cycles / measured_cycles
transaction_makespan = done_cycle - start_cycle
```

Local 注入和 ejection 不计入 flit_hops；只计算 Router 之间的方向链路。

## 14. 必须通过的通信测试

### 14.1 Comm 0A：ResultBeat Packet 化

Tile 0 向 Tile 1 发送一个带两个有效 Lane 的 FINAL ResultBeat。验证：

- Compute 仅在 NI 有两个空槽时看到 ready；
- 精确生成 HEAD、TAIL 两个 Flit；
- 所有 Header 字段和 Lane 位序正确；
- 阻塞时 Flit 保持稳定；
- 目标 NI 精确重建一个 RESULT Packet。

### 14.2 Comm 0B：两跳 XY

Tile 0 向 Tile 3 发送一个 RESULT。路径必须是：

```text
Tile 0 (0,0) -> East -> Tile 1 (1,0) -> South -> Tile 3 (1,1)
```

两个 Flit 的 `flit_hops` 必须为 4，且不得经过 Tile 2。

### 14.3 Comm 0C：有限 FIFO 与反压

Comm 0C 分为两个阶段：

1. 当 Tile 3 的 Local 输出出现 `valid=1` 后，把 ejection ready 连续拉低 3 周期；
2. 使用连续 Packet 流量继续阻塞 Tile 3，直到有限 FIFO 填满并使源 NI 出现 injection stall。

第一阶段验证：

- 已经 valid 的 Flit 数据稳定；
- 恢复 ready 后无丢失、重复或乱序。

第二阶段验证：

- FIFO occupancy 不超过 4；
- 反压逐跳传回源 NI；
- stall 和 full 计数器增加。

固定 3 周期只用于检查 valid/ready 稳定性，不要求在一个初始为空、包含多级 FIFO 的网络中恰好 3 周期传到源端。反压传播延迟由路径长度和当时 FIFO occupancy 决定。

### 14.4 Demo 1：行切分

Host/Tile 0 使用源端复制 MEM_WRITE，把同一输入分发到两个计算 Tile并收齐 ACK；两个 Tile 产生互不重叠的 FINAL 索引范围；Root 执行 GATHER_FINAL。

验证最终向量与 NumPy Golden 一致、每个索引恰好一次、每个 source 的 last 被记录，并只产生一次 TRANSACTION_DONE。

2026-10-08 实现证据：`src/simulator/system/demo1.py` 已把 MEM_WRITE/ACK、本地 GEMV、ResultBeat→Packet、FINAL 收集和 Host DONE 连成可运行链路。`src/tests/test_nmc_demo1.py` 的 18 项测试通过，运行指南与事件记录见 [Demo 1 验证](../validation.md#demo1)。Compute 使用功能模型，通信采用周期模型；尚不能作为 GEMV 延迟、RTL 等价或完整 NI 事务生命周期的证据。本规范仍保持 IMPLEMENTATION DRAFT。

### 14.5 Demo 2：K 维切分

两个来源对同一输出范围分别产生 PSUM，发送到 Root。验证：

- 按 `(tid, index, src_tile)` 匹配；
- INT32 wrap 与 Golden 一致；
- 只有 source mask 完整后结果才 complete；
- 重复来源产生 DUPLICATE_RESULT；
- FINAL/PSUM 类型不匹配产生 KIND_MISMATCH。

2026-10-08 实现证据：`src/simulator/system/demo2.py` 复用 Demo 1 调度链，按 K 分发输入段并计算 PSUM；Root 为每个全局输出维护 INT32 accumulator 和 received source mask，完整 mask 后才写入最终 SRAM。`src/tests/test_nmc_demo2.py` 覆盖端到端结果、重复来源、包级原子检查、到达顺序、回绕、尾部和反压，运行说明见 [Demo 2 验证](../validation.md#demo2)。Compute 与归约仍为功能模型，尚未提供归约吞吐时序、RTL 等价或完整 ERROR Packet 传播证据，本规范仍为 IMPLEMENTATION DRAFT。

### 14.6 Demo 3：竞争与公平性

至少三个输入同时请求同一输出，目标端周期性阻塞。验证 Round-Robin 服务顺序、Packet 输出锁、有限 FIFO、反压、最终完成和计数器。

### 14.7 协议错误

至少覆盖非法目标、错误 Packet 长度、TAIL 无 HEAD、保留位非 0、配置缺失、tid 不匹配、输出索引越界和 ERROR 后不得 DONE。

## 15. 与研究问题的关系

v0.1 固定机制，不预设哪种映射更优。后续 Mapper 和实验在同一实现上改变：

- 行切分或 K 维切分；
- Tile 放置和 Root 位置；
- Packet 注入顺序；
- 分组归约拓扑；
- FIFO 深度；
- Tile 数量和流量形状。

比较指标包括 makespan、flit_hops、链路利用率、FIFO full 周期、MAC 等待通信周期和归约等待周期。

Router 组播、Router 内归约、多 VC 和自适应路由只有在 v0.1 Baseline 产生可复现瓶颈后才作为新机制加入。

## 16. 实现顺序与冻结条件

建议按以下顺序实现：

1. Packet dataclass 与 pack/unpack；
2. NI ResultBeat Packet 化；
3. 单 Router FIFO、XY 和仲裁；
4. 2×2 Mesh 事件模型；
5. Endpoint GATHER_FINAL；
6. Endpoint REDUCE_PSUM；
7. Comm 0A–0C 与 Demo 1–3；
8. 对应 RTL 模块与同 Trace 对照。

本文件当前为 `IMPLEMENTATION DRAFT`。满足以下条件后升级为 `FROZEN v0.1`：

- Packet pack/unpack 测试逐位通过；
- Comm 0A–0C 通过；
- Demo 1 与 Demo 2 数值结果和完成条件通过；
- 对所有 valid/ready 接口执行稳定性断言；
- 事件模型与 RTL 对相同 Trace 的 Packet 顺序、结果和错误一致；
- 不再存在影响编码的字段位宽、Packet 长度或完成语义歧义。

冻结后，任何改变 Packet 布局、路由顺序、归约 key 或完成条件的修改必须升级版本，并记录 Architecture Decision Record。
