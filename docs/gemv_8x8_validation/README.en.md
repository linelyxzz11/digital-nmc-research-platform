# GEMV on an 8×8 mesh: reproducible validation

Published on the [contributor fork branch](https://github.com/chengjunpeng18-cmd/digital-nmc-research-platform/tree/sim/gemv-8x8-validation). The team upstream is `linelyxzz11/digital-nmc-research-platform`; this delivery has not been merged into upstream and no pull request has been opened.

This branch combines two measured campaigns based on `compiler/bounded-timing-predictor` at `f128f237a5393dbc1fadb8e47278e10d82d81f86`.

- Python: historical reproduction and a frozen 12-shape set (336 attempted, 292 feasible). At exact budget 1, NoC v0.4 has 2 wins / 10 ties / 0 losses against UniNDP-adapted + compact; geometric mean speedup 1.018812×.
- Real 64-Tile RTL: nine existing integration cases, four mappings with two seeds, and two boundary cases passed in the recorded campaign. RTL totals are 2611→2375 cycles for 161×97 and 3380→3218 for 251×127. The kernel itself becomes slower; input-path savings exceed that cost.

See the [Chinese entry point](README.md), [portable reproduction commands](reproduce.zh-CN.md), [current integration checks](integration_validation.zh-CN.md), and the two data directories under `data/results`. Historical measured records, restored records, and newly executed checks are distinguished explicitly.

The physical budget stays at 64 Tiles. Only active workers change (32 vs 16); reduction nodes consume the same budget. Weight/config preload is excluded. RTL is limited to Root/Host0, tid1, one transaction, default hardware, M≤256. Production RTL is unchanged. These are simulation cycles, not silicon/PPA or full-model accuracy results.
