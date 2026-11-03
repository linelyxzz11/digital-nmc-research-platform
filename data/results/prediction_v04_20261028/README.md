# v0.4：误选与时序预测修复

预测器与验证协议冻结于 `9aaf78037f155ad5249451dfe0950ad29a8c15a4`，随后运行八个预先固定的新形状。旧十六形状只作开发回放。强基线、完整 evaluator、硬件、候选目录和计时窗口均保持不变，原始 v0.3 记录保留。

## 两个误选已修复

每个案例只精确执行排名第一的方案：

| GEMV | 旧版选择及实际周期 | 新版选择及实际周期 | 新版预测周期 |
|---|---|---|---:|
| 129×65 | `(1,16)` compact/tree：1228 | `(4,4)` compact/tree：990 | 999 |
| 192×96 | `(1,16)` compact/tree：1867 | `(4,4)` compact/tree：1465 | 1474 |

129×65 的旧 K 方案预测为输入 221＋kernel/drain 760，实际为 187＋1041；192×96 为预测 424＋1071，实际 360＋1507。输入每包多算两周期，同时遗漏上游反压，造成排序错误。新版输入预测与参考模型一致，并显式限制 NI、Router 和端点队列；下一组结果等待握手后释放。

## 新形状验证

预先固定的八个形状：23×79、47×143、79×47、111×173、159×91、223×43、95×287、191×239；种子 70417。每形状尝试 28 个配置，共 224 项；220 项可行且数值通过。四项空分块来自 23×79 的 `(32,1)` × 两放置 × 两归约。

| 指标 | 强适配基线＋compact | 旧 NoC | 新 NoC |
|---|---:|---:|---:|
| 预算 1：最优命中 | 7/8 | 6/8 | **8/8** |
| 预算 2：最优命中 | 8/8 | 7/8 | **8/8** |
| 预算 4：最优命中 | 8/8 | 7/8 | **8/8** |
| 预算 1：相对强基线几何平均加速比 | 1.0000× | 0.9797× | **1.0205×** |
| 预算 1：最坏相对强基线比值 | 1.0000× | 0.8360× | **1.0000×** |
| 平均排序耗时，秒/形状 | 0.109 | 0.128 | **1.055** |
| 预算 1：平均生成＋排序＋数值评估耗时，秒 | 0.550 | 0.653 | **1.462** |

最优仅指固定候选目录。新 NoC 在 159×91 选择 1394 周期，强基线首次选择 1640；其他七形状与强基线首次选择相同。**本轮证据是选解更稳，不能声称搜索更快**：强基线增加到两次数值评估也能 8/8 命中，平均 1.174 秒，低于新模型一次评估的 1.462 秒。wall time 是本机两个分片并行运行的一次描述性测量，不作统计推断。

## 误差与模型范围

| 数据范围 | 可行配置 | 旧/新任务周期 MAPE | 旧/新 kernel＋drain MAPE | 新版最大 kernel 相对误差 |
|---|---:|---:|---:|---:|
| 原十六形状开发回放 | 440 | 10.47% / **0.365%** | 3.59% / **2.26%** | 16.33% |
| 新八形状 | 220 | 10.04% / **0.366%** | 4.10% / **2.07%** | 17.44% |

MAPE 是逐候选绝对百分比误差的算术平均；输入占比会使总任务误差小于 kernel 误差，所以两者分别报告。所有 660 个被检查配置的新版输入周期与参考模型完全一致，Flit-hop 一致，credit 上界符合声明。原十六形状的新首选命中为 16/16。

以上误差参照**同配置完整 Python evaluator**，并非 RTL 或芯片误差。预测网络整包逐跳，近似真实 wormhole；仍存在 kernel 偏差，也不构成严格周期上界。实际 RTL 的窄输入写口和注册 NI 差异继续单独报告。资源基线只是排序得分，不给它计算绝对周期 MAPE。

每个新形状先完成全部策略的排序，再分别执行前四个候选，最后补齐目录参考；没有向预测器提供最优标签。新模型每形状对所有可行计划执行包级推演（通常 28 次），这些调用、推演周期和排序时间都记录，不能称为仅做“一次模拟”的零成本选择器。

## 数据与复现

- [summary.json](summary.json)、[quality.csv](quality.csv)：开发/新数据的预算前缀、命中、退化和实际耗时。
- [phase_errors.csv](phase_errors.csv)、[kernel_errors.json](kernel_errors.json)：逐配置输入/kernel/总周期误差。
- [records.json.gz](records.json.gz)：新八形状的全部计划、数值、网络/端点数据，以及旧十六形状的回放结果。
- [provenance.json](provenance.json)、[源码快照](source_snapshot.tar.gz)：冻结提交、完整源码/协议指纹和可重建代码。
- [首选方案对照图](figure/fresh_mapping.svg)、[PDF](figure/fresh_mapping.pdf)、[PNG](figure/fresh_mapping.png)。
- [新形状周期误差图](figure/fresh_prediction_error.svg)、[PDF](figure/fresh_prediction_error.pdf)、[PNG](figure/fresh_prediction_error.png)、[图形证据](figure/evidence.json)。

图注：第一图展示八个新形状在预算 1 下的周期/强基线比，越低越快；逐形状显示全部结果。第二图展示新形状 220 个可行配置的带符号预测误差，包含效果相同的控制配置，不把它们当作独立随机样本。无置信区间或显著性声明。图已渲染检查，图例位于数据区外，SVG 文本可编辑。

运行方法与完整口径见 [v0.4 合同](../../../docs/spec/bounded_prediction_v0.4.md)。导出和绘图由 `tools/experiments/export_prediction_results.py` 完成：

```bash
python tools/experiments/export_prediction_results.py \
  --summary dump/prediction_v04/merged/summary.json \
  --replay dump/prediction_v04/development/replay.json \
  --inputs dump/prediction_v04/fresh --output-dir dump/prediction_v04/published
```
