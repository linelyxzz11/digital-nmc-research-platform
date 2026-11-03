# 三台电脑运行 64 Tile 主实验

112 项旧主实验已完成，以下分片用于复现。新增自动 mapper 的预算/holdout 评估见 [v0.3](spec/mapping_search_v0.3.md)，可以按完整形状分片；不需要为“完成旧实验”重新分派 38/37/37 项。

模型和 Mapper 共享同一份代码。各电脑独立克隆同一 GitHub 仓库、检出完全相同的 commit，运行不同分片，再把结果目录交回一台电脑汇总。单个案例仍由一台电脑完成，不跨机器同步每个仿真周期。

## 先统一版本

使用 Python 3.12 或 3.13 和 Git。64 Tile 运行入口只使用 Python 标准库；运行模型实验无需安装 RTL 工具。RTL 回归由本机和 CI 单独执行。

仓库：`https://github.com/linelyxzz11/digital-nmc-research-platform.git`。本阶段分支为 `sim/scalable-mesh-experiments`。在三台电脑各自的仓库目录中，先执行：

```bash
git fetch origin
git switch sim/scalable-mesh-experiments
git pull --ff-only
git rev-parse HEAD
```

三台电脑打印的完整 commit 必须一致。运行中不更新代码。建议使用虚拟环境；下列 `python` 指各自选定的解释器。Windows 可用 `py -3.12` 替换。

不要分别重新生成已发布清单。直接使用 [smoke.json](../ini/scalable/smoke.json) 和 [main.json](../ini/scalable/main.json)，它们含所有任务、分片、模型源码哈希和每个计划的哈希。文件内容按 UTF-8/LF 标准化校验，允许不同操作系统路径。

## 先跑 12 项冒烟

三台电脑分别执行对应的一行：

```bash
# 电脑 A：分片 0
python tools/experiments/scalable_experiments.py run --manifest ini/scalable/smoke.json --shard 0 --machine pc-a --output-dir dump/smoke/pc-a
# 电脑 B：分片 1
python tools/experiments/scalable_experiments.py run --manifest ini/scalable/smoke.json --shard 1 --machine pc-b --output-dir dump/smoke/pc-b
# 电脑 C：分片 2
python tools/experiments/scalable_experiments.py run --manifest ini/scalable/smoke.json --shard 2 --machine pc-c --output-dir dump/smoke/pc-c
```

把三个目录汇集为同一台电脑上的 `dump/smoke/pc-a`、`pc-b`、`pc-c`，然后执行：

```bash
python tools/experiments/scalable_experiments.py merge --manifest ini/scalable/smoke.json --inputs dump/smoke/pc-a dump/smoke/pc-b dump/smoke/pc-c --output-dir dump/smoke/merged
```

只有 12 项完整、没有重复、版本匹配且数值一致时才生成 `complete=true` 的汇总。

## 主实验：112 项

将上述 `smoke.json` 换为 `main.json`，输出目录换为 `dump/main/pc-a` 等。三分片分别为 38、37、37 项，计算量不保证完全相等。

```bash
# A / B / C 分别使用 --shard 0 / 1 / 2，并设置各自的输出目录
python tools/experiments/scalable_experiments.py run --manifest ini/scalable/main.json --shard 0 --machine pc-a --output-dir dump/main/pc-a
```

结果收齐后：

```bash
python tools/experiments/scalable_experiments.py merge --manifest ini/scalable/main.json --inputs dump/main/pc-a dump/main/pc-b dump/main/pc-c --output-dir dump/main/merged
```

每项保存一个带校验哈希的 `.result.json`。CTRL-C 或断电后，重新执行同一命令即可：已完成且校验通过的项目跳过；未完成的临时文件不当成结果。`--max-jobs 2` 可先执行两项，再去掉该参数继续。错误项目会保留错误记录，后续重新运行会重试；源码改变后须使用重新发布的清单和新输出目录。

合并默认拒绝：清单/源码不符、Git commit 不一致、损坏文件、重复案例、漏项、失败案例或不同映射数值不一致。预先标记的容量/切分不可行案例会保留，不能在合并时丢掉。输出是 `summary.json` 和 `results.csv`，可直接用于后续表格和图。

原始目录留在 `dump/`，无需提交大量日志和临时文件。组员只交换代码 commit、清单和结果目录；Native UniNDP 复现使用单独入口及单独硬件口径，不混入本清单的同硬件加速比。
