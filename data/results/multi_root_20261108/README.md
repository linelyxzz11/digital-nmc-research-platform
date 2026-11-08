# 多 Root 分支交付与实验数据

分支：`codex/sim-multi-root-extension`。基线：`origin/compiler/bounded-timing-predictor`，提交 `f128f237a5393dbc1fadb8e47278e10d82d81f86`。这是在既有 64-Tile 仿真平台上的增量；评审时应与该基线比较。

## 功能与接口

- `map_grid(..., final_roots=(0, 63))` 支持 1/2/4 个最终 Root；第一个须等于 `root_tile`。
- ROW 按完整 Worker 输出行分组；K 使用每 Worker 顺序子任务队列和轮转顺序；二维按完整 ROW 分区分组。K/二维多 Root 仅支持 central 归约。
- `GridPlan.final_root_ranges` 给出各 Root 的输出行范围及来源 mask；Root 本地写回，Host 校验并聚合各 Root Done，只产生一次全局完成。
- 各 Root 的 SRAM 在仿真结束后由软件拼接，用于 INT32 CPU Golden 校验。该拼接不是经 NoC 传回 Host 的数据传输；模型不包含权重首次加载耗时、完整输出回 Host 或 Python Mapper 搜索时间。
- K 任务切换配置周期纳入仿真；相同组的计算 Worker、MAC 次数和硬件时序固定。
- 放置搜索支持预声明 Root 集合、compact/spread 和随机种子，先封存静态评分选择，再执行有限资源周期仿真。Oracle 仅是该候选集合内最小周期，不是全局最优。
- 原有单 Root 计划仍按 v0.2 序列化；ROW/K/二维多 Root 分别使用 v0.3/v0.4/v0.5。旧预测器明确拒绝 K/二维多 Root。
- 当前单事务仍限制 `M <= 256`，各 Worker 还须满足既有 SRAM 容量约束。增加 Root 不会自动扩大可支持的矩阵规模。

多 Root 完整 RTL、K/二维 tree 归约、8/16 Root、4096×4096、PPA 和完整联合 Mapper 不在本分支范围。集中计算与归约比较实验也不包含在本次增量中。

## 文件清单

以下均为仓库相对路径；既有主仓库的其他运行依赖由基线提供。

| 类别 | 文件 |
|---|---|
| Mapper 核心 | `src/compiler/grid_mapper.py`、`multi_root_placement.py`、`bounded_predictor.py`、`mapping_search.py` |
| 系统执行 | `src/simulator/system/scalable.py`、`multi_root.py`、`task_queue.py` |
| 实验入口 | `tools/experiments/multi_root_row.py`、`multi_root_k.py`、`multi_root_2d.py`、`multi_root_placement_search.py` |
| 实验配置 | `ini/scalable/multi_root_row.json`、`multi_root_k.json`、`multi_root_2d.json`、`multi_root_placement_search.json` |
| 测试 | `src/tests/test_multi_root_row.py`、`test_multi_root_k.py`、`test_multi_root_2d.py`、`test_multi_root_placement.py` |
| 执行合同 | `docs/spec/multi_root_row_v0.1.md`、`multi_root_k_v0.1.md`、`multi_root_2d_v0.1.md`、`multi_root_placement_search_v0.1.md` |
| 使用说明 | 根目录 `README.md`、`src/tests/README.md` |
| 精选证据 | 本目录 README，四个子目录的 CSV、summary、provenance；placement 另含 candidates.csv 和 rejected.json |

`scalable.py` 来自 2026-11-01 多 Root 源码快照，不含后续归约比较新增的首末结果写回时刻等统计字段。核心源码从多 Root 源码包导入；放置搜索的代码、配置、测试和规格从 2026-11-07 Oracle 增量包导入。README 仅作合并说明与边界澄清。

## 历史结果与来源

这些是已有实验的精选导出，不代表本次重新完整运行 2160 个候选。每个子目录的 `provenance.json` 保留原结果文件 SHA-256、原 Git 提交及 dirty 状态、原源码/配置哈希和发布文件哈希。发布的 CSV/JSON 统一为 LF 换行，数据值不变；发布文件哈希对应 Git 中的文件字节，原始大 JSON 哈希仍对应本地历史文件。较早的 ROW/K 实验先于最终二维接口，原源码哈希可能与当前合并版不同；不将历史哈希改写成当前哈希。

| 数据集 | 原运行目录 | 数量 | 精选文件 |
|---|---|---:|---|
| ROW | `dump/multi_root_row_20261101` | 36 案例 | [CSV](row/results.csv)、[摘要](row/summary.json)、[来源](row/provenance.json) |
| K | `dump/multi_root_k_20261101` | 36 案例 | [CSV](k/results.csv)、[摘要](k/summary.json)、[来源](k/provenance.json) |
| 二维 | `dump/multi_root_2d_20261101` | 36 案例 | [CSV](2d/results.csv)、[摘要](2d/summary.json)、[来源](2d/provenance.json) |
| 放置搜索 | `dump/multi_root_placement_search_20261102` | 72 组 / 2160 候选 | [组汇总](placement/results.csv)、[候选](placement/candidates.csv)、[摘要](placement/summary.json)、[来源](placement/provenance.json)、[拒绝记录](placement/rejected.json) |

历史放置搜索命中率 38/72（52.78%），平均 regret 5.44%，最坏 28.45%；相对固定 compact/spread 几何平均加速为 1.0520× / 1.1324×。并非每组都优于固定放置。两种服务 profile 中的 pressure 仅用于 Root 瓶颈敏感性实验。

## 复现

在本分支仓库根目录、已安装 `requirements.txt` 的 Python 环境中运行：

```text
python -m pytest -q -p no:cacheprovider src/tests
python tools/experiments/multi_root_row.py --output-dir dump/reproduce_row
python tools/experiments/multi_root_k.py --output-dir dump/reproduce_k
python tools/experiments/multi_root_2d.py --output-dir dump/reproduce_2d
python tools/experiments/multi_root_placement_search.py --max-groups 1 --output-dir dump/reproduce_search_smoke
python tools/experiments/multi_root_placement_search.py --jobs 4 --output-dir dump/reproduce_search_full
```

所有大型原始输出写入已忽略的 `dump/`。下载整个分支即可获得 final Root 接口和执行依赖；不要只复制 Oracle 阶段的三个 Python 文件。

## 本次验证

导入时核对了 108 个历史案例和 2160 个候选：Golden 均通过、全局完成一次、Root Done 数等于 Root 数；重新计算了 72 组候选最小周期、regret 和两种固定基线加速比。

未修改基线的完整测试为 378 passed、5 skipped、1 failed；迁移后为 **457 passed、5 skipped、1 failed**，其中多 Root 四个测试文件的 **79 项全部通过**，没有新增失败。失败为 `test_pressure_sweep.py::test_cli_exports_results_and_source_provenance`：Windows 输出 `src\hardware.py`，测试期待 `src/hardware.py`。该旧文件未在本分支修改；不能把全工程测试声称为全部通过。5 项跳过均为缺少 RTL 仿真工具的检查，完整原因保存在本地 JUnit 记录。

[机器可读验证记录](verification.json) 保存本次测试计数、重跑案例、源代码哈希及导入包哈希。原始测试 XML 和可审查 Git 补丁保存在本地 `dump/multi_root_preparation/`。

本分支另行重跑 ROW、K、二维各 3 个案例（覆盖 1/2/4 Root），9 个周期结果均与历史 CSV 相同；重跑放置搜索前 3 组、90 个候选，Mapper 选择及 Mapper/Oracle/固定 compact/固定 spread 周期均与历史记录一致。完整 2160 候选未在本次打包时重跑。
