# Digital NMC Compute Architecture v0.1

状态：`FROZEN v0.1`（2026-09-20 完成接口歧义审计）
适用范围：功能模型、Mapper、事件模型、Tile RTL、NI 接口与验证环境
版本目标：冻结第一条能够被软件和硬件共同执行的本地 GEMV 语义

## 1. 规范目标

v0.1 定义一个最小但完整的数字 NMC 计算 Tile。它必须满足以下要求：

1. 同一份 Tile 任务描述可以被功能解释器、事件模型和 RTL 执行；
2. 同一个本地 GEMV 在所有实现中得到完全一致的 INT32 结果；
3. 计算模块通过唯一的 valid/ready 结果接口连接 NI；
4. 行切分和 K 维切分复用同一套计算硬件；
5. 输出反压时不丢失、不重复、不覆盖结果；
6. 本地计算完成与全局 transaction 完成具有不同语义。

本文中的“必须”表示 v0.1 的强制行为。“可以”表示实现选择，但不得改变外部可见结果。

## 2. v0.1 范围

### 2.1 本版支持

- 单 Tile 本地 GEMV；
- 2×2 Mesh 中的 4 个同构 Tile；
- 行切分产生最终结果；
- K 维切分产生部分和；
- 有符号 INT8 输入和权重；
- 有符号 INT32 累加、部分和与最终结果；
- 4-Lane output-stationary Vector MAC；
- 本地 SRAM 预加载；
- 结果 valid/ready 反压；
- 本地完成和错误事件。

### 2.2 本版不支持

- 二维切分；
- 多 transaction 并发执行；
- DMA、计算和通信重叠；
- Ping-Pong Buffer；
- Router 内归约；
- 稀疏计算；
- 浮点、饱和运算、ReLU、Bias 和重量化；
- Cache 与一致性；
- 乱序指令；
- FPGA 专用总线。

这些能力不得通过未定义字段或隐藏状态加入 v0.1。

## 3. 系统参数

| 参数 | v0.1 值 | 说明 |
|---|---:|---|
| `MESH_X` | 2 | Mesh 横向 Tile 数 |
| `MESH_Y` | 2 | Mesh 纵向 Tile 数 |
| `TILE_COUNT` | 4 | 同构 Tile 总数 |
| `LANES` | 4 | 每个 Tile 的标量 MAC Lane 数量 |
| `DATA_W` | 8 | 输入和权重位宽 |
| `PROD_W` | 16 | 有符号乘积位宽 |
| `ACC_W` | 32 | 累加器、部分和和结果位宽 |
| `TID_W` | 16 | transaction ID 位宽 |
| `LOCAL_ADDR_W` | 16 | Tile 本地 byte address 位宽 |
| `W_SRAM_BYTES` | 4096 | Weight SRAM 容量 |
| `X_SRAM_BYTES` | 1024 | Input SRAM 容量 |
| `Y_SRAM_BYTES` | 1024 | Result SRAM 容量 |
| `RESULT_FIFO_DEPTH` | 1 | Compute 到 NI 的 ResultBeat 缓冲深度 |

Tile 坐标与编号关系必须为：

```text
tile_id = tile_y * MESH_X + tile_x

(0,0) -> Tile 0    (1,0) -> Tile 1
(0,1) -> Tile 2    (1,1) -> Tile 3
```

### 3.1 时钟与复位

- 所有 v0.1 RTL 接口使用同一个上升沿时钟 `clk`；
- `rst_n` 为低有效同步复位，只在 `clk` 上升沿采样；
- `rst_n=0` 时，控制器回到 `IDLE`，清空 Result FIFO，并拉低 `result_valid` 和 `local_done_valid`；
- 复位清空 accumulator、计数器和所有未完成任务，不要求清空三块 SRAM；
- `rst_n` 返回 1 后，若没有其他阻塞，下一周期允许 `task_ready=1` 和 `mem_req_ready=1`。

## 4. Tile 微架构

```text
                         TileTask
                            │
                            ▼
                    ┌───────────────┐
                    │ Tile Control  │
                    └───────┬───────┘
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
          ▼                 ▼                 ▼
  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ Weight SRAM  │  │ Input SRAM   │  │ Result SRAM  │
  │ 4 × INT8/Rd  │  │ 1 × INT8/Rd │  │ 4 × INT32/Wr │
  └──────┬───────┘  └──────┬───────┘  └──────▲───────┘
         │                  │                  │
         │ w0..w3           │ x                │
         ▼                  ▼                  │
  ┌────────────────────────────────────────────┴──┐
  │          4-Lane Output-Stationary MAC         │
  │ Lane i: acc[i] <- acc[i] + w[i] * x           │
  │ 4 × signed INT8 multiplier                    │
  │ 4 × signed INT32 accumulator                  │
  └───────────────────────┬───────────────────────┘
                          │ ResultBeat
                          ▼
                   ┌───────────────┐
                   │ Result FIFO   │
                   └───────┬───────┘
                           │ valid/ready
                           ▼
                    NI / Reduction
```

每个 Lane 是一个标量 signed INT8 × signed INT8 MAC，而不是一个独立的向量单元。4 个 Lane 共享同一个输入标量 `x[k]`，每个 Lane 读取不同输出行的权重，并维护独立的 INT32 Accumulator。

## 5. 数值语义

### 5.1 类型

```text
weight       : signed INT8
input        : signed INT8
product      : signed INT16
accumulator  : signed INT32
result/psum  : signed INT32
```

第 `i` 个 Lane 的一次更新必须等价于：

```text
product16 = signed(weight8) * signed(input8)
acc32     = acc32 + sign_extend(product16, 32)
```

### 5.2 溢出

INT32 加法采用二进制补码回绕。功能模型必须显式转换到 32-bit signed 结果，不得依赖 Python 无限精度整数。v0.1 Benchmark 应优先选择不触发 INT32 溢出的数据，以便人工核对；溢出行为仍必须有自动测试。

### 5.3 禁止的隐式运算

计算核心不得自动执行：

- 饱和；
- 舍入或重量化；
- Bias；
- 激活函数；
- 不同输出行之间的 Lane reduction。

## 6. 本地存储和地址语义

### 6.1 统一地址规则

所有软件和硬件接口必须使用 byte address。不得在功能模型中使用 element address，而在 RTL 中使用 byte address。

所有多 byte 数值采用 little-endian。对 32-bit word，地址最低的 byte 对应 bits `[7:0]`；Result SRAM 中一个 INT32 的四个 byte 依次存放最低到最高有效 byte。外部存储端口的 `wstrb[i]` 控制 `wdata[8*i +: 8]` 写入 `mem_req_addr+i`。

| 区域 | 地址范围 | 内容 | 端口语义 |
|---|---|---|---|
| Weight SRAM | `0x0000-0x0FFF` | INT8 权重 | 每周期读取 4 个连续 INT8 |
| Input SRAM | `0x1000-0x13FF` | INT8 输入 | 每周期读取 1 个 INT8 |
| Result SRAM | `0x2000-0x23FF` | INT32 结果或部分和 | 一次写入最多 4 个连续 INT32 |

要求：

- `W_base` 必须 4-byte aligned；
- `X_base` 可以按 byte 对齐；
- `Y_base` 必须 4-byte aligned；
- 所有访问必须完全位于对应地址区域内；
- descriptor 在执行前完成全部范围和对齐检查；
- v0.1 执行 GEMV 时不得同时写 Weight SRAM 或 Input SRAM。

### 6.2 外部本地存储访问接口

Host adapter、加载器或 NI 通过统一的 32-bit 本地存储端口预加载数据和读取结果：

```systemverilog
mem_req_valid
mem_req_ready
mem_req_write
mem_req_addr[15:0]
mem_req_wdata[31:0]
mem_req_wstrb[3:0]

mem_rsp_valid
mem_rsp_ready
mem_rsp_rdata[31:0]
mem_rsp_error
```

请求只在 `mem_req_valid && mem_req_ready` 时被接受。读取响应只在 `mem_rsp_valid && mem_rsp_ready` 时被接收。请求方在等待 ready 时必须保持请求字段稳定。

`mem_req_addr` 必须 4-byte aligned，`wstrb` 指定该 32-bit word 中实际写入的 byte。v0.1 在 Tile 不处于 `IDLE` 时令 `mem_req_ready=0`，因此数据加载、计算和外部结果读取严格分阶段，不发生重叠。Input SRAM 中的 INT8 可以通过 `wstrb` 按 byte 写入；Result SRAM 通过 32-bit 读取。

### 6.3 权重布局

本地矩阵逻辑形状为：

```text
W_local[M_local][K_local]
```

行按 4 个一组：

```text
group = local_row / 4
lane  = local_row % 4
```

同一个 `k` 的 4 个 Lane 权重打包为一个 32-bit word：

```text
bits [ 7: 0] = W[4*group + 0][k]
bits [15: 8] = W[4*group + 1][k]
bits [23:16] = W[4*group + 2][k]
bits [31:24] = W[4*group + 3][k]
```

地址公式：

```text
W_addr(group,k) = W_base + 4 * (group * K_local + k)
X_addr(k)       = X_base + k
Y_addr(row)     = Y_base + 4 * row
```

不足 4 行的最后一组仍占用一个 Weight Word。无效 Lane 的权重内容可任意，但它们不得更新可见结果。

## 7. TileTask v0.1

Mapper、功能模型、事件模型和 RTL 必须共享下面的逻辑任务描述。v0.1 先冻结字段和语义，二进制 ISA 编码另行定义。

```yaml
version: 8-bit unsigned, fixed value 1
opcode: GEMV_LOCAL

tid: 16-bit unsigned
tile_id: 8-bit unsigned

m_local: 16-bit unsigned
k_local: 16-bit unsigned

w_base: 16-bit byte address
x_base: 16-bit byte address
y_base: 16-bit byte address

global_row_base: 16-bit unsigned

result_kind: FINAL | PSUM
result_dst_tile: 8-bit unsigned
task_last: boolean
```

字段语义：

| 字段 | 含义 |
|---|---|
| `tid` | 从 Mapper 到最终完成事件保持不变的 transaction 标识 |
| `m_local` | 当前 Tile 需要产生的本地输出数量 |
| `k_local` | 每个本地输出累加的本地 K 长度 |
| `global_row_base` | 本地第 0 个结果对应的全局输出索引 |
| `result_kind` | 行切分为 `FINAL`；K 维切分为 `PSUM` |
| `result_dst_tile` | NI 应将该 ResultBeat 发往的目标 Tile |
| `task_last` | 这是当前 Tile 在该 transaction 中的最后一个 ComputeJob |

约束：

- 所有 unsigned 字段必须能由声明位宽表示，`task_last` 必须为 boolean；无法编码的逻辑任务由 Mapper/Host 在握手前拒绝，不产生 Tile LocalDone；
- 已编码任务中的 `version` 必须等于 1；
- `1 <= m_local <= 256`；
- `1 <= k_local <= 1024`；
- `4 * ceil(m_local/4) * k_local <= W_SRAM_BYTES`；
- `k_local <= X_SRAM_BYTES`；
- `4 * m_local <= Y_SRAM_BYTES`；
- v0.1 同一 Tile 同时最多有一个已接受但未完成的 TileTask；
- `tile_id` 必须等于接收任务的物理 Tile；
- `0 <= result_dst_tile < TILE_COUNT`；
- `result_kind` 只能为 `FINAL` 或 `PSUM`，`task_last` 只能为一位 boolean；
- `global_row_base + m_local - 1` 必须能够由 16-bit unsigned 表示；
- descriptor 非法时不得修改 SRAM 或 Accumulator。

归约根、期望 PSUM 数量和归约拓扑属于 NI/ReductionConfig，不属于 MAC 核心字段。

### 7.1 TileTask 握手

```systemverilog
task_valid
task_ready
task_payload                    // 上述 TileTask 字段
```

TileTask 只在 `task_valid && task_ready` 时被接受。Tile 仅在 `IDLE` 且没有等待接收的 LocalDone 时令 `task_ready=1`。生产者在 `task_valid=1 && task_ready=0` 时必须保持全部任务字段稳定。

## 8. 本地 GEMV 执行语义

计算核心必须执行：

```text
for group in 0 .. ceil(m_local/4)-1:
    lane_mask = valid rows in this group
    acc[0:3] = 0

    for k in 0 .. k_local-1:
        x = InputSRAM[X_addr(k)]
        w[0:3] = WeightSRAM[W_addr(group,k)]

        for lane in 0 .. 3:
            if lane_mask[lane]:
                acc[lane] = int32_wrap(acc[lane] + w[lane] * x)

    emit ResultBeat(acc[0:3], lane_mask)
```

`lane_mask` 的 bit 0 对应 Lane 0。例：最后一组只有一行时必须为 `4'b0001`。

## 9. 控制器状态机

v0.1 控制器的架构状态必须等价于：

```text
IDLE
  │ 接受 TileTask
  ▼
VALIDATE
  │
  ▼
INIT_ACC
  │ 清零 4 个 accumulator
  ▼
FETCH
  │ 发出 Weight/Input SRAM 读请求
  ▼
MAC
  │ 对一个 k 执行 4 个 Lane 更新
  ├── k 未完成 ───────────────► FETCH
  ▼
WRITE_RESULT
  │ 将有效 Lane 写入 Result SRAM，并压入深度为 1 的 Result FIFO
  ▼
EMIT_RESULT
  │ ResultBeat 完成 valid/ready 握手
  ├── 仍有 row group ─────────► INIT_ACC
  ▼
LOCAL_DONE
  │ LocalDone 完成 valid/ready 握手
  ▼
IDLE
```

该状态图描述架构顺序。v0.1 的事件模型和参考 RTL 必须采用下一节固定的 FETCH/MAC 流水；功能模型只需保持功能与事件顺序一致，不要求模拟周期。

### 9.1 参考时序

v0.1 RTL 参考实现采用：

- SRAM 同步读延迟 1 周期；
- 第一个 FETCH 用于流水填充；从第一个 MAC 周期开始，同时发出下一个 `k` 的读请求，因此流水填充后每周期完成一个 `k` 的 4-Lane MAC；
- 不重叠不同 row group；
- Result SRAM 写回和 ResultBeat 握手各占一个周期；
- ResultBeat 被阻塞时不开始下一组。

无任何 Stall 时，一个 row group 的参考周期为：

```text
1 cycle INIT_ACC
1 cycle memory pipeline fill
K_local cycles MAC
1 cycle Result SRAM write and Result FIFO push
1 cycle ResultBeat handshake

T_group = K_local + 4
```

完整本地任务的参考周期：

```text
G = ceil(M_local / 4)
T_local_no_stall = G * (K_local + 4) + 1
```

最后的 `+1` 是 LocalDone。命令握手周期不计入该式。若 ResultBeat 或 LocalDone 被反压，实际周期按阻塞周期增加。

### 9.2 单 Tile 端口与 Bank 参考组织（2026-10-28 补充）

以下给出满足 9.1 的一种可执行端口组织，作为 RTL 实现与对照模型的起点；它不增加 v0.1 的外部接口约束，也不代表已选定的 SRAM 宏或器件实测时序。

| 区域 | 参考组织/容量 | Compute 请求 | 读延迟 | 请求启动间隔 |
|---|---|---|---:|---:|
| Weight | 1 Bank，1024×32 bit，共 4096 B | 每周期一个 32-bit Word，供 4 个 Lane 使用 | 1 周期 | 1 周期 |
| Input | 1 Bank，1024×8 bit，共 1024 B | 每周期一个 INT8，广播到 4 个 Lane | 1 周期 | 1 周期 |
| Result | 4 Bank，每 Bank 64×32 bit，共 1024 B | 每 Bank 每周期最多写一个 INT32，合计最多 4 个 | 不在计算阶段读取 | 1 周期 |

每 Bank 采用一个读/写服务端口；Weight、Input、Result 的端口彼此独立。Weight 和 Input 因而可以同周期读，逻辑读取量最多为 `4+1=5 B/cycle`。Result 的四个 Bank 按 INT32 word 交错：`bank = floor(Y_addr/4) mod 4`。连续四个结果落入不同 Bank，即使 `Y_base` 只按 4 B 对齐、未按 16 B 对齐，仍可在一个周期内写完；无效 Lane 不发写请求。该组织满足 6.1 的对齐要求。

上述为内部 Compute 端口。6.2 的外部加载/读回端口仍是 32 bit，且只在 IDLE 访问。尤其不能由内部每周期写 128 bit 推断外部端口同样有该带宽；窄 Input Bank 如何串行处理外部 32-bit 写、请求接收与提交的时序，仍属于待实现的加载控制器。

精确的时钟约定如下：

- cycle 0 为已接受、已验证有效任务的 `INIT_ACC`；统计不含命令/VALIDATE、预加载或外部读回。
- cycle `c` 接受的读请求在 `c+L` 可被 MAC 消费。数据在请求接受时采样，响应按 `(group,k,region)` 匹配，不能在发请求的同一周期使用同步读结果。
- 读延迟 `L` 与启动间隔 `II` 独立；`L=3, II=1` 允许连续三周期发请求。MAC 仅在当前 `k` 的 Weight/Input 都返回后更新 accumulator，默认一次更新占一个时钟、每周期最多一次 4-Lane 更新。
- 默认提供 2 个在途 `(Weight Word, Input byte)` 操作数对槽位。请求被拒绝时保持字段，收到数据后保留到对应 MAC；槽位在 MAC 后释放，下一周期可重用。最长读延迟为 `L` 时，本参考调度需要至少 `L+1` 个槽位才能在 `II=1` 下保持持续发起。
- 最后一次 MAC 后进入写回；写请求在获准的边沿提交。全部有效 Lane 写完后，下一周期允许 ResultBeat 握手。被阻塞时保持完整结果，不预取下一组；最后一组握手后的下一周期才允许 LocalDone。

因此，4-Lane 表示峰值每周期完成 **4 次标量 MAC 更新**，并非每周期产生 4 个完整点积结果。无阻塞时每 `K_local+4` 周期交出一组最多 4 个结果；5×3 的两组 ResultBeat 分别在 cycle 6、13 握手，LocalDone 在 cycle 14 握手，共 15 周期。

逐请求入口为 [`PortScheduledCompute`](../../src/simulator/system/tile_timing.py) 和 [`TileMemoryPorts`](../../src/simulator/system/tile_memory.py)。端口组织由 `TileMemoryConfig` 显式指定并写入实验结果；`hardware.py` 中原有的 `banks_per_tile/bank_width_bytes` 是粗粒度元数据，尚不能表达三块 SRAM 的独立端口，不应被解释成三块 SRAM 共用一个 32-bit 端口。容量和地址仍沿用统一硬件配置。

该模型可将 Weight/Input 映射到共享的 1/2/4 个 Bank，或减少 Result Bank，进行地址冲突与逐 Bank round-robin 仲裁对照。共享 Weight/Input 时两者使用相同几何参数，`bank = floor(byte_addr/word_bytes) mod banks`，每 Bank 最多接受 `ports_per_bank` 个请求；每个端口同时受 `issue_interval` 约束。仲裁仅在实际接受请求时轮转，未获准请求由调用方保留。模型区分同周期竞争造成的等待和端口尚未恢复造成的等待，单位均为请求·周期，不能直接与任务周期相加。默认独立 Weight/Input 组织没有两者之间的 Bank 竞争；共享组织属于另行标记的硬件敏感性对照。

返回由预留操作数槽位接收，不提供响应反压；外部加载、NI/归约争用、复位和非法任务的硬件握手未纳入此单 Tile 入口。模型与测试只证明这套端口和计算时序的一致性；RTL、综合频率、器件时序与完整系统性能仍需独立验证。

同日后续实现见 [单 Tile RTL](../../src/rtl/nmc_tile/README.md)：固定端口基线已完成 71 个用例/77 段执行的逐周期对照，含加载、复位和非法任务处理，并通过通用 SRAM 结构检查。其结果不覆盖具体 SRAM 宏时序或多 Tile 系统性能。

逐请求模型随后已接入 Python 2×2 runner：`tile_timing=TileComputeConfig()` 令 Compute 与 NI/Mesh 同时钟推进，可显式覆盖 Bank、端口启动间隔、读延迟和操作数槽位。默认端口与公式参考的完整系统 Trace 一致；配置和接口说明见 [系统实验](../../tools/experiments/compute_timing.md#timed-tile-system)。该接入沿用功能输入写提交与功能归约，未将 RTL 加载器和归约服务时间计入完整系统。

## 10. Compute 到 NI 的 ResultBeat 接口

### 10.1 信号

```systemverilog
result_valid
result_ready

result_tid[15:0]
result_dst_tile[7:0]
result_base_index[15:0]
result_lane_mask[3:0]
result_data[4][31:0]
result_kind                 // FINAL or PSUM
result_last                 // 当前 TileTask 的最后一个 ResultBeat
```

Lane 和展开位序固定为：

```text
result_lane_mask[i]          对应 Lane i
result_data[i]               对应 global output (result_base_index + i)
flattened_data[32*i +: 32]   等于 result_data[i]
```

无效 Lane 的 `result_data` 固定输出 0，接收方仍必须以 `lane_mask` 判断有效性。

其中：

```text
result_base_index = global_row_base + 4 * group
result_last       = task_last && (group is the final row group)
```

`result_last=1` 表示这是当前 Tile 在该 transaction 中最后一个 ComputeJob 的最后一个 ResultBeat。无论 `task_last` 是 0 还是 1，每个 TileTask 都仍然产生自己的 LocalDone；`task_last` 不改变本地计算和 LocalDone 数量。

### 10.2 握手规则

一次 ResultBeat 只在以下条件同时成立时被 NI 接收：

```text
result_valid == 1 && result_ready == 1
```

当 `result_valid=1 && result_ready=0` 时，计算模块必须：

- 保持全部 ResultBeat 字段逐周期稳定；
- 保持 Result FIFO 内容不变；
- 不覆盖当前结果；
- 不产生 LocalDone；
- 不开始下一 row group；
- 不重复计算已经完成的 MAC。

NI 负责把 ResultBeat 转换为 Packet/Flit。MAC 核心不得依赖 Flit 位宽、Router 端口或路由算法。

## 11. 完成与错误语义

### 11.1 LocalDone

```systemverilog
local_done_valid
local_done_ready
local_done_tid[15:0]
local_done_status
```

LocalDone 只能在当前 TileTask 的最后一个 ResultBeat 被 NI 接受后产生。每个成功 TileTask 必须恰好产生一次 LocalDone。

LocalDone 采用独立 valid/ready 握手。`local_done_valid=1 && local_done_ready=0` 时，Tile 必须保持 `local_done_valid`、`local_done_tid` 和 `local_done_status` 逐周期稳定，并保持 `task_ready=0`。只有 LocalDone 完成握手后才能接受下一项任务。

LocalDone 表示：

> 当前 Tile 已完成本地计算，所有本地结果已经交给 NI。

它不表示：

- Packet 已到达目标 Tile；
- 所有 PSUM 已被归约；
- Host 已获得最终结果；
- 整个 transaction 已完成。

全局 `TRANSACTION_DONE` 由目标 Tile 的 NI/Reduction/Completion 单元产生，将在通信架构规范中定义。

### 11.2 状态码

| 状态 | 含义 |
|---|---|
| `OK` | TileTask 正常完成 |
| `BAD_OPCODE` | 不支持的操作 |
| `BAD_FIELD` | 已编码任务的 version、enum 或全局输出索引非法 |
| `BAD_SHAPE` | `m_local` 或 `k_local` 非法 |
| `BAD_TILE` | `tile_id` 与当前 Tile 不符，或源/目标 Tile 超出系统范围 |
| `ADDR_RANGE` | 任一访问越过本地区域 |
| `ADDR_ALIGN` | Weight 或 Result 地址不满足对齐 |
| `PROTOCOL_ERROR` | 内部握手或状态发生不可能事件 |

非法 descriptor 必须在执行前被拒绝，并产生一次相同 `tid` 的错误 LocalDone；不得产生 ResultBeat。

若一个 descriptor 同时存在多个错误，只报告以下顺序中最先匹配的状态：

```text
BAD_OPCODE
  > BAD_FIELD（version、enum）
  > BAD_SHAPE
  > BAD_TILE
  > BAD_FIELD（global output index overflow）
  > ADDR_ALIGN
  > ADDR_RANGE
```

错误任务从 VALIDATE 进入 LOCAL_DONE，不得进入 INIT_ACC。错误 LocalDone 也遵守上一节的反压稳定规则。

## 12. 行切分与 K 维切分

### 12.1 行切分

每个 Tile 保存若干完整输出行：

```text
result_kind = FINAL
```

所有 Tile 可以接收相同输入向量。每个 ResultBeat 已是最终数值，不需要跨 Tile 数值归约。

### 12.2 K 维切分

每个 Tile 保存所有输出行的一段 K：

```text
result_kind = PSUM
```

MAC 核心仍执行同一个 `local_y = local_W * local_x`。它不负责合并其他 Tile 的部分和。NI/Reduction 单元根据 `(tid, global_output_index)` 匹配和归约 PSUM。

因此，切分方式不得改变 MAC 数据通路，只改变 TileTask、数据放置和 ResultBeat 类型。

## 13. v0.1 必须通过的测试

### 13.1 Demo 0A：2×2 基础正确性

```text
W = [[1, 2],
     [3, 4]]
x = [5, 6]
y = [17, 39]
```

必须验证：

- byte address；
- row-major 输入转换为 Weight Word；
- INT8 × INT8 和 INT32 累加；
- `lane_mask=0011`；
- 一个 ResultBeat；
- 一个 LocalDone。

### 13.2 Demo 0B：5×3 Lane 和符号测试

```text
x = [2, -1, 3]

W = [[ 1,  2, -1],
     [-2,  0,  4],
     [ 3, -1,  2],
     [ 0,  5, -2],
     [-1,  2,  1]]

y = [-3, 8, 13, -11, -1]
```

必须产生：

```text
ResultBeat 0:
  base_index = global_row_base
  lane_mask  = 1111
  data       = [-3, 8, 13, -11]
  last       = 0

ResultBeat 1:
  base_index = global_row_base + 4
  lane_mask  = 0001
  data[0]    = -1
  last       = 1
```

该测试必须覆盖负数、两个 row group、Accumulator 重新清零和非整 Lane 尾部。

### 13.3 Demo 0C：结果反压

在每个 ResultBeat 首次令 `result_valid=1` 后，将 `result_ready` 连续拉低 3 周期。必须验证：

- 所有 ResultBeat 字段在 3 个周期内保持不变；
- 恢复 ready 后只完成一次握手；
- 没有丢失或重复 ResultBeat；
- 最后一个 ResultBeat 被接受前没有 LocalDone；
- LocalDone 只产生一次；
- `local_done_ready` 拉低时，LocalDone 的 tid 和 status 保持稳定。

### 13.4 Demo 0D：错误处理

至少覆盖：

- `m_local=0`；
- `k_local=0`；
- `W_base` 未对齐；
- `Y_base` 未对齐；
- 地址越界；
- `tile_id` 不匹配；
- 目标 Tile 越界；
- version、result kind 或 global output index 非法；
- 软件构造器在握手前拒绝无法由声明位宽表示的字段和非 boolean `task_last`；
- 一个 descriptor 同时含多个错误时遵守固定错误优先级。

每个非法任务必须不修改存储、不产生 ResultBeat，并只产生一次对应错误状态。

## 14. 跨模块责任

| 模块 | 必须遵守本规范的内容 |
|---|---|
| Mapper | 生成合法 TileTask、地址、全局输出索引和 FINAL/PSUM 类型 |
| Functional Model | 精确实现 byte address、权重布局、INT32 wrap 和 ResultBeat 顺序 |
| Event Model | 在不改变功能语义的前提下加入 SRAM、MAC 和反压周期 |
| Tile RTL | 实现 4-Lane 数据通路、控制状态机、握手与错误检查 |
| NI | 接收稳定的 ResultBeat，并负责 Packet 化和后续传输 |
| Verification | 使用相同任务和 Golden 检查结果、顺序、反压、完成与错误 |

任何模块都不得私自增加另一套地址、结果格式或完成定义。

## 15. 冻结条件

v0.1 已依据以下证据进入 `FROZEN`：

1. 第 3 至 12 节完成逐字段歧义审计，结果记录于 `docs/spec/design_decisions_v0.1.md#compute-audit`；
2. Demo 0A 和 0B 的手算结果、Weight Word、ResultBeat 字段全部确认；
3. 功能模型完成 Demo 0A 至 0D，并覆盖新增的字段、错误优先级和 LocalDone 反压规则；
4. `docs/validation.md#trace` 给出无阻塞 5×3 Trace；
5. 冻结决定记录于 `docs/spec/design_decisions_v0.1.md#compute`。

冻结后，改变数值类型、Lane组织、地址语义、权重布局、ResultBeat或完成条件都必须提升规范版本，不能只在单个模块中修改。

## 16. 下一份规范

`communication_architecture_v0.1` 将在本规范的 ResultBeat 边界之后定义：

- NI ingress/egress；
- ReductionConfig；
- Packet 和 Flit 格式；
- SEND/RECV；
- 端点归约；
- Router 端口、XY routing、FIFO、仲裁和反压；
- `TRANSACTION_DONE` 与 `ERROR` Packet。

通信规范不得修改本文已经冻结的计算结果和握手语义。
