# 多 Root 放置搜索与 Oracle 实验交付包

本包整理了“多个 random seed、更多 Worker/Root 候选位置、有限候选集 Oracle、最优命中率、固定 compact/spread 加速比与 Mapper regret”阶段新增的代码、配置、测试和结果。

## 1. 新增代码在哪里

- `src/compiler/multi_root_placement.py`：候选位置生成、去重、静态通信量估计和 Mapper 排序。
- `tools/experiments/multi_root_placement_search.py`：批量运行精确周期仿真、寻找有限候选集 Oracle，并导出指标。
- `src/tests/test_multi_root_placement.py`：候选生成、估计器、指标公式、并行一致性和命令行测试。
- `ini/scalable/multi_root_placement_search.json`：8 个 random seed、1/2/4 Root 候选、工作负载与时序配置。

这些文件依赖原工程已有的 Mapper、64-Tile Mesh 仿真器和多 Root ROW/K/2D 实现，所以本包是“新增阶段的独立交付包”，不是可以脱离原工程运行的完整工程副本。

## 2. 结果文件

- `results/results.csv`：72 个公平比较组的汇总结果。
- `results/candidates.csv`：2160 个候选方案的周期与统计数据。
- `results/rejected.json`：被拒绝的候选，本次为 `[]`。
- `docs/spec/multi_root_placement_search_v0.1.md`：完整实验方法、结果和结论。

为了让压缩包便于传输，没有重复打包约 76 MB 的详细 `results.json`。该文件仍保留在原工程：

`dump/multi_root_placement_search_20261102/results.json`

## 3. 主要结果

- 公平比较组：72 组
- 精确仿真候选：2160 个
- Mapper 命中有限候选集 Oracle：38/72，命中率 52.78%
- 平均 regret：5.44%
- 最坏 regret：28.45%
- 相对固定 compact 的几何平均加速：1.052 倍
- 相对固定 spread 的几何平均加速：1.132 倍

这里的 Oracle 是“本次有限候选集合中的最小周期”，并不代表所有可能物理位置中的理论全局最优。

## 4. 在原工程中复现实验

在原工程根目录执行：

```powershell
.venv\Scripts\python.exe tools\experiments\multi_root_placement_search.py `
  --config ini\scalable\multi_root_placement_search.json `
  --output-dir dump\multi_root_placement_search_reproduce `
  --jobs 8
```

完整实验在当前电脑上曾耗时约 31.5 分钟，实际时间会随处理器与并行数变化。

## 5. 验证状态

- 与本阶段及多 Root 相关的专项测试：110 项通过。
- 全工程测试：457 项通过、5 项因缺少 RTL 工具跳过、1 项既有 Windows 路径格式测试失败。
- 该失败位于旧的 `pressure_sweep.py`，不属于本阶段代码，本交付包未修改它。

## 6. 安全说明

制作本包只复制了本地文件，没有执行 `git add`、提交、推送、合并或上传，也没有覆盖原工程文件。
