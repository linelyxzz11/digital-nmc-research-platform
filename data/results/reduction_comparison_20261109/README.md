# Central / Tree 归约比较交付记录

分支：`codex/sim-reduction-comparison`。基线：`codex/sim-multi-root-extension`，提交 `40ef59a10f4581cd550e7f83f5438dc15621fd0a`。这是在已上传多 Root 版本之上的独立增量，不应直接用整份源码覆盖 `main`。

## 交付功能

- 在既有 8×8 Mesh Python 周期模型中，固定单个最终 Root（Tile 0），比较 Central 与 fan-in=4 的 Tree。
- ROW/K/二维三种切分、4 组共同工作负载，共 12 对 / 24 条执行记录。
- 同一对固定 Worker 位置、MAC Lane、输入、Tile/Root 时序；Tree 从其余物理 Tile 中分配中间 reducer。
- 输出总周期、阶段周期、Tree 加速、输出写回跨度、有效 MAC 数、利用率、链路与队列计数、reducer 数及活跃 Tile 数。
- 保留单 Root 与 1/2/4 Root 的原有执行行为；新增写回观察指标不影响调度。
- 导出 JSON、CSV、摘要和 PNG/SVG，拒绝覆盖非空输出目录。

这项 Central 指“集中归约”，不是“集中式计算 vs NMC”的架构比较。

## 本次重跑结果

2026-11-09 在当前整理源码上完整重跑 24 条记录，全部通过 INT32 Golden，记录值与 2026-11-08 本地原实验逐项一致。不同切分使用共同 M/K 与 Worker 数；ROW 是无跨 K 部分和的退化对照。

| 切分 | 工作负载数 | Tree 胜 / 平 / 负 | Tree 几何平均加速 |
|---|---:|---:|---:|
| ROW | 4 | 0 / 4 / 0 | 1.000× |
| K | 4 | 4 / 0 / 0 | 2.675× |
| 二维 | 4 | 4 / 0 / 0 | 1.491× |

数据与来源：[24 条 CSV](results.csv)、[摘要](summary.json)、[来源与源码哈希](provenance.json)、[验证记录](verification.json)。详细周期与解释见 [实验合同](../../../docs/spec/reduction_comparison_v0.1.md)。

## 适用边界

权重预驻留；结果写入最终 Root SRAM，Host 收到 Done 后完成。没有完整输出经 NoC 返回 Host、权重首次加载或 Python 搜索耗时。Tree 使用更多活跃 reducer Tile，不能声称相同活跃面积或能耗；所有方案固定在同一 64-Tile 物理预算内。

只覆盖 compact、fan-in=4、单最终 Root。没有实现多最终 Root 的 Tree，没有新增 RTL、4096×4096、综合、面积或功耗结果。此次增量不包含集中计算实验、课程作业、外部模拟器副本、Word/HTML 报告、ZIP、虚拟环境和原始 dump。

## 验证与兼容性

归约比较 10 项测试、多 Root 79 项测试，共 **89 项通过**。写回观察测试检查最终 Root 的实际提交事件，排除中间 reducer；多个 Root 同周期写回只计一个全局周期。

本地完整基线：457 passed、5 skipped、1 failed。本地完整整理版：467 passed、5 skipped、1 failed，**没有新增失败**。原失败是 `test_pressure_sweep.py::test_cli_exports_results_and_source_provenance` 的 Windows 路径分隔符问题；5 项跳过原因保存在验证 JSON 中。此分支尚未上传，不能将先前多 Root 分支的线上 463 项通过视为本分支 CI 结果。

## 复现

在完整分支仓库根目录、已安装 `requirements.txt` 的 Python 环境运行：

```text
python tools/experiments/reduction_comparison.py --config ini/experiments/reduction_comparison.json --output-dir dump/reduction_comparison_run
python -m pytest -q -p no:cacheprovider src/tests/test_reduction_comparison.py
python -m pytest -q -p no:cacheprovider src/tests
python tools/check_layout.py --tracked
```

重复实验请使用新的输出目录。原始结果与图表保存在 `dump/`，只将精简证据提交到本目录。来源 JSON 区分实际执行文件字节哈希与标准 LF 哈希，兼容不同平台换行；执行时尚未产生交付提交，因此同时记录基线和执行源码哈希，不伪造一个尚未存在的运行提交。
