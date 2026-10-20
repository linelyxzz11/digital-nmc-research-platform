# Contributing

## Workflow

1. 先创建或认领 GitHub Issue。
2. 从 `main` 创建短分支，例如 `docs/spec/isa-v0.1`、`simulator/gemv-golden`、`rtl/vector-mac`。
3. 在 Pull Request 中写明问题、改动、验证方法和已知限制。
4. 接口变更同时更新 `docs/spec/`；重要取舍记录到 `docs/spec/design_decisions_v0.1.md`。

## Definition of done

- 功能改动包含能够失败的有效测试；
- RTL 改动附仿真命令和关键波形或自检查结果；
- 实验改动保存配置、随机种子、精简结果和绘图脚本；
- 不提交论文 PDF、作者仓库副本、构建目录和大型输出。
