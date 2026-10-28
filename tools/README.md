# 工具

- `check_layout.py`：检查本地 Markdown 链接和 VS Code 工作区路径；`--tracked` 只检查 Git 索引中的文件及其链接目标，可用于验证公开仓库的完整性。
- `experiments/mapper_sweep.py`：批量生成并执行 Mapper 计划，枚举 ROW/K、`k_split` 和 worker/Root 放置，汇总 NoC 周期、hop、FIFO 占用，并对照一阶 `cost_score`（MAC/4 + 包数）给出校准结果；可输出 JSON/CSV。`--shapes` 支持 `suite`、`frontier`、`half`、`extremes`、`aspect` 预设或 `MxK` 列表；大 K 的 `--k-splits all` 自动降为 1/8、1/4、1/2、3/4、7/8 采样。它比较通信调度，不代表完整 GEMV 延迟。
- `experiments/`：实验说明及后续批量运行、统计和绘图脚本。
- `rtl/run_tile_rtl.py`：编译单 Tile RTL、检查 SRAM 综合资源、与 Python 逐边沿比较，并保存 CSV/波形；完整运行需要 Icarus Verilog、Verilator 和 Yosys。
- `experiments/timed_tile_system.py`：验证逐请求 Tile 与公式参考在全部 24 个形状上的系统基线一致性，并比较显式端口配置的影响；原压力脚本也支持 `--compute-model tile --tile-config FILE`。

实验读取 `ini/` 的配置和 `data/workloads/` 的数据，把原始输出写入 `dump/`，精选结果写入 `data/results/`。
