# Published measured results

These are the previously completed measurements, not measurements newly collected by copying them to this branch.
See [the team entry point](../../../docs/gemv_8x8_validation/README.md) for scope, limitations and portable reproduction commands.

PNG/SVG and CSV/JSON are retained. Python records.json.gz contains the measured candidate records and sealed rankings; walltime.json retains all warmup/timed samples. RTL evidence.json.gz contains the ten real execution summaries, plans, Golden/Root checks and SHA256 references to the full local traces.

Large raw Flit traces, VCD, build files, runtime/tool installers, environment files, and original delivery ZIPs are intentionally excluded. Full raw artifacts remain with the contributor and can be requested for review; their numerical results can also be regenerated with the checked-in runner. Source snapshots from the earlier local delivery are not duplicated: this Git branch contains the source. Historical snapshot hashes retained in provenance refer to those local delivery artifacts.
