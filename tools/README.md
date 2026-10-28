# 工具

- `check_layout.py`：检查本地 Markdown 链接和 VS Code 工作区路径；`--tracked` 只检查 Git 索引中的文件及其链接目标，可用于验证公开仓库的完整性。
- `experiments/mapper_sweep.py`：批量生成并执行 Mapper 计划，枚举 ROW/K、`k_split` 和 worker/Root 放置，汇总 NoC 周期、hop、FIFO 占用，并对照一阶 `cost_score`（MAC/4 + 包数）给出校准结果；可输出 JSON/CSV。`--shapes` 支持 `suite`、`frontier`、`half`、`extremes`、`aspect` 预设或 `MxK` 列表；大 K 的 `--k-splits all` 自动降为 1/8、1/4、1/2、3/4、7/8 采样。它比较通信调度，不代表完整 GEMV 延迟。
- `experiments/`：实验说明及后续批量运行、统计和绘图脚本。
- `rtl/run_tile_rtl.py`：编译单 Tile RTL、检查 SRAM 综合资源、与 Python 逐边沿比较，并保存 CSV/波形；完整运行需要 Icarus Verilog、Verilator 和 Yosys。
- `rtl/run_root_writeback_rtl.py`：验证 Root 128-bit 写口的连续吞吐、四 Bank/mask、原子拒绝、Compute/Host 仲裁及复位；将 Python 有限 Root 的 51 组写请求和 ready 时间线送入实际 RTL，并通过外部口读回 SRAM。
- `experiments/timed_tile_system.py`：验证逐请求 Tile 与公式参考在全部 24 个形状上的系统基线一致性，并比较显式端口配置的影响；原压力脚本也支持 `--compute-model tile --tile-config FILE`。
- `experiments/finite_root_system.py`：固定 Tile，比较真实有限 Root 队列/吞吐/写回的五组配置，不使用人工 ejection gate；同时保存独立手算时间线。
- `experiments/mapping_headroom.py`：对既有有限 Root 数据审计固定 ROW、K、当前 AUTO 和两选一事后上界，报告共同可行集合上的加速比/regret及全套覆盖率。
- `experiments/mapping_candidate_sweep.py`：固定硬件，扫描全部合法 worker/Root 放置与采样 K 切分，核对数值、ACK/Done，输出周期/停顿及有限候选上界。
- `experiments/unindp_native.py`：临时克隆本机 UniNDP 作者仓库，复跑三类 AiM GEMV，保存作者原生 latency、指令/DRAM 计数、依赖与源码版本；不将其称为本项目的同硬件移植。见 [对照口径](../docs/unindp_evaluation.zh-CN.md)。

实验读取 `ini/` 的配置和 `data/workloads/` 的数据，把原始输出写入 `dump/`，精选结果写入 `data/results/`。
