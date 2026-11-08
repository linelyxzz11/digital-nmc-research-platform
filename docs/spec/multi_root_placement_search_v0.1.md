# 多 Root 放置搜索与有限候选 Oracle 实验 v0.1

日期：2026-11-02

证据等级：Python 周期模型；不是 RTL、芯片时序或 PPA 结果

## 1. 研究问题

在相同 GEMV、相同 Worker 数量、相同 Root 数量和相同硬件配置下，静态通信代价 Mapper 能否从更多 Worker/Root 放置中选出接近最优周期的方案？

本实验不再只比较固定的 `compact/spread`。它预声明多组 Root 位置、8 个 random seed 和两种确定性 Worker 放置，对所有合法候选运行同一个 Python 周期模型，并把有限候选集合中的最小周期作为 Oracle。

## 2. 公平实验合同

每个比较组固定：

- 8×8 Mesh、Host Tile 0和Root 0；
- M、K、Worker数量、切分方式与grid；
- 1/2/4个最终Root；
- baseline或pressure Root服务参数；
- resident weights、输入提交、MAC总次数和完成协议；
- central归约。

组内只改变Root位置和Worker位置。ROW、K和二维使用共同工作负载，跨切分时比较绝对周期，不用“各自相对单Root加速比”直接排序。

共同工作负载：

| M | K | Worker | ROW | K | 二维 |
|---:|---:|---:|---:|---:|---:|
| 128 | 64 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 64 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 128 | 16 | 16×1 | 1×16 | 4×4 |
| 256 | 128 | 32 | 32×1 | 1×32 | 8×4 |

## 3. 候选目录

Worker放置包括：

- `compact`；
- `spread`；
- `random` seed 0–7。

Root位置包括：

```text
1 Root:  [0]

2 Roots: [0,7] [0,56] [0,63] [0,36]

4 Roots: [0,7,56,63]
         [0,3,32,35]
         [0,18,36,54]
         [0,7,36,56]
```

固定compact/spread基线使用现有Root集合：1 Root `[0]`，2 Roots `[0,63]`，4 Roots `[0,7,56,63]`。

候选按 `(grid, final_roots, workers)` 去重。本轮共72个比较组、2160个合法候选，0个拒绝候选。每组1 Root有10个候选，2/4 Root各有40个候选。

## 4. Mapper与Oracle边界

Mapper在任何精确周期仿真开始前封存选择。静态排序依次最小化：

```text
最繁忙有向链路估计Flit数
Root最大结果包负载
总估计Flit-hop
Root Done返回Host的总跳数
K任务切换数
计划摘要（只用于稳定打破平局）
```

估计包含Host→Worker输入写、Worker→Host ACK、Worker→Root结果包和Root→Host Done，但不读取真实候选周期。Oracle定义为：

```text
oracle_cycles = min(candidate_cycles)
```

该Oracle仅代表本节有限候选目录，不代表所有物理放置的全局理论最优。

## 5. 指标

```text
oracle_hit = mapper_cycles == oracle_cycles
regret = mapper_cycles / oracle_cycles - 1
speedup_vs_fixed_compact = fixed_compact_cycles / mapper_cycles
speedup_vs_fixed_spread = fixed_spread_cycles / mapper_cycles
```

平局最优也计为命中。跨组加速使用几何平均，同时保留胜/平/负和最坏regret。

## 6. 完整结果

### 6.1 总体

| 指标 | 结果 |
|---|---:|
| 比较组 | 72 |
| 精确候选仿真 | 2160 |
| Oracle命中 | 38/72 |
| Oracle命中率 | 52.78% |
| 平均regret | 5.44% |
| 最坏regret | 28.45% |
| 相对fixed compact几何平均加速 | 1.0520× |
| 相对fixed spread几何平均加速 | 1.1324× |
| 相对fixed compact胜/平/负 | 30/38/4 |
| 相对fixed spread胜/平/负 | 68/0/4 |

结论是：搜索更多Root位置总体优于两个固定放置，尤其明显优于fixed spread；但当前静态评分并不等于精确周期预测，仍存在4个组比fixed compact/spread更慢。

### 6.2 按切分方式

| 切分 | 组数 | 命中率 | 平均regret | 最坏regret | 相对compact | 相对spread |
|---|---:|---:|---:|---:|---:|---:|
| ROW | 24 | 66.67% | 8.61% | 28.45% | 1.0946× | 1.2453× |
| K | 24 | 50.00% | 0.59% | 5.36% | 1.0115× | 1.0446× |
| 二维 | 24 | 41.67% | 7.12% | 24.44% | 1.0515× | 1.1163× |

K切分的Oracle命中率不是最高，但平均和最坏regret最小，说明Mapper即使没有恰好命中最优，所选方案通常也很接近最优。ROW的固定位置优化收益更明显，但4 Root时静态评分可能严重误选。

### 6.3 按Root数量

| Root数 | 组数 | 命中率 | 平均regret | 最坏regret | 相对compact | 相对spread |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 24 | 100.00% | 0.00% | 0.00% | 1.0000× | 1.2058× |
| 2 | 24 | 41.67% | 0.78% | 3.33% | 1.1612× | 1.1605× |
| 4 | 24 | 16.67% | 15.54% | 28.45% | 1.0026× | 1.0377× |

2 Root结果最有研究价值：虽然精确命中率只有41.67%，但最坏regret仅3.33%，同时相对两个固定基线约有16%几何平均收益。4 Root暴露出当前评分函数的主要缺口：它过度优先最繁忙链路，不能充分反映总通信量、Root服务时序和阶段重叠。

### 6.4 按Root服务配置

| Profile | 组数 | 命中率 | 平均regret | 最坏regret | 相对compact | 相对spread |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 36 | 55.56% | 5.83% | 28.45% | 1.0582× | 1.1471× |
| pressure | 36 | 50.00% | 5.06% | 28.21% | 1.0458× | 1.1179× |

### 6.5 最坏regret案例

最坏组为 `M=128, K=64, Worker=16, ROW 16×1, 4 Root, baseline`：

| 方案 | Root位置 | Worker放置 | 周期 | 静态评分前三项 |
|---|---|---|---:|---|
| Mapper | `[0,7,56,63]` | compact | 4650 | 384 / 8 / 5324 |
| Oracle | `[0,3,32,35]` | compact | 3620 | 416 / 8 / 3494 |

Mapper优先选择了更低的估计最繁忙链路负载384，却接受了明显更高的总估计Flit-hop 5324，最终产生28.45% regret。该结果是后续改进代理评分的明确证据，但不能使用本轮Oracle标签直接调参后再把同一目录称为独立验证集。

## 7. 同工作负载下的切分绝对周期

下表格式为 `Mapper/Oracle`。它只比较相同M/K、Worker数、Root数和profile。

| M×K, Worker | Root | Profile | ROW | K | 二维 | Mapper最快 |
|---|---:|---|---:|---:|---:|---|
| 128×64, 16 | 1 | baseline | 3070/3070 | 2786/2786 | 1418/1418 | 二维 |
| 128×64, 16 | 1 | pressure | 3274/3274 | 7618/7618 | 2794/2794 | 二维 |
| 128×64, 16 | 2 | baseline | 3534/3534 | 1537/1527 | 1250/1210 | 二维 |
| 128×64, 16 | 2 | pressure | 3602/3602 | 3953/3943 | 1938/1898 | 二维 |
| 128×64, 16 | 4 | baseline | 4650/3620 | 892/892 | 1329/1068 | K |
| 128×64, 16 | 4 | pressure | 4677/3648 | 2147/2134 | 1673/1412 | 二维 |
| 256×64, 16 | 1 | baseline | 3206/3206 | 5378/5378 | 2090/2090 | 二维 |
| 256×64, 16 | 1 | pressure | 3594/3594 | 15042/15042 | 4842/4842 | ROW |
| 256×64, 16 | 2 | baseline | 3670/3670 | 2833/2823 | 1586/1546 | 二维 |
| 256×64, 16 | 2 | pressure | 3762/3762 | 7665/7655 | 2962/2922 | 二维 |
| 256×64, 16 | 4 | baseline | 4786/3756 | 1690/1604 | 1522/1236 | 二维 |
| 256×64, 16 | 4 | pressure | 4813/3784 | 4170/4020 | 2210/1924 | 二维 |
| 256×128, 16 | 1 | baseline | 6342/6342 | 5562/5562 | 2826/2826 | 二维 |
| 256×128, 16 | 1 | pressure | 6538/6538 | 15226/15226 | 5578/5578 | 二维 |
| 256×128, 16 | 2 | baseline | 7286/7286 | 3057/3037 | 2482/2402 | 二维 |
| 256×128, 16 | 2 | pressure | 7342/7342 | 7889/7869 | 3858/3778 | 二维 |
| 256×128, 16 | 4 | baseline | 9522/7468 | 1800/1800 | 2858/2341 | K |
| 256×128, 16 | 4 | pressure | 9549/7496 | 4334/4310 | 3321/2804 | 二维 |
| 256×128, 32 | 1 | baseline | 14894/14894 | 10772/10772 | 5018/5018 | 二维 |
| 256×128, 32 | 1 | pressure | 15306/15306 | 29652/29652 | 7770/7770 | 二维 |
| 256×128, 32 | 2 | baseline | 16008/16008 | 5739/5673 | 4644/4644 | 二维 |
| 256×128, 32 | 2 | pressure | 16148/16148 | 15179/15113 | 6020/6020 | 二维 |
| 256×128, 32 | 4 | baseline | 18998/15791 | 3152/3152 | 5066/4259 | K |
| 256×128, 32 | 4 | pressure | 19054/15847 | 7868/7868 | 5754/4975 | 二维 |

二维在24个公平组中的20个Mapper结果中最快，K在3个4-Root baseline组中最快，ROW只在`256×64, 16 Worker, 1 Root, pressure`中最快。因此不能仅凭“K相对自身单Root的加速比大”宣称K整体最好。

## 8. Random seed与Root位置观察

本轮所有72个Mapper选择和所有Oracle平局候选都来自compact/spread，random seed 0–7没有产生周期最优候选。这不是说random永远无用，只说明本轮8×8候选目录中，确定性放置已覆盖更好的位置。

Oracle最常选择：

- 1 Root `[0]`：24次；
- 2 Root `[0,56]`：22次；
- 4 Root `[0,3,32,35]`：18次。

当前固定2 Root基线是`[0,63]`，但Oracle更偏好`[0,56]`；这直接证明扩展Root候选位置是必要的。

## 9. 验证与复现

运行命令：

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py tools/experiments/multi_root_placement_search.py `
  --config ini/scalable/multi_root_placement_search.json `
  --output-dir dump/multi_root_placement_search_20261102 `
  --jobs 8
```

完整运行耗时约1892秒。本轮逐组独立重算了Oracle、Mapper周期、regret和两种加速比，检查72组和2160候选，错误数为0。所有候选均 `golden_ok=true`、`global_transaction_done=1`，且Root Done数量等于Root数。

输出：

- `dump/multi_root_placement_search_20261102/results.json`：完整结果和执行摘要；
- `results.csv`：72个比较组；
- `candidates.csv`：2160个候选；
- `rejected.json`：拒绝记录，本轮为空。

## 10. 结论边界与后续

本轮证明“扩展Root位置＋静态Mapper”总体优于固定compact/spread，并给出了有限目录Oracle和可审计regret。但它没有证明当前Mapper已经足够好：4 Root平均regret达到15.54%，最坏28.45%。

下一步若改进评分，应冻结新的评分版本，并使用新的工作负载或新的Root候选作为验证集，避免用本轮Oracle标签调参后在同一数据上报告收益。树形归约、“结果留在Root/完整输出回Host”、多Root RTL和8/16 Root scaling仍未实现，不能由本轮结果外推。
