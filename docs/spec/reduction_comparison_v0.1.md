# Central Reduction 与 Tree Reduction 周期对比报告 v0.1

本次整理分支：`codex/sim-reduction-comparison`，基于 `codex/sim-multi-root-extension` 的 `40ef59a10f4581cd550e7f83f5438dc15621fd0a`。原实验于 2026-11-08 在本地完成；[发布结果与核验记录](../../data/results/reduction_comparison_20261109/README.md) 区分原实验与本次重跑。分支复用已有单 Root Tree 执行逻辑，新增比较入口和最终写回统计。

## 1. 实验目标

本实验完成项目主线中的第二项任务：在相同 GEMV、相同计算 Worker、相同 MAC Lane、相同放置方式和相同周期参数下，对比：

- 集中归约（central reduction）；
- 树形归约（tree reduction，fan-in=4）。

ROW、K、2D 三种切分都参与实验。ROW 不产生跨 K 部分和，因此只是退化正确性对照；K 和 2D 才是有效的归约性能比较。

Tree 相对 Central 的加速比定义为：

`speedup = Central 总周期 / Tree 总周期`

数值大于 1 表示 Tree 更快。

## 2. 公平性边界

两种归约共享以下条件：

- `8×8` Mesh，共 64 个物理 Tile；
- compact 放置；
- Host 与唯一最终 Root 均为 Tile 0；
- 完全相同的计算 Worker 位置；
- 每个 Worker 4 个 INT8 MAC Lane；
- 完全相同的 Tile、Bank、FIFO、NoC 和 Root 周期参数；
- 完全相同的输入分发过程；
- 权重预驻留；
- 最终结果均经过 Root SRAM 写回和 Done 通知。

完成口径为结果写入最终 Root 的 SRAM、Host 收到 Done 并排空执行链；不包含权重首次加载、完整输出经 NoC 返回 Host、Python Mapper 搜索时间。这里的 Central 指集中归约，不是“集中式计算 vs NMC”的集中式计算基线。

Tree 会从同一块 64-Tile 芯片中尚未用于计算的 Tile 里分配中间 reducer。Central 不使用这些中间 Tile。因此本实验公平地固定了**整芯片物理预算和计算资源**，但 Tree 的活跃 Tile 数更多。报告不能把这描述成相同的活跃面积或相同能耗。

## 3. 工作负载与额外 Reducer

| M×K | Worker | ROW | K | 2D | K Tree reducer | 2D Tree reducer |
|---|---:|---:|---:|---:|---:|---:|
| 128×64 | 16 | 16×1 | 1×16 | 4×4 | 5 | 4 |
| 256×64 | 16 | 16×1 | 1×16 | 4×4 | 5 | 4 |
| 256×128 | 16 | 16×1 | 1×16 | 4×4 | 5 | 4 |
| 256×128 | 32 | 32×1 | 1×32 | 8×4 | 11 | 8 |

16-Worker Central 使用 17 个活跃 Tile（16 Worker + Host/Root），Tree K 使用 22 个，Tree 2D 使用 21 个。32-Worker Central 使用 33 个，Tree K 使用 44 个，Tree 2D 使用 41 个。所有方案均不超过 64-Tile 物理预算。

## 4. 端到端周期结果

| M×K / Worker | 切分 | Central 周期 | Tree 周期 | Tree 加速比 |
|---|---|---:|---:|---:|
| 128×64 / 16 | ROW | 3070 | 3070 | 1.000× |
|  | K | 2786 | 1190 | **2.341×** |
|  | 2D | 1418 | 936 | **1.515×** |
| 256×64 / 16 | ROW | 3206 | 3206 | 1.000× |
|  | K | 5378 | 2190 | **2.456×** |
|  | 2D | 2090 | 1104 | **1.893×** |
| 256×128 / 16 | ROW | 6342 | 6342 | 1.000× |
|  | K | 5562 | 2363 | **2.354×** |
|  | 2D | 2826 | 2065 | **1.369×** |
| 256×128 / 32 | ROW | 14894 | 14894 | 1.000× |
|  | K | 10772 | 2845 | **3.786×** |
|  | 2D | 5018 | 3982 | **1.260×** |

四个工作负载的几何平均结果：

| 切分 | Tree 胜/平/负 | 几何平均加速 |
|---|---:|---:|
| ROW | 0 / 4 / 0 | 1.000× |
| K | 4 / 0 / 0 | **2.675×** |
| 2D | 4 / 0 / 0 | **1.491×** |

## 5. 周期为什么下降

### 5.1 输入周期没有变化

Central 和 Tree 使用相同 Worker 及相同输入分发路径，所以每一对实验的输入周期完全一致。Tree 的性能提升全部来自 `compute_and_gather` 阶段，而不是改变输入条件。

### 5.2 Tree 把单 Root 的串行归约分散到多个节点

以 `256×128、32 Worker、K 切分`为例：

- Central 计算和归约阶段：10316 周期；
- Tree 计算和归约阶段：2389 周期；
- 最终端到端周期从 10772 降到 2845；
- 输出跨度从 8064 降到 2279 周期。

Central 需要让单 Root 接收并处理所有 Worker 的部分和。Tree 先让 11 个中间 reducer 并行合并，再把较少的聚合结果送往最终 Root，因此显著缩短关键路径。

### 5.3 Tree 并没有减少网络总流量

Tree 的 flit-hop 实际更高。例如上述 32-Worker K 实验从 19257 增加到 21177。原因是同一批数据经过了多级 reducer，增加了中间转发跳数。

因此正确结论不是“Tree 通信更少”，而是：

> Tree 用更多中间传输和更多活跃 reducer Tile，换取归约工作的空间并行，避免单 Root 串行处理成为关键路径。

### 5.4 Root 等待周期需要谨慎解释

Tree 下最终 Root 的空闲等待周期高于 Central，因为最终 Root 会等待中间 reducer 生成已聚合结果。这不等同于系统更拥塞。最终判断必须结合总周期和输出跨度：虽然最终 Root 等待更久，整个系统完成得更早。

### 5.5 为什么 K 的收益比 2D 更大

纯 K 切分中，所有 Worker 都产生全部输出行的部分和，Central Root 压力最大，因此 Tree 的分层并行收益最明显。2D 已经通过 ROW 维切分缩小了每个归约组的输出范围，Central 的瓶颈相对较弱，所以 Tree 仍然有效，但加速较小。

## 6. 结论

1. ROW 的 Central/Tree 周期和所有主要指标完全一致，符合“没有跨 K 归约”的预期。
2. K 的 Tree 在四组工作负载中全部获胜，几何平均加速为 `2.675×`。
3. 2D 的 Tree 在四组工作负载中全部获胜，几何平均加速为 `1.491×`。
4. 当前架构的单 Root 集中归约确实是 K 和 2D 的性能瓶颈，Tree 能有效缓解该瓶颈。
5. Tree 的代价是更多 reducer Tile、更多 flit-hop 和更复杂的控制，而不是免费的周期优化。

## 7. 局限性

- 只测试 compact 放置和 fan-in=4，没有搜索 fan-in=2/8 或其他 reducer 位置；
- 只测试单最终 Root，未与多 Root central reduction 交叉比较；
- 周期来自 Python 事件模型，不是完整 RTL 时序或真实芯片频率；
- 没有评估面积、功耗、能耗或 reducer Tile 的机会成本；
- 最繁忙链路利用率是按完整端到端周期归一化的观察指标，不能替代逐阶段拥塞分析。

`run_grid(...).root_metrics` 新增四个只读统计，不改变调度或时序：

| 字段 | 定义 |
|---|---|
| `first_result_write_cycle` | 最终 Root 首次提交输出写回的全局周期，从 1 开始 |
| `last_result_write_cycle` | 最终 Root 最后一次提交输出写回的全局周期 |
| `output_span_cycles` | 末周期减首周期再加 1，包含两次写回之间的空闲周期 |
| `result_write_cycles` | 至少一个最终 Root 提交输出的不同全局周期数；同周期多个 Root 写回仅计一次 |

中间 reducer 的写回不进入上述统计；`result_write_cycles` 不是所有 Root 写口占用周期之和。`root_waiting_cycles` 由最终 Root 的 IDLE 统计扣除输入/完成阶段估算，只用于辅助解释，不等同于 NoC 阻塞率。

## 8. 正确性与复现

- 24 条记录均通过独立 INT32 Golden GEMV 核对；
- 每条记录满足 `总周期 = 输入 + 计算归约 + 完成`；
- 每条记录的有效 MAC 数均为 `M×K`；
- 同一 workload/split 的 Central 与 Tree 使用完全相同的 Worker 和 MAC Lane；
- ROW 的 Central 与 Tree 周期完全一致；
- JSON 中保存配置、平台、Python 版本及关键源码 SHA-256。

本地复现命令：

```powershell
.\.venv\Scripts\python.exe tools\experiments\reduction_comparison.py `
  --config ini\experiments\reduction_comparison.json `
  --output-dir dump\reduction_comparison_20261108
```

程序拒绝覆盖非空输出目录；重复运行时应使用新的输出目录，保留旧实验的审计记录。

## 9. 后续可选实验

本主线任务完成后，如果继续研究归约拓扑，可以依次比较：

1. fan-in=2、4、8 的周期与 reducer 数量权衡；
2. Tree 与 2/4 Root central reduction 的周期、活跃 Tile 和 flit-hop；
3. reducer 位置搜索相对当前确定性放置的收益；
4. 在 RTL 中验证一个最小的两级 Tree 归约事务。
