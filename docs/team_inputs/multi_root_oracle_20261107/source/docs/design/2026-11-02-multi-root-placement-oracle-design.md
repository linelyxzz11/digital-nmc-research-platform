# 多 Root 放置搜索与有限候选 Oracle 设计

日期：2026-11-02  
状态：待用户审阅  
范围：8×8 Mesh，1/2/4 个最终 Root，ROW、K、二维切分，Python 周期模型

## 1. 研究问题

现有实验只比较固定 Root 集合下的 `compact` 和 `spread`，并且每个实验只使用一个 seed。下一阶段需要回答：在相同 GEMV、相同 Worker 数量、相同 Root 数量和相同硬件配置下，一个不读取真实周期的 Mapper 能否从更多 Worker/Root 放置候选中选出接近最优周期的方案？

本阶段不增加 8/16 Root，不修改多 Root 执行协议，不声称获得跨规模 scaling law，也不把穷举 Oracle 当作可部署 Mapper。

## 2. 公平比较单位

一次独立比较组由以下字段唯一确定：

```text
(M, K, worker_count, split_family, grid, root_count, root_profile)
```

同组内固定：

- 8×8 Mesh 和 Host Tile 0；
- GEMV 矩阵、输入向量和事务编号；
- Worker 数量、MAC/Bank/NoC/FIFO/Root 服务参数；
- Root 数量和切分方式；
- 权重常驻、输入提交和完成协议；
- 总 MAC 次数及每种切分已有的任务切换规则。

同组内只改变 Root 位置和 Worker 位置。这样 Oracle、Mapper、fixed compact、fixed spread 的周期可以直接比较。

ROW、K、二维另外使用共同工作负载生成一张跨切分表。该表比较绝对周期，不再用“各自相对单 Root 的加速比”推断切分优劣。由于二维可能存在多个合法 grid，grid 仍作为明确字段报告。

第一轮共同工作负载预声明为：

| M | K | Worker数 | ROW grid | K grid | 二维 grid |
|---:|---:|---:|---:|---:|---:|
| 128 | 64 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 64 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 128 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 128 | 32 | 32×1 | 1×32 | 8×4 |

每个工作负载分别执行 baseline/pressure 两种 Root profile 和 1/2/4 Root。按第3节的候选上限，完整实验最多运行 `4×3×2×(10+40+40)=2160` 个候选。先使用缩小配置完成测试和冒烟实验，再运行完整目录。

## 3. 预声明候选集合

### 3.1 Random seed

第一轮使用 8 个 seed：

```text
0, 1, 2, 3, 4, 5, 6, 7
```

seed 只控制 `random` Worker 放置，不改变 GEMV 数据。`compact` 和 `spread` 是确定性候选，不因 seed 重复生成。

### 3.2 Root 位置

Root 0 保持在 Tile 0，与 Host 共处，以保持与当前实验一致的资源占用关系。其余 Root 使用预声明模板：

```text
1 Root:
  [0]

2 Roots:
  [0, 7]    # 横向远端
  [0, 56]   # 纵向远端
  [0, 63]   # 对角远端，现有固定方案
  [0, 36]   # 内部位置

4 Roots:
  [0, 7, 56, 63]   # 四角，现有固定方案
  [0, 3, 32, 35]   # 两行中部
  [0, 18, 36, 54]  # 内部对角分散
  [0, 7, 36, 56]   # 边缘与内部混合
```

每个 Root 集合均需通过 Mapper 的合法性检查。Root Tile 不得同时作为 Worker；Worker 数量保持不变。

### 3.3 Worker 位置

每个合法 Root 集合生成：

- 一个 `compact` 候选；
- 一个 `spread` 候选；
- 8 个 `random` 候选，对应 seed 0–7。

候选按 `(grid, final_roots, workers)` 去重。随机 seed 产生重复 Worker 集合时只保留一次，但记录所有来源 seed。

### 3.4 候选规模

候选规模上限为：

- 1 Root：10 个放置候选；
- 2 Roots：4×10=40 个候选；
- 4 Roots：4×10=40 个候选。

实际数量可能因去重或不可行计划略少。所有拒绝项必须记录原因，不能静默丢弃。

## 4. Mapper：不读取真实周期的通信代价排序

新增一个多 Root 放置选择器。它只读取 `GridPlan` 的静态信息，不调用 `run_grid`，不读取已有实验结果。

对每个候选估计以下量：

1. Host 到各 Worker 的输入分发 XY 路径负载；
2. Worker 到所属最终 Root 的结果/部分和 XY 路径负载；
3. 最繁忙链路的估计 Flit 负载；
4. 总估计 Flit-hop；
5. 各 Root 接收结果量的最大值与负载不均衡；
6. K 多 Root 的任务切换数量，作为固定配置开销；
7. 完成包从各 Root 返回 Host 的路径代价。

Mapper 使用固定、可复现的字典序排序：

```text
(estimated_busiest_link_load,
 estimated_max_root_load,
 estimated_total_flit_hops,
 estimated_completion_hops,
 task_switch_count,
 plan_digest)
```

最后的 `plan_digest` 仅用于稳定打破平局。该分数是通信代理，不伪称为精确周期预测器。Mapper 选择排序第一的候选；随后才运行周期模型得到 `mapper_cycles`。

## 5. Oracle 与基线

### 5.1 有限候选 Oracle

同组所有合法候选都使用同一个 `run_grid` 周期模型执行：

```text
oracle_cycles = min(candidate_cycles)
```

Oracle 只代表第 3 节预声明候选集合中的最优值，不代表全体物理放置的理论全局最优。

### 5.2 Fixed compact

使用当前规范中的固定 Root 集合：

- 1 Root `[0]`；
- 2 Roots `[0, 63]`；
- 4 Roots `[0, 7, 56, 63]`；

Worker 使用 `compact`。其实际周期记为 `fixed_compact_cycles`。

### 5.3 Fixed spread

使用相同固定 Root 集合，Worker 使用 `spread`。其实际周期记为 `fixed_spread_cycles`。

## 6. 指标定义

每组至少保存：

```text
oracle_cycles
mapper_cycles
fixed_compact_cycles
fixed_spread_cycles

oracle_hit = mapper_cycles == oracle_cycles
regret = mapper_cycles / oracle_cycles - 1
speedup_vs_fixed_compact = fixed_compact_cycles / mapper_cycles
speedup_vs_fixed_spread = fixed_spread_cycles / mapper_cycles
```

平局时，只要 Mapper 选中的候选周期等于 Oracle 周期，就算 Oracle hit。

汇总报告包括：

- Oracle 最优命中数和命中率；
- 平均 regret、几何平均周期比和最坏 regret；
- 相对 fixed compact/spread 的几何平均加速；
- 胜/平/负数量；
- 按 ROW/K/二维、1/2/4 Root、baseline/pressure 分层结果；
- 每个 seed 成为 Mapper 候选或 Oracle 候选的次数；
- Oracle 方案的 Root/Worker 位置分布；
- 总候选数、合法数、拒绝数和精确周期模型调用数。

不能只报告平均值；必须保留逐工作负载结果和最坏退化。

## 7. 数据流与文件边界

新增公共模块，避免复制三套统计逻辑：

```text
ini/scalable/multi_root_placement_search.json
    ↓
候选生成器：Root 模板 × compact/spread/random seeds
    ↓
静态通信代价 Mapper 排序
    ↓
周期模型执行全部合法候选
    ↓
Oracle、固定基线和 Mapper 指标汇总
    ↓
JSON + CSV
```

建议文件：

- `src/compiler/multi_root_placement.py`：候选去重、静态通信代价与Mapper选择；
- `tools/experiments/multi_root_placement_search.py`：配置读取、精确执行、Oracle和汇总；
- `ini/scalable/multi_root_placement_search.json`：预声明seed、Root模板、共同工作负载和硬件配置；
- `src/tests/test_multi_root_placement.py`：候选合法性、无Oracle泄漏、公式和可复现性测试；
- `docs/spec/multi_root_placement_search_v0.1.md`：最终实验合同与结果。

原有 `multi_root_row.py`、`multi_root_k.py`、`multi_root_2d.py` 保持兼容，不将本阶段逻辑直接塞入三个旧入口。

## 8. 输出结构

`results.json` 保存：

- 配置哈希、源码哈希、Git状态和Python环境；
- 每个候选的计划摘要、Root/Worker位置、来源placement/seed；
- Mapper静态代理各分量、排名和选择结果；
- 每个候选的真实周期与现有NoC/Root/Compute指标；
- 每组Oracle、基线、regret、命中和加速比；
- 汇总统计。

`results.csv` 保存逐比较组的主要指标；`candidates.csv` 保存逐候选明细；`rejected.json` 保存拒绝原因。

## 9. 正确性与防止 Oracle 泄漏

测试必须证明：

1. 改变候选的真实 `cycles` 字段不会改变Mapper排序；
2. Mapper选择在调用任何精确周期模型之前完成并封存；
3. 相同配置重复生成的候选集合、排序和摘要完全一致；
4. Oracle只从成功且数值正确的候选中产生；
5. fixed compact/spread一定属于候选集合；
6. 所有比较组的M/K、Worker数、Root数和硬件参数一致；
7. `regret >= 0`，命中时 `regret == 0`；
8. 加速比方向为“基线周期/Mapper周期”；
9. ROW、K、二维多Root数值结果仍与GEMV golden一致；
10. 不支持的tree、多Root RTL或Host完整输出回收模式不得被静默标为已验证。

## 10. 本阶段明确不做

- 不增加8/16 Root；
- 不修改Root完成协议；
- 不实现多Root tree归约；
- 不实现K/二维多Root RTL；
- 不扩展现有v0.4有界周期预测器；
- 不把结果换算成频率、TOPS或能耗；
- 不自动提交、推送或合并到GitHub。

树形归约对照和“结果留在各Root/完整输出返回Host”属于下一份独立实验合同，避免与放置搜索同时改变多个变量。
