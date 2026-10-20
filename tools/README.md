# 工具

- `check_layout.py`：检查本地 Markdown 链接和 VS Code 工作区路径；`--tracked` 只检查 Git 索引中的文件及其链接目标，可用于验证公开仓库的完整性。
- `experiments/`：实验说明及后续批量运行、统计和绘图脚本。

实验读取 `ini/` 的配置和 `data/workloads/` 的数据，把原始输出写入 `dump/`，精选结果写入 `data/results/`。
