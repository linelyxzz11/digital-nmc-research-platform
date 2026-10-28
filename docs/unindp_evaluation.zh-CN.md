# UniNDP 对照指标与下一轮实验

2026-10-28。项目参考文献中的名称是 **UniNDP**，HPCA 2025；本轮按该工作核对用户提到的 unidap。[作者论文](https://nicsefc.ee.tsinghua.edu.cn/nics_file/pdf/d356f1fd-6204-4b21-8849-a9c3b2e15065.pdf)、[作者代码](https://github.com/godfather991/UniNDP)，本机固定提交 `c8b21684358441d29fc81fa0866d07cec20582ed`，已核对为当次远端 HEAD。

研究重点转为映射评估和对照实验。已通过的 Tile/宽写回 RTL 留作小规模校准；不再把逐项扩大 RTL 作为产生实验结论的前置条件。

## 先解决收益空间过小的问题

对既有有限 Tile/Root 结果重新审计，固定 workers=(1,2)、Root=3，只比较均衡 ROW/K。在二者都可行的 19 个形状上：

| 策略 | 相对固定 K 的几何平均加速比 | 命中该两选一最优 | 最坏 regret |
|---|---:|---:|---:|
| 固定 ROW | 0.8392× | 5/19 | 98.80% |
| 固定 K | 1.0000× | 14/19 | 72.30% |
| 当前 AUTO | 1.0418× | 17/19 | 4.81% |
| 两选一事后最优 | 1.0462× | 19/19 | 0 |

`regret = T_selected/T_best_in_declared_candidates - 1`。全部 24 形状的可行覆盖为 ROW 20/24、K 23/24、AUTO 24/24；上表性能比只用共同可行的 19 项，不把不可行项目丢掉后假装性能更好。AUTO 的两个误选是 128×64、64×32。

这说明现有两选一范围的收益上限很小；1.0462× 是该固定候选集上的事后上界，不是整个 NMC 问题的理论上界。随后已扫描全部合法 worker/Root 放置及五档 K 切分：24 形状、1,680 候选，1,188 次执行正确、492 次因容量或切分约束不可行。共同可行的 19 项上，扩大后的事后最优相对原固定 K 为 **1.0490×**，相对现有 AUTO 仅 **1.0068×**。各形状的最优周期都比原固定放置下的两选一最优少 2 周期，选择 Host 0 兼 Root 即可省掉 Done 的两跳。

因此下一阶段优先参数化 Python Mesh/任务接口，增加到 4×4 和至少四个计算 Tile；RTL 保留当前小规模对照集。固定 K 基线应加入就近放置和 Host 兼 Root，避免把显然可消除的两跳当作新 mapper 的收益。不能通过只换弱 ROW 基线扩大数字，也不能把硬件配置变化计作 mapper 收益。

审计数据：[JSON](../data/results/mapping_headroom_20261028/results.json)、[逐形状 CSV](../data/results/mapping_headroom_20261028/results.csv)。这是既有实验的重分析，不是新增的 UniNDP 性能对比。

扩大候选的数据：[汇总](../data/results/mapping_candidates_20261028/summary.json)、[完整压缩 JSON](../data/results/mapping_candidates_20261028/results.json.gz)、[CSV](../data/results/mapping_candidates_20261028/results.csv)、[源码快照](../data/results/mapping_candidates_20261028/source_snapshot.tar.gz)。候选中的 ROW 保持均衡，K 采用 1/8、1/4、1/2、3/4、7/8 采样；未声称穷举所有整数切分或二维分块。

## 对齐哪些指标

UniNDP 第 VII-B～F 节分别比较映射收益、模拟器校验、搜索剪枝、预测器与机制分解。我们沿用这些评估问题，并补充 Mesh 的观测量。

| 优先级 | 指标及定义 | 本项目的比较口径 |
|---|---|---|
| 主指标 | 任务延迟与几何平均加速比：`S_i=T_baseline,i/T_policy,i`，`G=exp(mean(log S_i))` | 同一硬件、工作负载、数据精度、权重常驻条件和计时窗口；同时列出最坏退化与可行覆盖率 |
| 主指标 | 映射质量：regret、最优命中率、Top-K 命中 | 对同一有限候选目录做穷举，得到小系统参考；穷举最优只作为上界 |
| 主指标 | 搜索效率：候选生成/预测/精确评估耗时，生成/剪枝/仿真候选数 | 同一机器、同一候选范围、同一时间或评估预算；绘制质量—耗时曲线 |
| 主指标 | 预测器质量：Spearman 排序相关、Top-K 召回、选解 regret | 用共同模拟器评价；有周期单位且经过独立标定的预测器才报告 MAPE；不能把现有 `cost_score` 当作绝对周期预测 |
| 校准指标 | 模型与 RTL 的周期偏差、排序一致性 | 只在实际存在共同 RTL 的子模块/小例子上报告；不将单 Tile 一致性说成完整系统误差 |
| 解释指标 | 指令/访问数；NoC byte、flit-hop、注入停顿、FIFO 满周期；Bank/MAC、Root 服务与排队 | 用来解释任务延迟变化。Root 服务与 NoC/Compute 有重叠，不能直接堆叠相加；计数注明 packet、flit-cycle、tile-cycle 等单位 |

UniNDP 原生 DRAM row-change 对 SRAM Tile 不适用，记为 N/A。当前没有可靠功耗、工艺和频率模型，TOPS/W、能量和面积不作为这一轮主结论。

当前总周期是权重常驻、功能输入写提交、逐请求 Tile 和有限 Root 的**模型任务周期**，不是已校准的完整硬件端到端时延。kernel/加载/完成排空分别报告；若以后加入权重加载和外存，另列 cold/warm 条件。

## 已跑通作者原生对照

使用作者原始 AiM 配置（64 channel、16-bit 数据宽度）和固定 requirements；在独立临时克隆运行 `compile.py -A aim -W mm -Q -K 30`。我们的 GEMV `W[M,K]×x[K]` 对应作者 `-S 1 K M 1`，不要把双方符号 M 的含义混淆。

| GEMV 输出×归约维度 | 作者 baseline latency | UniNDP latency | 原生加速比 | Host DRAM 访问数：baseline→优化 |
|---|---:|---:|---:|---:|
| 4096×4096 | 10922 | 9366 | 1.1661× | 284→64 |
| 11008×4096 | 26714 | 24722 | 1.0806× | 333→107 |
| 4096×11008 | 29406 | 25646 | 1.1466× | 772→107 |

保留作者模拟器原生时间单位，仅在同一作者配置内计算比值。CSV 的原始字段对应 `Optimal_lat/Baseline_lat`、`cmd`、`pu_DRAM_access`、`host_DRAM_access`、`row_change`。第三例的 row-change 从 44 增至 64，总延迟仍下降，说明单个访问计数不能替代主时延指标。

以上是三个代表性原生案例的复跑，不是论文全量复现，也不是本项目相对 UniNDP 的加速比。编译 wall time 已记录，但单次运行不用于宣称搜索效率优势。作者工具主要生成指令和时序，本轮没有额外做其 GEMV 数值正确性验证。

数据与完整参数：[JSON](../data/results/unindp_native_20261028/results.json)、[CSV](../data/results/unindp_native_20261028/results.csv)。复现脚本会临时克隆本机作者仓库，不把作者代码提交到本项目：

```bash
python -m pip install --target /tmp/unindp-deps -r lib/UniNDP/requirements.txt
python tools/experiments/unindp_native.py --author-root lib/UniNDP \
  --dependency-dir /tmp/unindp-deps --output-dir dump/unindp_native
python tools/experiments/mapping_headroom.py --output-dir dump/mapping_headroom
python tools/experiments/mapping_candidate_sweep.py \
  --shapes suite --k-splits sample --output-dir dump/mapping_candidates
```

## 同硬件对比与交付顺序

统一使用一份可行候选目录和同一个 Tile/Root/NoC evaluator，比较固定 K、固定 ROW、现有 AUTO、忽略 Mesh 拥塞的资源/访问代价方法、拥塞感知方法及穷举参考。最后两种方法目前仍需实现；不能将两条自写公式直接命名为已复现的 UniNDP。

要形成直接 UniNDP baseline，需要移植其分块、映射和候选筛选决策，适配本项目的容量、Lane、字节地址和任务接口，再把计划交给同一个 Mesh evaluator。作者原生树状 DRAM 配置与我们的 SRAM Mesh 不同，直接比较两边 raw cycles 无法隔离 mapper 收益。

下一轮集中交付三类结果：

1. 同硬件映射延迟/加速比，以及所有退化和不可行项目；
2. 相同候选/时间预算下的质量—搜索耗时曲线；
3. 预测排序/Top-K 质量，以及能解释误选的 NoC、Bank、Root 计数。

本机先跑候选目录和模型实验；一台空闲电脑复跑 UniNDP 原生工作负载，另一台复现同硬件候选评估和压力配置。不同配置分别汇总。只有时序敏感性改变候选排序或结论时，才优先补对应硬件校准；多 Tile RTL、完整归约 RTL 与更细器件实现随后推进。
