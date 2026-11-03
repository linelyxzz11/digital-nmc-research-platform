# 同硬件映射搜索实验

四个 calibration 和十二个 holdout 形状；16×28=448 项候选尝试，440 项可行且 GEMV 数值一致，8 项因空 shard/容量约束不可行。策略先作选择，之后才补齐有限候选参考。固定 INT8 随机种子 20261028；同硬件、权重常驻、同一完整模型计时窗口。

## 优先采用更强的 compact 基线

原资源预测器无法区分相同网格的 compact/spread，按计划哈希打破同分。为避免把这种任意同分选择算作收益，额外核查 **UniNDP-adapted + 固定 compact**：保留同一资源预测/分块/搜索，使用默认就近放置先验，不查询最优放置。这是在冻结的候选周期测量上回放的事后基线强度审计，没有新增模拟器执行，也没有调优 NoC 代码或比较新增编译耗时。

| 精确预算 | noc 相对更强 compact 基线的几何平均加速比 | 胜/平/负 | 强基线最优命中 | noc 最优命中 |
|---:|---:|---:|---:|---:|
| 1 | 0.9850× | 1/9/2 | 10/12 | 10/12 |
| 2 | 0.9822× | 0/11/1 | 12/12 | 11/12 |
| 4 | 1.0000× | 0/12/0 | 12/12 | 12/12 |
| 8 | 1.0000× | 0/12/0 | 12/12 | 12/12 |

**当前不能声称通信感知方法优于强基线。** 低预算略慢、存在单例 24.04% 退化；预算 4/8 持平。原先 1.0633× 依赖基线如何打破放置同分，不应作为主要收益。完整 [审计数据](compact_audit.json) 与 [强基线图](figure_compact_audit/compact_baseline.svg) 保留这一反证。

可执行强基线配置为 `search(..., policy='unindp-adapted', placements=('compact',), exact_budget=K)`；回放脚本为 `tools/experiments/audit_compact_baseline.py`。

## 原预设实验完整保留

| 精确模拟预算 | Holdout 几何平均加速比：noc / UniNDP-adapted | 胜/平/负 | 适配基线命中最优 | noc 命中最优 | noc 最坏相对基线比值 |
|---:|---:|---:|---:|---:|---:|
| 1 | 1.0633× | 3/8/1 | 8/12 | 10/12 | 0.9020× |
| 2 | 1.0052× | 2/9/1 | 10/12 | 11/12 | 0.8062× |
| 4 | 1.0000× | 0/12/0 | 12/12 | 12/12 | 1.0000× |
| 8 | 1.0000× | 0/12/0 | 12/12 | 12/12 | 1.0000× |

加速比为 `T_adapted/T_noc`，逐形状取比再求几何平均；最优仅指本目录。预算 1 的 192×96 从基线 1684 周期退化到 1867（慢 10.87%）；预算 2 的 129×65 从 990 退化到 1228（慢 24.04%）。不得只报告平均收益。Holdout Spearman 平均为 0.9457→0.9918，排序改善没有转化成预算 4/8 时的最终延迟收益。

原预设实验显示排序改善与预算敏感性；结合上面的强基线核查，不能将 1.0633× 解释为稳健的研究收益。UniNDP 适配范围见 [合同](../../../docs/spec/mapping_search_v0.3.md)，不能外推为作者完整 DRAM 工具的结果。

文件：

- [summary.json](summary.json)：汇总、分组质量和精确预算前缀。
- [quality.csv](quality.csv)：全部 256 项逐形状/策略/预算结果。
- [records.json.gz](records.json.gz)：16 项完整 case 记录，包括所有可行计划、独立数值检查、网络/端点计数、排序和真实精确评估时间。
- [author_parity.json](author_parity.json)：固定作者代码的八形状分块/分配核对，含无可用满利用切分的边界。
- [compact_audit.json](compact_audit.json)：更强固定放置基线的逐形状/预算回放与分组结果，不计作新增仿真或盲测。
- [source_snapshot.tar.gz](source_snapshot.tar.gz)：对应模型/脚本及集成 RTL 源文件。原始记录在代码提交进入 Git 前后生成，记录的 commit 可不同，但全部 case 的源码指纹相同；快照中的相应文件逐项核对一致。
- [配对预算图](figure/mapping_quality.svg)、[PDF](figure/mapping_quality.pdf)、[PNG](figure/mapping_quality.png)、[图形证据](figure/figure_evidence.json)。

图注：十二个 holdout 形状的任务周期比值。圆点与方点分别只精确评估 1/4 个候选；虚线为相同延迟。每点是一个确定性仿真形状，不使用不确定度或显著性标记。图展示低预算的收益与退化，以及预算增加后的重合；全部预算和最坏情况见上表/CSV。该图适合放在 evaluation 的搜索预算讨论中，不能独立用作“普遍优于 UniNDP”的证据。

图已按最终尺寸渲染检查，SVG 保留可编辑文本。源数据完整、标记/颜色双编码、无文字遮挡；没有作统计推断。绘图脚本为 `tools/experiments/plot_mapping_quality.py`。

搜索耗时是本机单次描述值：公共目录生成、预测和该预算的实际精确执行时间之和；策略过滤/剪枝时间还单独保存在每项策略的 `generation_seconds` 中。冷预测缓存按策略清空。不能从一次 wall time 宣称统计显著的编译加速。

复现：

```bash
python tools/experiments/mapping_evaluation.py run --output-dir dump/mapping/cases
python tools/experiments/mapping_evaluation.py merge --inputs dump/mapping/cases --output-dir dump/mapping/merged
python tools/experiments/plot_mapping_quality.py --summary dump/mapping/merged/summary.json --output-dir dump/mapping/figure
```
