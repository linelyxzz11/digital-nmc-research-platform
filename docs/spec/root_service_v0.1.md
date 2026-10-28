# Finite Root Service v0.1

状态：`IMPLEMENTATION DRAFT`，2026-10-28。实现入口为 [timed_endpoint.py](../../src/simulator/system/timed_endpoint.py)，配置为 [RootTiming](../../src/simulator/system/root_config.py)。沿用 [通信规范](communication_architecture_v0.1.md) 的 Packet、来源 mask、INT32 回绕、包级错误检查和全局完成条件；这是 Python 逐周期模型，Root RTL 与具体 SRAM/加法器时序尚未校准。

## 资源与默认服务

| 参数/资源 | 默认值 | 计数或组织 |
|---|---:|---|
| `rx_queue_packets` | 2 | 完整排队 RESULT + 已接受包头的槽位预留 |
| 活动 Packet 寄存器 | 1 | 从 RX 队列取出、正在服务的一个 Packet，独立于上述槽位 |
| `reduction_entries` | 256 | INT32 部分和条目，另有逐输出的来源 mask/有效状态 |
| `done_queue_packets` | 1 | 保持完整 Done，直到 Root NI 接收 |
| `lanes_per_cycle` | 4 | 每服务组最多处理 4 个 INT32，允许 1–4 的敏感性配置 |
| `read_cycles` | 1 | 读部分和及来源 mask |
| `add_cycles` | 1 | 对每个有效 Lane 做 signed INT32 回绕加法 |
| `update_cycles` | 1 | 提交部分和及来源 mask |
| `write_cycles` | 1 | 将已完整的输出写入 Root Result SRAM |

默认服务组相当于四个 INT32 加法通道和可处理四个索引的部分和读写端口。部分和存储及 mask 是端点的独立资源预算，不能把先前单 Tile RTL 的 6 KiB 存储统计直接当成包含本端点的总预算。结果写入复用架构中的 Root Result 区域，按 `4*(global_index-output_base)` 寻址；每个 Packet 的最多四个有效索引位于同一个连续四索引范围，可并行写入四个 Result Bank。面积、功耗和 Fmax 尚无实现证据。

当前一项 transaction 占用一个 Root。`values` 是 Result SRAM 的调试镜像，`received_source_masks` 和接收预留 mask 均限于已校验的 `output_count` 个索引；历史 Trace 与 Mesh 的 `received_*` 列表是观测记录，不参与缓存容量或服务决策。Done 一次 transaction 只产生一项，因此增大 Done 队列深度不会凭空提升本版吞吐。

## 有限接收与反压

Root 在 Local 输出接受 RESULT HEAD 前检查 `queued_packets + reserved_head < rx_queue_packets`。HEAD 实际握手后预留一个完整包槽，TAIL 使用同一预留槽完成重组，不能再次索要槽位；否则深度为 1 时会自行堵死。一个 Root Local 接口每周期至多接收一个 Flit，当前 RESULT 固定为 HEAD+TAIL。

接收槽满时仅阻塞新的 RESULT HEAD，让反压通过 Router FIFO、NI 注入 FIFO 传回 Compute 的 ResultBeat。已经预留的 TAIL 可以继续；LOAD 控制包和 Host 兼 Root 时的 Done 走各自协议通路。调度使用周期开始时的可用槽信息，同周期服务取出的槽在下一周期才用于新的包头接收。

完整 Packet 到达后先检查全部有效 Lane、tid、kind、来源、范围、重复和 source-last。通过后才更新接收预留 mask 并排队；数值计算不在 `accept()` 中执行。预留 mask 也覆盖队列与活动 Packet，防止尚未服务的重复包漏检。错误不会部分提交该包的其他 Lane；Python 模型终止 transaction，后续不得产生 Done。网络 ERROR Packet 的完整恢复流程尚未实现。

## 部分和容量与前向进展

K 切分要求 `reduction_entries >= output_count`，不足时在执行前拒绝。此项是当前 FIFO、单 VC、无输出索引重排/credit 协议的明确限制：某来源可能先发来大量新索引，若仅把小部分和缓冲“写满即停”，其后续包头会挡住另一个来源的匹配部分和，形成端点死锁。较小缓冲需要独立的调度/重排/credit 或溢写机制，不能用隐藏的 Python 字典扩容代替。

在合法配置下，满缓冲仍允许已有索引的匹配来源进入服务；最终结果写入后释放该部分和条目。测试包含不均衡 K=1/63 的 32×64 任务：先到的来源填满 32 项缓冲，随后匹配来源成功排空并完成。FINAL 收集不使用部分和条目，不受此项 PSUM 容量限制。

## 服务状态与时钟

一个活动 Packet 不与下一 Packet 的数值服务重叠：

```text
DISPATCH (1 cycle)
  FINAL: WRITE_RESULT
  PSUM:  READ → ADD → UPDATE → [WRITE_RESULT when source mask is complete]
  repeat for each service group
PACKET_DONE (1 cycle)
  update serviced source-last mask
  if all outputs are written and all sources are last:
CREATE_DONE (1 cycle) → held Done FIFO → ready/valid handshake
```

PSUM 首个来源也走通用 READ/ADD/UPDATE，初值为零，不另设首来源旁路。只有最终 WRITE_RESULT 结束后才修改 Result SRAM 和完成输出计数。接收预留 mask 与已服务 mask 分离；到达最后一个包不等于数值服务已完成。

若 Packet 有 n 个有效 Lane，`C=ceil(n/lanes_per_cycle)`，且各组均是首次或最终来源，则不含排队和 Done 的服务周期为：

```text
FINAL Packet       = 2 + C*W
PSUM first source  = 2 + C*(R+A+U)
PSUM final source  = 2 + C*(R+A+U+W)
```

混合完成状态时，仅对有最终输出的服务组追加 W。Done 创建在最后 Packet 提交后单独占一个周期；它在下一次 `step(done_ready=True)` 才出队。输出阻塞时 payload 稳定，不丢失也不重复。系统把 Done 直接从该有限队列交给 NI，不再经过软件 `pending` 队列。

手算：两个来源、四个输出预先排队，R=A=U=W=1，四通道：

| 周期 | 操作 |
|---|---|
| 0 | 调度来源 1 |
| 1/2/3 | 读 / 加 / 更新部分和 |
| 4 | 提交来源 1 Packet |
| 5 | 调度来源 2 |
| 6/7/8 | 读 / 加 / 更新完整部分和 |
| 9 | 写四个最终结果 |
| 10 | 提交来源 2 Packet |
| 11 | 创建 Done，进入输出槽 |
| 12 | Done 握手，共 13 周期 |

同一例子两通道为 20 周期，单通道为 34 周期；数据为 `[5,3,-3,-11]`，均有独立手算断言。Root 在系统创建时开始计时，故 `ROOT_CYCLE.local_cycle == network_cycle`；Compute 的局部周期仍从各自 TASK_START 起算。

## API、CLI 与观测

`run_plan`、`run_demo1`、`run_demo2` 增加 `root_timing=RootTiming()`，按计划中的 GATHER_FINAL/REDUCE_PSUM 选择 TimedRoot；原 `endpoint_factory` 继续用于功能 Root 入口。`capture_root_trace=True` 记录每个 Root 周期的状态、源包、索引、读取/更新值、队列占用及写回。`root_done_ready` 是测试输出反压的可选回调，默认只按 NI 实际空位接收 Done。

```bash
PYTHONPATH=src python -m simulator.system.demo2 --compute-model tile \
  --root-model timed --root-config ini/root_service/baseline.json \
  --root-trace --trace dump/root_k.json
python -m pytest -q -p no:cacheprovider src/tests/test_timed_root.py
python tools/experiments/finite_root_system.py --output-dir dump/finite_root_system_20261028
```

配置为平坦 JSON，可只写覆盖字段；`baseline.json`、`scalar.json`、`queue1.json`、`slow_write.json` 位于 `ini/root_service/`。计数区分实际 `rx_blocked_flit_cycles`、仅表示占用的 `rx_full_cycles`、各阶段服务周期与 Done 阻塞；队列有满/阻塞周期并不必然增加任务完成时间。`service_cycles` 不含 IDLE 和 Done 等待，且与 Compute/NoC 重叠，不能简单相加当作全局周期。

系统统一窗口仍为输入加载、计算与 Root 服务/收集、完成排空。输入写提交保持功能操作；权重预加载及命令/VALIDATE 不计时。该模型补齐 Root 服务假设，尚不代表完整端到端 RTL 或器件延迟。Root 的复位与协议 ERROR 传播仍需独立验证。

## 实验结果

固定同一 Tile 配置、workers=(1,2)、Root=3，全部 24 个形状，比较功能 Root、默认四通道、单通道、单接收槽、慢写回五组。没有人工周期窗口或额外 ejection gate。共 240 条记录：215 次成功、25 次预期不可行，数值、ACK/Done 次数和网络流量均通过守恒检查。

两种切分都合法的 19 个形状中，功能 Root 与默认有限 Root 都是 K 胜 14、ROW 胜 5；单通道 Root 为 K 胜 13、ROW 胜 6。这套形状集合的胜出次数不能解释为现实应用概率。

| 形状 | 功能 Root：ROW/K | 默认有限 Root：ROW/K | 单通道 Root：ROW/K |
|---|---:|---:|---:|
| 32×64 | 505/409 | 510/419 | 516/440 |
| 64×32 | 409/385 | 414/395 | 420/596 |
| 128×16 | 385/421 | 390/431 | 396/1072 |
| 256×8 | 421/535 | 426/734 | 432/2078 |

64×32 的 K 在默认 Root 下仍更快，单通道 Root 下则因真实服务/排队受限反转；这是不同 Root 硬件配置的敏感性，不是 mapper 的同硬件收益。默认 RX 从两槽减成一槽，本次 43 个可行比较的完成周期均保持不变，但包头阻塞计数增加，说明新增等待可被其他服务/计算覆盖。

[CSV](../../data/results/finite_root_system_20261028/results.csv)、[压缩原始 JSON](../../data/results/finite_root_system_20261028/results.json.gz)、[手算 Trace](../../data/results/finite_root_system_20261028/hand_traces.json)、[源码快照](../../data/results/finite_root_system_20261028/source_snapshot.tar.gz) 随仓库发布；记录完整配置、源码哈希和容量边界。
