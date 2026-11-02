# 从独立克隆复现

```powershell
git clone --branch sim/gemv-8x8-validation https://github.com/linelyxzz11/digital-nmc-research-platform.git
Set-Location digital-nmc-research-platform
python -m venv .venv
$py=(Resolve-Path .venv/Scripts/python.exe).Path
& $py -m pip install -r requirements.txt
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
```

所有命令在仓库根目录执行。Linux 使用对应的 `python` 和 `export PYTHONPATH=src`；不能原样复制 PowerShell 变量语法。

## 无需 RTL 工具的发布记录复核与重绘

```powershell
& $py tools/experiments/review_validation_results.py --output-dir dump/review/python
& $py tools/experiments/summarize_validation_campaign.py --input-dir dump/review/python --protocol ini/experiments/validation_20261031/protocol_final.json --output-dir dump/review/python_figures
& $py tools/rtl/review_gemv_results.py --output-dir dump/review/rtl_inputs
```

这些命令复核保存记录/生成输入，不执行新的 Python 周期仿真或 RTL，不重新宣称盲测。RTL 紧凑证据有 Golden/Root 检查、资源摘要和原始文件哈希；完整原始 Flit trace 保留在本地交付，可向提交者索取。

## 重新执行 Python

```powershell
& $py -m pytest -q -p no:cacheprovider -ra
& $py tools/check_layout.py --tracked
& $py tools/experiments/validation_campaign.py run --protocol ini/experiments/validation_20261031/protocol_final.json --output-dir dump/new_python_run
# 所有其他实验结束后，独立做一次预热+五次计时。
& $py tools/experiments/validation_campaign.py time --protocol ini/experiments/validation_20261031/protocol_final.json --output-dir dump/new_python_run
& $py tools/experiments/summarize_validation_campaign.py --input-dir dump/new_python_run --protocol ini/experiments/validation_20261031/protocol_final.json --output-dir dump/new_python_summary
```

协议会核对源码指纹，不得重签旧协议隐藏源码变化。不同代码或新 campaign 必须使用新协议、新目录。历史 Windows/Linux 工具 fingerprint 键格式限制见 Python 报告；不要混合跨版本结果。原 112 项清单依赖历史源码，可在独立 worktree 检出 `8c0c6eeebdba6d81355d360698d679c61484bd20`，按其 `docs/distributed_experiments.zh-CN.md` 运行三个 shard 并严格合并；不要在当前较新模型上关闭清单检查。v0.3 历史复跑使用 `005246acf0a2231a5490a628a0f38c5bc13a9e06`；v0.4 历史入口为 `prediction_validation.py replay/run/merge`，原八形状今天属于历史数据。

## 配置真实 RTL 工具

Linux/现有 WSL：准备 Icarus/vvp、Verilator、Yosys 并记录 `iverilog -V`、`verilator --version`、`yosys -V`。Windows 可用官方 OSS CAD Suite，安装路径不要含空格；本轮固定版本为 2025-10-31，SHA256：`f00c5b44774fa91633b24c766891b6c557e1cc9a65c48779463a6781dc5d5f17`。

```powershell
$tools=Join-Path $PWD 'work/rtl-tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
curl.exe -L --fail 'https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2025-10-31/oss-cad-suite-windows-x64-20251031.exe' -o "$tools/suite.exe"
if ((Get-FileHash "$tools/suite.exe").Hash.ToLower() -ne 'f00c5b44774fa91633b24c766891b6c557e1cc9a65c48779463a6781dc5d5f17') {throw 'archive mismatch'}
Start-Process -FilePath "$tools/suite.exe" -ArgumentList @('-y',"-o$tools/extracted") -WindowStyle Hidden -Wait
. tools/rtl/windows_oss_environment.ps1 -SuiteRoot "$tools/extracted/oss-cad-suite"
```

Windows adapter 需要系统 .NET Framework csc，仅为真实工具设置路径并转发真实输出/exit code。若受限沙箱阻止 Windows 路径 API，应允许正常用户权限执行工具，不得靠强制 skip。适配器仅改当前进程，不改生产 RTL。

## 实际运行 RTL

```powershell
& $py tools/rtl/run_tile_rtl.py --output-dir dump/new_rtl/tile
& $py tools/rtl/run_root_writeback_rtl.py --output-dir dump/new_rtl/root
& $py tools/rtl/run_mesh_rtl.py --output-dir dump/new_rtl/legacy --wave
& $py -m pytest -q -p no:cacheprovider -ra src/tests/test_tile_rtl.py src/tests/test_root_writeback_rtl.py src/tests/test_reduce_rtl.py src/tests/test_mesh_rtl.py src/tests/test_grid_mapper_rtl.py src/tests/test_rtl_gemv_runner.py
foreach ($m in @(161,251)) {
  $k=if($m -eq 161){97}else{127}
  foreach ($policy in @('unindp-compact','noc-v04')) {
    & $py tools/rtl/run_mesh_rtl.py --case-manifest ini/rtl_gemv_20261031.json --cases "gemv_${m}x${k}_${policy}_s20261031" "gemv_${m}x${k}_${policy}_s20261101" --simulation-timeout 1200 --output-dir "dump/new_rtl/core_${m}_${policy}"
    if($LASTEXITCODE -ne 0){throw 'RTL case failed'}
  }
}
& $py tools/rtl/run_mesh_rtl.py --case-manifest ini/rtl_gemv_20261031.json --cases signed_tail_k_high_id signed_tail_row_high_id --output-dir dump/new_rtl/boundary
& $py tools/rtl/summarize_gemv_rtl.py --input-dir dump/new_rtl --output-dir dump/new_rtl_summary
```

以上按五个子目录保存，汇总器可直接读取并重做 packet/Root/资源/来源审计及四组图。`--simulation-timeout` 是进程墙钟限额，不改 RTL 周期或通过标准。独立额外种子用于正确性，不作为独立硬件性能样本。`export_gemv_validation.py` 是本地完整交付包生成器，需要本地完整回归/环境元数据；阅读和复现核心实验无需执行它。
