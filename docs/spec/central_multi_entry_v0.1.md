# 同一集中阵列的 1/2/4 入口模型

关联 Issue #23。状态：Python 架构模型；新增 RTL 校准按任务安排后置。
接续[固定字节预取的长包实验](weight_bursts_v0.1.md)。

## 物理定义与控制变量

集中计算阵列始终位于节点 36：32 组独立四 Lane MAC，共 128 MAC；所有 X/Y 和计算控制器仍属于该集中阵列。
入口节点不执行 MAC 或归约，只负责 NoC 与阵列内部通道之间的转发。

- 独立入口集合依次为 `[36]`、`[36,37]`、`[36,37,44,45]`，位于相邻 2×2 区域。
- 每个入口真正使用对应 Router 的本地接收/注入端口，均最多每个网络 tick 一 Flit；不是在同一个 NI 后面增加软件队列。
- 原先固定的 32 个权重 Bank、ROW 分块和 Worker 顺序不变，上述四节点均不占用权重 Bank。
- Worker 按固定列表下标轮询分配到入口；同一 Worker 的输入、权重、信用、结果都走同一个入口，避免包级乱序。
  这不是最优放置/入口分配搜索；改变入口集合仍会改变部分 NoC 路径，不能将收益全部解释为纯端口带宽。
- 权重包固定 128 B；每簇预取预算固定 128 B（一个包信用）；源组包/发送预算沿用长包实验。
- 每条内部入口链路每核心周期最多一 Flit（16 B），与外部 NoC 的 `link_interval` 分开计时。
- 主对照的内部共享通道固定为每方向四 Lane，即 64 B/核心周期。只改变启用的入口数；单入口仍最多使用其中一 Lane。
- 瓶颈对照将内部共享通道限制为每方向一 Lane，即 16 B/核心周期，对两入口和四入口分别测试。
- 相同 64 个 Router。统一预留四入口及内部最大资源预算，未启用的入口容量不重新分配给活跃入口；NMC 不使用这套汇集通道。
  本模型未证明等面积、等 Fmax 或能耗；预留未使用资源是一项比较预算约定，而不是芯片面积报告。

## 内部通道的周期规则

实现：[central_fabric.py](../../src/simulator/system/central_fabric.py)。

入方向：`NoC → Gateway RX → 内部仲裁/链路 → 集中簇 X/W 缓冲`。
出方向：`集中阵列信用/结果队列 → 内部仲裁/链路 → Gateway TX → NoC`。
输入/权重与信用/结果全部经过内部路径，不让任何一种流量免费瞬移。
两个方向全双工、分别仲裁和限速，不共享同一组 Lane。

每个内部包包含一 Flit 描述信息，以及向上取整的 payload Flit。128 B 权重包需要九个传输机会，信用需要一个。
内部通道在每周期按入口轮询，最多授予 `fabric_lanes` 个不同入口，每入口最多一 Flit。
一个入口上的包保持顺序；包可以在多个周期被仲裁暂停，其他入口可以在其间前进。

最后一 Flit 获得传输机会后，经过 `base_latency + Manhattan(gateway,36)` 周期才交付。
默认入口 36/37/44/45 的延迟分别为 1/2/2/3 个核心周期。传播可与后续包传输重叠，非逐包等待确认。
这条内部星形连接的距离延迟是模型假设；不是通过原 NoC 再走一遍，也尚未经过物理布线校准。

每包第一 Flit 发送前，中央调度器预留目标队列槽位，包含所有尚在内部传输中的包：

- X：每计算簇两包；到达后仍按每周期 1 B 写入 X SRAM。
- W：每簇一个预取包槽，信用覆盖源组装、NoC、Gateway、内部通道和目标预取全过程。
- 出方向：每 Gateway TX 两包。

预留成功后保证该包到达时有空间，不把传播期间的多项预留当成空闲容量。MAC 只在完整权重包交付后取用。
调度器按确定的延迟管理在途槽位；内部预约/仲裁控制线未单独建模传播或能量，须留待 RTL/物理实现验证。
外部权重信用仍在预取 FIFO 最后四字节被读取后生成，经内部出方向和真实 NoC 返回 Bank。

## 有限存储预算

每个入口预留两项 Gateway RX/内部入方向包槽，每项最大 128 B；其中包含正在接收的 NoC 包、待传包和最后 Flit 尚未到达的包。
这两项在整个交付期间持续占用，不因包离开发送队列就立即释放。内部发送可在两项预算内流水化。
四个入口合计 1024 B payload 容量；每计算簇原有预取容量另计，部分写入也由前述目标槽位预留覆盖。

每入口出方向另有两项内部发送槽与两项 Gateway TX 槽，每项最多 16 B payload（结果）或零 payload（信用），
四入口合计分别 128 B 与 128 B。描述符、Header、控制状态和原有四 Flit NI/Router 存储额外计入，不能把这些 payload 数字当作总 SRAM/PPA。
原集中结果队列最多 32 包、信用待发队列最多 `32×prefetch_packets` 包，预算不随入口数量扩张。

终止要求 Host 输出正确且已写入、所有 Compute 完成、NoC/序列化器排空、内部所有预留包与 Gateway TX 排空。
`total_cycles` 继续采用驻留权重的任务边界，非纯 MAC 周期，也不含外部权重加载/配置/量化。

## 与历史模型的关系

`central_entries=None` 保留旧单入口的直接交付路径，默认调用及历史周期不变。
`central_entries=(36,)` 启用新的有限内部通道，虽只有一个入口，周期也可能比旧模型多。
因此实验同时保存 legacy 单入口和新单入口，新增两/四入口的改进率以新单入口为基准。
不能将两种单入口口径混成一个基线。

调用示例：

```python
run_comparison(W, x, architecture='central', central_tile=36,
               central_entries=(36, 37, 44, 45), fabric_lanes=4,
               weight_packet_bytes=128, prefetch_packets=1)
```

## 实验与验证

配置：[multi_entry.json](../../ini/central_comparison/multi_entry.json)。
128×128、128×512 在原始链路 16 B/周期运行；256×512 另外测试 8/4 B/周期。
每个场景七项：NMC、legacy 单入口、新 1/2/4 入口（内部 64 B/周期）、2/4 入口（内部 16 B/周期），共 35 点。
使用相同 seed、W/x、Worker，保存逐点 Golden、流量、各入口计数、内部仲裁/阻塞、周期、配置/源码哈希。
十个对应的 legacy/NMC 点与前轮逐字段重放核对。

手算测试独立验证九 Flit 包的传输/传播边沿、双入口并行、单 Lane 仲裁、在途目的槽位预留。
系统测试覆盖 1/2/4 入口、1/4 内部 Lane、两档网络周期、负数、尾包、单槽 Gateway、慢输出和资源冲突。

```bash
python -m pytest -q -p no:cacheprovider src/tests/test_central_fabric.py src/tests/test_central_comparison.py
python tools/experiments/central_entry_sweep.py --output-dir dump/central_entries
python tools/experiments/plot_central_entries.py --input-dir dump/central_entries
```

本轮不增加 MAC、Root 或模型规模，也未实现输入共享广播、跨任务权重复用、字节信用、多入口最优放置或新 RTL。
