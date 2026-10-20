# 验证指南与运行记录

[Demo 0 入门](#demo0) · [Demo 0B 周期 Trace](#trace) · [Comm 0A–0C](#comm) · [NMC Demo 1](#demo1) · [NMC Demo 2](#demo2)

<a id="demo0"></a>

## Demo 0：环境、手算与功能验证

这份练习验证 `docs/spec/compute_architecture_v0.1.md`，不评估性能，也不代表 NoC 或 RTL 已经完成。

### 第一次运行前先回答

不要先看测试断言。请在纸上写出：

1. Demo 0A 的四次乘法分别是什么，为什么输出是 `17` 和 `39`；
2. 两行输出为什么对应 `lane_mask=0011`；
3. `W[0][0]`、`W[1][0]`、`W[0][1]`、`W[1][1]` 分别落在哪个 Weight Word 的哪个 byte；
4. 为什么 Demo 0B 要产生两个 ResultBeat；
5. 为什么最后一个 ResultBeat 的 `base_index` 是 `global_row_base+4`；
6. 当 `result_valid=1, result_ready=0` 时，哪些字段必须保持不变；
7. `LocalDone` 为什么不能和被阻塞的最后一个 ResultBeat 同时完成。

能够不看规范回答这些问题，才说明测试不只是由工具代跑。

### 建立环境

在仓库根目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

确认当前解释器来自仓库：

```bash
which python
python --version
```

`which python` 应指向当前项目根目录下的 `.venv/bin/python`。

### 分阶段运行

先只运行基础 GEMV：

```bash
python -m pytest -q src/tests/test_demo0_functional.py::test_demo0a_2x2_basic_correctness
```

再运行 Lane 和符号测试：

```bash
python -m pytest -q src/tests/test_demo0_functional.py::test_demo0b_signed_values_two_groups_and_tail_mask
```

然后验证反压和错误：

```bash
python -m pytest -q src/tests/test_demo0_functional.py::test_demo0c_backpressure_holds_payload_and_delays_done
python -m pytest -q src/tests/test_demo0_functional.py -k demo0d
```

最后运行整个文件：

```bash
python -m pytest -q src/tests/test_demo0_functional.py
```

### 你应该逐行跟踪的 Demo 0A

权重布局不是普通的 `W[行][列]` 顺序。对于第一个 row group：

```text
W_addr(group=0, k=0) = 0x0000
byte lane 0 = W[0][0] = 1
byte lane 1 = W[1][0] = 3
byte lane 2 = 0（无效 Lane）
byte lane 3 = 0（无效 Lane）

W_addr(group=0, k=1) = 0x0004
byte lane 0 = W[0][1] = 2
byte lane 1 = W[1][1] = 4
```

计算过程：

```text
k=0, x=5: acc = [1*5, 3*5, 0, 0] = [5, 15, 0, 0]
k=1, x=6: acc = [5+2*6, 15+4*6, 0, 0]
             = [17, 39, 0, 0]
```

如果你原先把权重按整行连续存储，测试恰好仍可能得到正确数学结果，但将无法证明 Mapper、功能模型和 RTL 使用同一地址布局。因此测试必须同时检查数值和 ResultBeat 字段。

### 失败时怎么读

- `ModuleNotFoundError: simulator`：你可能不在仓库根目录运行。
- 输出数值对但 mask 错：row group 或尾部 Lane 规则错误。
- 负数结果错误：INT8 byte 没有按二进制补码解码。
- Demo 0C 失败：先画出每个周期的 `valid/ready/payload/done`，不要直接修改断言。
- Demo 0D 修改了 Result SRAM：descriptor 检查发生得太晚。

### 验收证据

本阶段冻结只需要保存：

1. 你手写的 Demo 0A/0B 计算和 Weight Word 布局；
2. `pytest` 的通过输出；
3. Demo 0C 的逐周期表；
4. 对每个失败用例说明对应规范条款。

下一阶段才开始事件模型、RTL 和 NoC，不要用当前测试宣称性能或硬件已经验证。

<a id="trace"></a>

## Demo 0B：5×3 GEMV 周期 Trace

状态：`FROZEN v0.1 EVIDENCE`
对应规范：`docs/spec/compute_architecture_v0.1.md`
用途：作为 Compute Architecture v0.1 的地址、流水和接口冻结证据。

### 1. 固定配置

```text
M_local = 5
K_local = 3
LANES = 4
group_count = ceil(5/4) = 2

W_base = 0x0000
X_base = 0x1000
Y_base = 0x2000
global_row_base = 8

result_ready = 1
local_done_ready = 1
task_last = true
```

本 Trace 不计 TileTask 握手周期。SRAM 采用一周期同步读；流水填充后，每周期完成一个 `k` 的 4-Lane MAC。当前 row group 的 ResultBeat 被接收前，不启动下一组。

```text
T_local_no_stall
  = ceil(M_local/4) * (K_local + 4) + 1
  = 2 * (3 + 4) + 1
  = 15 cycles
```

### 2. 输入

```text
x = [2, -1, 3]

W = [[ 1,  2, -1],
     [-2,  0,  4],
     [ 3, -1,  2],
     [ 0,  5, -2],
     [-1,  2,  1]]
```

权重采用 `(group, k, lane)` 布局：

| 地址 | group | k | Lane 0..3 signed INT8 | 32-bit Weight Word |
|---:|---:|---:|---|---:|
| `0x0000` | 0 | 0 | `[1,-2,3,0]` | `0x0003FE01` |
| `0x0004` | 0 | 1 | `[2,0,-1,5]` | `0x05FF0002` |
| `0x0008` | 0 | 2 | `[-1,4,2,-2]` | `0xFE0204FF` |
| `0x000C` | 1 | 0 | `[-1,0,0,0]` | `0x000000FF` |
| `0x0010` | 1 | 1 | `[2,0,0,0]` | `0x00000002` |
| `0x0014` | 1 | 2 | `[1,0,0,0]` | `0x00000001` |

### 3. 无阻塞周期 Trace

| Cycle | 状态/操作 | group,k | 读请求或返回数据 | acc before | product | acc after | 外部事件 |
|---:|---|---|---|---|---|---|---|
| 0 | `INIT_ACC` | 0,— | — | — | — | `[0,0,0,0]` | — |
| 1 | `FETCH k0` | 0,0 | 请求 `W=0x0000, X=0x1000` | `[0,0,0,0]` | — | 不变 | — |
| 2 | `MAC k0 + FETCH k1` | 0,0 | 返回 `w=[1,-2,3,0], x=2`；请求 `0x0004,0x1001` | `[0,0,0,0]` | `[2,-4,6,0]` | `[2,-4,6,0]` | — |
| 3 | `MAC k1 + FETCH k2` | 0,1 | 返回 `w=[2,0,-1,5], x=-1`；请求 `0x0008,0x1002` | `[2,-4,6,0]` | `[-2,0,1,-5]` | `[0,-4,7,-5]` | — |
| 4 | `MAC k2` | 0,2 | 返回 `w=[-1,4,2,-2], x=3` | `[0,-4,7,-5]` | `[-3,12,6,-6]` | `[-3,8,13,-11]` | — |
| 5 | `WRITE_RESULT` | 0,— | 写 `Y[0:3]`，压入 FIFO | `[-3,8,13,-11]` | — | 不变 | — |
| 6 | `EMIT_RESULT` | 0,— | — | — | — | — | ResultBeat 0 握手 |
| 7 | `INIT_ACC` | 1,— | — | — | — | `[0,0,0,0]` | — |
| 8 | `FETCH k0` | 1,0 | 请求 `W=0x000C, X=0x1000` | `[0,0,0,0]` | — | 不变 | — |
| 9 | `MAC k0 + FETCH k1` | 1,0 | 返回 `w=[-1,0,0,0], x=2`；请求 `0x0010,0x1001` | `[0,0,0,0]` | `[-2,0,0,0]` | `[-2,0,0,0]` | — |
| 10 | `MAC k1 + FETCH k2` | 1,1 | 返回 `w=[2,0,0,0], x=-1`；请求 `0x0014,0x1002` | `[-2,0,0,0]` | `[-2,0,0,0]` | `[-4,0,0,0]` | — |
| 11 | `MAC k2` | 1,2 | 返回 `w=[1,0,0,0], x=3` | `[-4,0,0,0]` | `[3,0,0,0]` | `[-1,0,0,0]` | — |
| 12 | `WRITE_RESULT` | 1,— | 写 `Y[4]`，压入 FIFO | `[-1,0,0,0]` | — | 不变 | — |
| 13 | `EMIT_RESULT` | 1,— | — | — | — | — | ResultBeat 1 握手 |
| 14 | `LOCAL_DONE` | — | — | — | — | — | LocalDone 握手，返回 IDLE |

ResultBeat 0：

```text
base_index = 8
lane_mask  = 1111
data       = [-3, 8, 13, -11]
last       = 0
```

ResultBeat 1：

```text
base_index = 12
lane_mask  = 0001
data       = [-1, 0, 0, 0]
last       = 1
```

LocalDone：

```text
tid    = TileTask.tid
status = OK
```

### 4. 后续实现检查

#### Mapper / ISA

- [ ] 能根据 `M_local=5, K_local=3` 生成 6 个 Weight Word；
- [ ] 使用 byte address，并生成表中的 W/X/Y 地址；
- [ ] `global_row_base=8` 时生成 `base_index=8,12`；
- [ ] 行切分使用 `FINAL`，K 维切分使用 `PSUM`；
- [ ] 不把归约配置塞入 Compute TileTask。

#### Compute RTL

- [ ] 4 个 Lane 共享同一个 `x`，每个 Lane 读取不同权重；
- [ ] group 1 前清零全部 accumulator；
- [ ] 无效 Lane 不写 Result SRAM；
- [ ] 同步 SRAM 一周期延迟和 FETCH/MAC 流水与本 Trace 一致；
- [ ] Result FIFO 满时不启动下一个 row group；
- [ ] INT32 加法显式二进制补码回绕。

#### NI / Verification

- [ ] `valid=1 && ready=0` 时 ResultBeat 全字段稳定；
- [ ] 最后一个 ResultBeat 被接收前不产生 LocalDone；
- [ ] LocalDone 不等于全局 transaction 完成；
- [ ] NI 只从 ResultBeat 边界开始定义 Packet/Flit；
- [ ] 同一组任务同时驱动功能模型、RTL testbench 和后续 NI 测试。

### 5. 冻结证据

1. 全量运行 `python -m pytest -q src/tests/test_demo0_functional.py`，预期 17 项通过；
2. 字段和时序歧义审计见 `docs/spec/design_decisions_v0.1.md#compute-audit`；
3. 冻结决定见 `docs/spec/design_decisions_v0.1.md#compute`；
4. 第 4 节保留为 Mapper、RTL 和 NI 后续实现时的自检清单；
5. 下一步开始 `communication_architecture_v0.1.md`。

<a id="comm"></a>

## 通信 v0.1：测试与实际记录

日期：2026-10-06
覆盖规范：`docs/spec/communication_architecture_v0.1.md`

### 测试范围

| 测试 | 验证内容 |
|---|---|
| Packet pack/unpack | RESULT、MEM_WRITE、MEM_ACK、TRANSACTION_DONE、ERROR 的字段边界、保留位和 Flit 序列 |
| Comm 0A | ResultBeat 原子转换为 HEAD+TAIL、两个空槽规则、目标 NI 重建 |
| Comm 0B | Tile 0→1→3 的确定性 XY 路径和 4 flit-hops |
| Comm 0C-A | 目的端连续阻塞 3 周期时，visible Flit 保持稳定且恢复后无丢失/重复 |
| Comm 0C-B | 持续阻塞至有限 FIFO 填满，验证反压最终传播到源 NI |

### Comm 0C 规范澄清

目的端只阻塞 3 周期，足以检查 valid/ready 稳定性，但在初始为空、具有多级 4-Flit FIFO 的网络中，不保证反压已经传播到源 NI。因此测试分成两个阶段：

1. 固定 3 周期，验证稳定性；
2. 持续阻塞直至源 NI 出现 injection stall，验证有限缓冲和反压传播。

这种拆分不会削弱原要求，反而避免把网络传播延迟错误地写死为 3 周期。

### 运行命令

```bash
cd "/Users/Admin/Documents/computer architecture/computer architecture project"
source .venv/bin/activate
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py
```

也可以分别运行：

```bash
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py -k packet
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py -k comm0a
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py -k comm0b
python -m pytest -q -p no:cacheprovider src/tests/test_communication_v0_1.py -k comm0c
```

### 2026-10-06 实际结果

定向通信测试：

```text
..........                                                               [100%]
10 passed in 0.04s
```

包含原 Compute Demo 0 测试的完整回归：

```text
...........................                                              [100%]
27 passed in 0.07s
```

Comm 0B：

```text
path             = Tile 0 -> Tile 1 -> Tile 3
received_packets = 1
flit_hops        = 4
```

Comm 0C 的一次确定性运行：

```text
Phase A:
  first_local_valid_cycle = 3
  blocked_cycles          = 3
  received_packets        = 1
  flit_hops               = 4

Phase B:
  source_stall_cycle      = 13
  injected_packets        = 8
  source_stall_cycles     = 3
  fifo_full_cycles        = 22
  max_fifo_occupancy      = 4
  received_packets        = 8
  flit_hops               = 32
```

Phase B 中注入和接收 Packet 数量相同，证明持续阻塞和恢复期间没有丢失或重复。完整回归未破坏原有 17 项 Compute v0.1 测试。命令使用 `-p no:cacheprovider`，因此不需要在项目目录写入 `.pytest_cache`。

<a id="demo1"></a>

## NMC Demo 1：两个 Tile 行切分 GEMV

日期：2026-10-08。对应 Communication v0.1 的 Demo 1（GATHER_FINAL）。

这次结果由实际输入 SRAM、Weight SRAM 和已有 `Demo0Tile.execute()` 产生，经 ResultBeat、NI、Mesh 到 Root。NumPy 只在 Host/测试侧校验，Root 不读取 Golden。

### 固定例子与分工

```text
W = [[ 1,  2,  3,  4],
     [-1,  0,  2, -2],
     [ 3, -1,  0,  1],
     [ 2,  2, -1,  0]]
x = [2, -1, 3, 1]
y = [13, 2, 8, -1]

Tile 0: Host，发送输入 MEM_WRITE，接收 ACK 和 TRANSACTION_DONE
Tile 1: 驻留 W 的第 0–1 行，产生 FINAL [13, 2]
Tile 2: 驻留 W 的第 2–3 行，产生 FINAL [8, -1]
Tile 3: Root，按 global output index 写入最终 Result SRAM
```

四个 INT8 输入编码成 little-endian word `0x0103FF02`，写到两个计算 Tile 的 `0x1000`，`wstrb=1111`。权重预加载采用 Compute v0.1 的 `(group,k,lane)` 布局，例如 Tile 1 第一个 Weight Word 为 `0x0000FF01`。

```text
Host Tile 0
  ├─ MEM_WRITE -> Tile 1 Input SRAM -> MEM_ACK ─┐
  └─ MEM_WRITE -> Tile 2 Input SRAM -> MEM_ACK ─┤
                                              ▼
                                      收齐 ACK 后启动任务
                                        │            │
                                  Tile 1 GEMV   Tile 2 GEMV
                                        │ FINAL      │ FINAL
                                        └─────┬──────┘
                                              ▼
                                      Tile 3 GATHER_FINAL
                                              │
                                    TRANSACTION_DONE Packet
                                              ▼
                                          Host Tile 0
```

### 手动运行

在仓库根目录、已激活 `.venv` 的终端执行：

```bash
PYTHONPATH=src python -m simulator.system.demo1 --trace dump/demo1_trace.json
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo1.py
python -m pytest -q -p no:cacheprovider
```

代码入口：`src/simulator/system/demo1.py`。验收测试：`src/tests/test_nmc_demo1.py`。运行输出放 `dump/`，不新建另一套目录。

### 默认运行应该看到的事件

| 通信调度 Cycle | 事件 | 证明什么 |
|---:|---|---|
| 0 | 权重驻留，Host 发送 Tile 1 输入 | 本地权重与远端输入分发分开 |
| 4 | Tile 1 提交输入写入 | SRAM 数据来自实际接收的 Packet |
| 7 | Host 收到 Tile 1 ACK，发送 Tile 2 输入 | ACK 标识提交完成 |
| 11 | Tile 2 提交输入写入 | 第二份复制也已落到 SRAM |
| 14 | Host 收齐 ACK，两个任务执行，ResultBeat 被 NI 接受 | 未收到输入 ACK 前不能启动计算 |
| 15 | 两个来源分别产生 LocalDone | 只表示本地结果已交给 NI |
| 18 | Root 收到前两个 FINAL 元素 | 尚未满足全局完成 |
| 20 | Root 收齐四个输出与两个来源 last，创建 DONE | `source_mask=0110`，`output_count=4` |
| 24 | Host 收到 TRANSACTION_DONE | 一次全局完成通知 |

预期摘要：`y=[13,2,8,-1]`、`input_acks=2`、`host_done_count=1`、`flit_hops=12`、`check=PASS`。

### 你需要自己核对的五个问题

1. Tile 1 为什么使用 `global_row_base=0`，Tile 2 为什么使用 2？
2. 为什么每个 Tile 的 `lane_mask=0011`，而不是 `1111`？
3. 两个 Tile 的 LocalDone 已出现，但 Root 尚未收齐结果时，能否 DONE？
4. 行切分为什么只收集 FINAL，不进行跨 Tile INT32 加法？
5. 如果 Root 长时间拒绝接收，反压如何传回 NI，为什么下一个 ResultBeat 不能被接受？

修改 `DEMO_W/DEMO_X` 前先手算答案，再运行入口和测试。测试同时包含 signed 数据、9×5 非整 Lane/输入尾部、非零 global base、65×5 Root 阻塞、写入错误、重复索引和来源 last 缺失。

### 证据范围

这是功能闭环与接口验证，不是性能评测或 RTL 结果：

- GEMV 使用现有功能模型即时计算，Trace 的 TASK_START 与 ResultBeat 可能具有相同通信 Cycle；
- 通信继续按 Mesh 周期前进，LocalDone 在所有 ResultBeat 被 NI 接受后出现；
- 权重预加载不计时，Host 配置和任务下发使用 Python 调度；本版不是完整二进制 ISA/Runtime；
- Host 采用逐个写入等待 ACK 的保守加载方式，不能代表优化广播性能；
- Host 待发送控制事件队列为软件队列；Router 与 NI 注入 FIFO 保持既有有限深度；接收侧使用既有 Mesh Packet 重组模型，完整 NI ejection FIFO/事务生命周期尚待实现；
- 协议异常测试终止 Python transaction 并验证不写错结果；完整 ERROR Packet 传播/复位恢复留待后续实现。

下一步 Demo 2 复用这条链，改成 K 维切分和 PSUM Endpoint 归约。之后加入 Bank/MAC 时序，才能对比两种切分的任务完成时间。

### 2026-10-08 实际运行记录

```text
Demo 1 定向测试：18 passed in 0.23s
完整回归：       45 passed in 0.23s
默认运行入口：   check=PASS，y=[13,2,8,-1]
目录检查：       PASS（46 个本地链接，2 个工作区）
```

默认入口的完整 JSON 事件记录已保存到 `dump/demo1_trace.json`。该文件可由上述命令重新生成，按仓库规则不提交 Git。

<a id="demo2"></a>

## NMC Demo 2：K 切分与目标端 PSUM 归约

日期：2026-10-08。对应 Communication v0.1 的 REDUCE_PSUM。

复用 Demo 1 的 Host、存储写入/ACK、Compute、NI 和 Mesh；只改变 W/x 的切分、RESULT kind 和 Root 操作。NumPy 仍只用于验证，不参与生成 Root 数值。

### 与 Demo 1 使用同一个 GEMV

```text
W = [[ 1,  2,  3,  4],
     [-1,  0,  2, -2],
     [ 3, -1,  0,  1],
     [ 2,  2, -1,  0]]
x = [2, -1, 3, 1]

Tile 1: W[:,0:2] 和 x[0:2]  -> PSUM [ 0, -2, 7,  2]
Tile 2: W[:,2:4] 和 x[2:4]  -> PSUM [13,  4, 1, -3]
Root 3: 两份 PSUM 逐元素相加 -> y    [13,  2, 8, -1]
```

例如输出 0：Tile 1 算 `1*2 + 2*(-1) = 0`，Tile 2 算 `3*3 + 4*1 = 13`，Root 算 `0+13=13`。

| 字段/动作 | Demo 1 行切分 | Demo 2 K 切分 |
|---|---|---|
| Tile 1 本地形状 | M=2，K=4 | M=4，K=2 |
| Tile 2 本地形状 | M=2，K=4 | M=4，K=2 |
| 输入分发 | 每个 Tile 得到完整 x | 每个 Tile 得到自己的 x 段 |
| global_row_base | Tile 1=0，Tile 2=2 | 两者都为 0 |
| lane_mask | 两者都为 0011 | 两者都为 1111 |
| result_kind | FINAL | PSUM |
| Root 处理 | 写入不同输出索引 | 同一输出索引按来源归约 |

Tile 1 输入 word 为 `0x0000FF02`，Tile 2 为 `0x00000103`，两者都写入自己的 `0x1000`，`wstrb=0011`。只改变合法 descriptor 和内存内容，MAC 数据通路与 Packet 布局保持原有定义。

### Root 如何防止错误完成

每个输出索引独立保存 `acc32`、`received_source_mask` 和 complete 状态。配置期望 `source_mask=0110`（Tile 1 和 Tile 2）：

```text
收到 Tile 1 的 index=0: acc=0，mask=0010，complete=false
收到 Tile 2 的 index=0: acc=13，mask=0110，complete=true
```

第一次收到某来源，只更新归约 Buffer；收齐全部来源后才写 Root Result SRAM。相同 `(tid,index,src_tile)` 再到达必须产生 DUPLICATE_RESULT，不能再次累加。

全部输出 complete，且全部 source last 都已收到后，才产生一次 TRANSACTION_DONE。仅收到两份 Packet、或仅看到 LocalDone，都不足以完成。

代码先检查整个 RESULT Packet 的全部有效 Lane，再修改任何 accumulator/mask/SRAM。如果一个包的 Lane 0 合法、Lane 1 重复或越界，Lane 0 也不会部分提交。错误后终止该 Python transaction，不再产生新的 DONE。

### 手动运行与观察

```bash
# 已在仓库根目录且激活 .venv
PYTHONPATH=src python -m simulator.system.demo2 --trace dump/demo2_trace.json
python -m pytest -q -p no:cacheprovider src/tests/test_nmc_demo2.py
python -m pytest -q -p no:cacheprovider

# 把 4 维 K 改成 1+3 切分；先自己算两份 PSUM，再核对 Trace
PYTHONPATH=src python -m simulator.system.demo2 --k-split 1 --trace dump/demo2_k1_trace.json
```

代码入口为 `src/simulator/system/demo2.py`，共享调度链在 `demo1.py`；测试在 `src/tests/test_nmc_demo2.py`。Trace 沿用 `dump/`，重点看 TASK_START、RESULTBEAT_ACCEPT、PSUM_REDUCE 和 HOST_TRANSACTION_DONE。

默认 Trace 中：

| 通信调度 Cycle | Root/Host 事件 |
|---:|---|
| 14 | 收齐输入 ACK，两个 Tile 执行本地 GEMV，交出 PSUM |
| 15 | 两个来源报告 LocalDone |
| 18 | Root 收到 Tile 1，四个输出 mask=0010，最终结果数量=0 |
| 20 | Root 收到 Tile 2，四个输出 mask=0110，写入四个最终结果并创建 DONE |
| 24 | Host 收到 DONE |

预期结果为 `y=[13,2,8,-1]`、`psum_elements=8`、`input_acks=2`、`host_done_count=1`、`check=PASS`。Cycle 仍是通信调度标记；Compute 和归约均为功能操作，不能作为完整 GEMV 或归约流水性能。

### 已覆盖的验证与边界

- 默认 GEMV、单输出、Host 作为 Root，以及 9×5 signed 数据；
- K=5 的 1+4、2+3、3+2、4+1 切分，输入尾部 byte strobe、输出尾部 Lane 和非零全局索引；
- 33×5 Root 持续阻塞，有限 FIFO 填满后出现 ResultBeat Stall，恢复后所有 PSUM 恰好归约一次；
- 逐索引 source mask、缺少来源或 source last 时不得 DONE；
- 重复来源、FINAL/PSUM 混淆、tid/目的地/来源不匹配、越界和包级原子拒绝；
- 三来源的六种到达顺序、INT32 正溢出/负溢出；该部分使用合法合成 PSUM 单元测试，小型 INT8 GEMV 本身达不到该溢出范围；
- 完成后不得再返回第二次 DONE，永久阻塞通过超时报告失败。

完整 NI ejection FIFO、ERROR Packet 传播、复位恢复、并发 transaction 和 RTL 校准仍未实现。本阶段不因此冻结 Communication v0.1。

### 一个实际的通信取舍

使用同一个 9×5 GEMV、两个计算 Tile 和默认 Root：

| 指标 | 行切分 | K 切分 |
|---|---:|---:|
| 输入 MEM_WRITE/ACK 数 | 4 | 2 |
| RESULT Packet 数 | 3 | 6 |
| 有效输出/PSUM 元素数 | 9 | 18 |
| 总 flit-hops（含输入、ACK、RESULT、DONE） | 20 | 20 |

这里 K 切分减少了输入写请求，却增加了结果包；总代价需要看具体 shape 和打包。默认 4×4 两种切分都只生成两个 RESULT Packet，不能仅凭 PSUM 元素多就断言 Flit 多。

这为后续切分选择提供可观测证据，但尚不能据通信调度 Cycle 宣称 K 切分更快。下一步应补齐 Bank/MAC/归约时序，让计算时间与通信反压共同决定完成时间。

### 2026-10-08 Demo 2 实际运行记录

```text
Demo 2 定向测试：34 passed in 0.14s
完整回归：       79 passed in 0.15s
2+2 与 1+3 入口：check=PASS，最终 y 均为 [13,2,8,-1]
目录检查：       PASS（49 个本地链接，2 个工作区）
```

两份实际 Trace 保存于 `dump/demo2_trace.json` 和 `dump/demo2_k1_trace.json`。它们可重新生成，按仓库规则不提交 Git。1+3 切分对应部分和 `[2,-2,6,4]` 与 `[11,4,2,-5]`，可先手算再对照。

<a id="mapper"></a>

## Mapper v0.1：Demo 1/2 计划生成与对照

日期：2026-10-20。入口为 `src/compiler/auto_mapper_v0_1.py`，规则与运行方法见 [Mapper 说明](../src/compiler/README.md)。

- ROW 与 K 默认沿用现有 Demo 的连续、近似均分；K 模式接受合法的显式切分点。
- auto 只在默认 ROW/K 候选间按合法性选择，ROW 合法则优先；这不是性能最优声明。
- 计划包括 TileTask、权重预加载镜像、输入 MEM_WRITE、Root 配置和期望 ACK。
- 保留现有“权重驻留、输入经 NoC 写入并等待 ACK”的边界，不把权重加载计为已经模拟的传输。
- Demo CLI 暂未直接消费 JSON 计划；现阶段通过共享模型及对照测试证明字段和结果一致。

```bash
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --demo --strategy auto -o dump/mapper_plan.json
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --demo --strategy k --k-split 1
PYTHONPATH=src python -m compiler.auto_mapper_v0_1 --self-test
python -m pytest -q -p no:cacheprovider src/tests/test_mapper_v0_1.py
python -m pytest -q -p no:cacheprovider
python tools/check_layout.py --tracked
```

内置自检覆盖 28 个场景。pytest 对照使用默认 GEMV、9×5 非整 Lane、非均匀 K 切分、非零全局索引、Host 兼任 Root 和不同 worker 顺序；通过实际 Tile/Root 功能对象核对结果，并与现有 Demo 的任务、权重和 ACK 集合一致。容量回退包含 9×600 的 Lane 填充导致 ROW 不可行，以及 K=1500/2048 的跨 Tile 切分。

错误用例在计划生成时拒绝重复/非法 Tile、Root 与 worker 冲突、字段溢出、非 INT8 数据、维度不匹配和超过本版两任务容量的请求。JSON 命令行同时覆盖输入文件、输出文件和失败退出码。

### 2026-10-20 实际运行记录

```text
完整 pytest 回归：105 passed
其中 Mapper 回归：26 项
Mapper 内置自检：28 个场景全部通过
```

Mapper 只生成计划，当前对照测试不构成“已集成自动执行入口”或完整 GEMV 性能结果。
