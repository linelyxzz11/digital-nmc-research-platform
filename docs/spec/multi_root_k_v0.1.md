# K 切分多最终 Root 执行合同 v0.1

## 1. 研究问题与范围

本合同研究纯 K 切分 GEMV 中，多个最终 Root 是否能分担部分和规约、结果写回和附近 NoC 链路压力。

Python 周期模型支持 central K 切分使用 1、2、4 个最终 Root。所有方案使用相同的 8×8/64 Tile、相同 K Worker、相同 K 分片、相同 MAC 总数和相同硬件参数。当前不支持 tree K 多 Root、二维多 Root、相关自动预测、完整任务队列 RTL、4096×4096、综合或 PPA。

## 2. 为什么需要任务队列

K Worker产生全部输出行的部分和，而一个 `TileTask` 只有一个 `result_dst_tile`。因此每个物理 K Worker被表示为顺序子任务队列：

```text
Worker 5:
  Task A: y[0:128]   的部分和 -> Root A
  Task B: y[128:256] 的部分和 -> Root B
```

每个子任务仍只有一个目的 Root，不修改 ResultPacket 线格式。所有子任务的 `m_local` 之和等于 M，因此不重复计算：

```text
sum(useful_mac_ops) = M * K
```

## 3. 计划与版本

K 多 Root计划新增：

```text
TileTaskQueue:
  tile_id: int
  tasks: tuple[TileTask, ...]

GridPlan.task_queues: tuple[TileTaskQueue, ...]
```

版本兼容规则：

- 单 Root计划继续按 v0.2 序列化；
- ROW 多 Root继续按 v0.3 序列化，原 digest不变；
- K 多 Root按 v0.4 序列化并记录完整队列顺序。

K 多 Root只接受纯 K 网格 `(1, K_parts)` 和 `central` 规约。tree与二维入口会在执行前被拒绝。

## 4. 权重窗口和输出范围

Weight SRAM按四行一组打包。Root边界因此按四行组切分，最后一个范围允许不足四行。对于 Root负责的 `[row_start,row_end)`：

```text
m_local         = row_end - row_start
global_row_base = row_start
w_base          = W_START + 4 * (row_start // 4) * k_local
x_base          = 原K分片输入地址
y_base          = Y_START
result_kind     = PSUM
result_dst_tile = 对应Root
task_last       = True
```

不同子任务复用同一份常驻权重和输入。`y_base` 是本地临时结果窗口；前一任务的结果完成握手后才切换任务，因此可以安全复用。

## 5. 顺序执行和轮转调度

每个 Tile同一时刻只运行一个子任务。中间子任务的 LocalDone由队列内部消费，系统只在最后一个子任务结束后记录一次物理 Worker LocalDone。

相邻子任务之间固定计入一个 `task_switch_cycles`；第一个任务没有额外启动开销。该一周期是当前Python模型假设，尚未由RTL校准。

为避免所有 Worker同时只向同一个 Root发送，队列按 Worker索引循环移位：

```text
Worker 0: A -> B -> C -> D
Worker 1: B -> C -> D -> A
Worker 2: C -> D -> A -> B
Worker 3: D -> A -> B -> C
```

调度顺序进入v0.4计划和digest，运行时不会根据拥塞动态改变。

## 6. Root规约与完成

每个 Root负责一个连续输出窗口，并期望该窗口中的每行来自全部 K Worker：

```text
source_mask = OR(1 << worker for worker in workers)
```

Root收齐某行全部部分和后写入自己的本地 Result SRAM。每个最终 Root分别发送一个 `TransactionDonePacket`；Host收齐全部 Root Done后只产生一次全局完成：

```text
root_done_packets = final Root数量
global_transaction_done = 1
```

系统还必须等待所有Worker队列完成、全部端点空闲且Mesh排空。最后按 `FinalRootRange` 从各Root的本地Result SRAM拼接完整y，并与CPU INT32参考逐项比较。

## 7. 公平实验

固定Root集合：

```text
1 Root : (0,)
2 Roots: (0,63)
4 Roots: (0,7,56,63)
```

所有Root数量使用从4-Root候选中冻结的完全相同Worker集合；未启用的候选Root不能改作额外Worker。

首轮包含：

- M=128、K=64、8个K Worker；
- M=256、K=64、16个K Worker；
- M=256、K=128、16个K Worker；
- compact/spread两种放置；
- baseline/pressure两种Root服务配置；
- 1/2/4 Root。

共36组。pressure配置为1包接收队列、每周期1 lane、写回2周期，只用于Root瓶颈敏感性分析，不代表默认硬件性能。

## 8. 指标

主指标是包含计算、NoC、规约、任务切换和Done排空的总周期。辅助指标包括：

- 相对同组1 Root的加速比；
- `task_switch_cycles`和每Worker子任务数；
- flit-hop、最忙链路利用率、FIFO/端口阻塞；
- 每Root接收包、服务、写回、最大队列和完成周期；
- Root负载不均衡和完成时间跨度；
- Root Done数量与唯一全局完成；
- 有效MAC总数，必须等于 `M*K`。

## 9. 本地实验结果（2026-11-01）

36/36案例数值正确、MAC总数正确且每组仅产生一次全局完成。

| Root服务 | Root数 | 平均总周期 | 相对1 Root平均加速 | 最小–最大加速 |
|---|---:|---:|---:|---:|
| baseline | 1 | 4291.67 | 1.000× | 1.000–1.000× |
| baseline | 2 | 2411.83 | 1.754× | 1.647–1.886× |
| baseline | 4 | 1454.67 | 2.821× | 2.232–3.415× |
| pressure | 1 | 11577.00 | 1.000× | 1.000–1.000× |
| pressure | 2 | 6076.00 | 1.893× | 1.845–1.957× |
| pressure | 4 | 3342.00 | 3.415× | 3.211–3.767× |

最佳案例为 `M=256, K=64, 16 workers, compact, pressure, 4 Roots`：总周期4020，相对单Root加速3.7667×，全部16个Worker合计产生48个任务切换周期。

本轮最弱的多Root案例仍为1.6472×。与ROW多Root约1%的默认收益不同，纯K切分中每个Worker都向最终Root发送全部M行的部分和，单Root需要承担完整集中规约，因此更容易成为主要瓶颈。该结论只适用于本合同的36组Python周期模型案例，不外推为RTL、芯片或其他规模的固定加速比。

原始本地结果位于 `dump/multi_root_k_20261101/`，该目录可重建且不随Git发布。

## 10. 复现

在仓库根目录执行：

```bash
python tools/experiments/multi_root_k.py --output-dir dump/multi_root_k_20261101
python -m pytest -q -p no:cacheprovider src/tests/test_multi_root_k.py
```

配置入口为 `ini/scalable/multi_root_k.json`。未适配任务队列的预测器会明确拒绝v0.4计划，不会静默输出错误周期。
