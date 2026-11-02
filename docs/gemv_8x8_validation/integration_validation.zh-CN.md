# 独立分支整合验证（2026-11-02）

目标分支：`sim/gemv-8x8-validation`。创建前已成功 fetch；上游 `compiler/bounded-timing-predictor` 仍为 `f128f237a5393dbc1fadb8e47278e10d82d81f86`，远端当时无同名分支。两个旧 worktree 和旧交付均保留，没有 reset/覆盖旧数据，没有修改 main 或上游分支。

## 本次新工作区实际执行

- 完整 pytest：**389 passed、0 skipped、0 failed**，约 384 秒；包含原五项真实 RTL 入口、原九个 Mesh 集成案例和新增 Runner 测试。见[本次测试日志](integration_pytest.txt)。
- 重新执行四个核心 RTL 映射（seed=20261031）：4/4 通过。两个尺寸分别运行 baseline 与 v0.4，均调用真实 Icarus/vvp，并完成 Verilator lint。逐项核对独立 Golden、Root 写回、Plan/输入 SHA、包类型计数、ACK/Done、各 Tile 观测和模型/RTL 周期，与历史证据完全一致。
- 本次只额外重跑上述一个种子的四个核心案例；第二种子与边界案例保留为历史实测记录，没有将其宣称为此次重跑。
- 发布记录恢复/审计：23 个 Python 形状/硬件记录、10 个 RTL 摘要通过。Python 已重新生成六组图与摘要（12 新形状、336 attempted、292 feasible）。恢复、审计和重绘不等于重新运行历史全量实验。
- 生产 RTL、`src/compiler`、`src/simulator` 未修改；测试 TB 保持此前实际通过版本 SHA `96110cd5890fac7a256f0a7fd73f5b39b3d05962d5fa2cd1f4efa6def03af648`。

| GEMV | Baseline 总周期 | v0.4 总周期 | 本次与历史 |
|---|---:|---:|---|
| 161×97 | 2611 | 2375 | 完全相同 |
| 251×127 | 3380 | 3218 | 完全相同 |

本次数值核对与逐 Tile 观测见[机器可读验证记录](integration_verification.json)。完整本地运行目录含原始 Flit/编译输出，不纳入 Git；可按案例名与 SHA 向提交者索取。

## 整合与发布处理

两个历史来源的 tracked diff、untracked 源文件和两份交付 changed_files/patch 已核对；来源是实际工作区。Python 冻结运行器/汇总器保持原始源文件哈希。没有重复应用补丁。

发布仅将机器路径改为仓库相对引用、补充跨平台复现说明、保留精简证据；文本统一 LF，SVG 清理行末空白，JSON 数值保持不变。历史 provenance 的哈希仍描述原始交付，不将其重新签名为新实验；发布清单另记实际文件 SHA。

必要文件包含 Python 六组和 RTL 四组 PNG/SVG、两轮 CSV/JSON、协议、测试及中文报告。排除工具安装包、虚拟环境、编译产物、波形、冗余大日志、完整 ZIP 和作者仓库副本。

最终提交前检查项目布局、Git diff 空白、全部图片、文件大小及私人路径/令牌模式。清单见[分支文件列表](file_inventory.json)；来源对照见[逐文件整合清单](integration_files.json)。
