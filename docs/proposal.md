# Project Proposal

## Communication-Aware Mapping and Endpoint Reduction for a Configurable Digital NMC System

**Topic.** This project proposes a configurable digital near-memory computing (NMC) system for small-batch neural-network inference. It focuses on jointly selecting GEMV partitioning, Tile placement, and partial-sum reduction under finite Bank bandwidth, MAC throughput, NoC bandwidth, and FIFO capacity.

**Project status.** This document defines the proposed system and evaluation plan. The figures describe the version 0.1 architecture; they do not represent completed RTL or measured performance. Performance claims will be made only after the experiments and acceptance criteria in Section 4 are completed.

## 1. Introduction

Matrix-vector multiplication (GEMV),

\[
y = Wx, \qquad W \in \mathbb{Z}^{M\times K},\ x \in \mathbb{Z}^{K},
\]

is a major operation in batch-1 neural-network inference and the decode phase of large language models. Conventional processors repeatedly move weights between memory and compute units. Digital NMC reduces this movement by placing simple vector compute units close to memory Banks and keeping weights near those units.

Adding nearby MAC units alone does not guarantee scalable performance. A GEMV can be partitioned by output rows, by the reduction dimension \(K\), or in two dimensions. Output-row partitioning broadcasts the input vector and produces independent outputs. K-dimension partitioning distributes input slices but creates partial sums that must be reduced. Two-dimensional partitioning combines both communication patterns. Consequently, a balanced number of MAC operations can still perform poorly because of Bank conflicts, network hotspots, finite FIFOs, backpressure, or an overloaded reduction root.

The central research question is:

> Under fixed hardware resources, can a communication-aware mapper jointly choose GEMV partitioning, Tile placement, and reduction topology to reduce execution time and network congestion compared with compute-balanced or hop-based mapping?

The project will build an explainable path from workload to hardware:

```text
Workload → Operator IR → Mapper → ISA/Packet trace
         → Golden model / event model / RTL → metrics
```

The course-level outcome is a correct, reproducible multi-Tile NMC prototype and evaluation framework. The research-level outcome is an experimentally tested characterization of when joint mapping and endpoint reduction help, when a simple partition is already sufficient, and where scaling stops. FPGA deployment is optional and is not required for the proposed contribution.

## 2. Related Work

Existing approaches provide important parts of the required stack, but no single selected work provides the proposed combination of a directly connected Mesh NoC, finite-buffer behavior, an executable mapping trace, and an RTL-comparable endpoint reduction design.

| Approach | Relevant contribution | Gap addressed by this project |
|---|---|---|
| **PIMSIM-NN** [1] | Connects a neural-network compiler, an ISA, and a SystemC PIM simulator. | Its main target is crossbar PIM. We use a digital Bank/MAC Tile and require one trace to drive both models and RTL. |
| **UniNDP** [2] | Provides a unified abstraction, instruction-driven simulation, and mapping search for near-DRAM processing. | Its hierarchy does not directly model the proposed finite-FIFO 2D Mesh, backpressure, hotspot formation, and endpoint reduction. |
| **Scalability Limitations of PIM** [3] | Shows with real UPMEM systems that host communication, rearrangement, and reduction can dominate after local kernels accelerate. | We create a modifiable direct-interconnect model and locate the saturation point under controlled Bank and NoC resources. |
| **Allspark** [4] | Jointly considers workload partitioning, scheduling, placement, and NoC behavior for a PIM system. | We study a smaller GEMV-centered search space whose decisions generate an executable trace and can be checked against RTL. |
| **PID-Comm** [5] | Develops collective-communication abstractions and optimized reduction/data movement for commodity PIM DIMMs. | Its virtual hypercube and host/DIMM path are not a physical Mesh NoC. We compare centralized, tree, and grouped endpoint reduction on the same direct Mesh. |

PIMeval/PIMbench [6] additionally motivates systematic digital-PIM benchmarking. BookSim and Ramulator-style models are useful external references for network and memory trends, but they do not replace the project’s shared ISA semantics or RTL checks.

The proposed extension lies at the intersection of these approaches:

1. a communication-aware GEMV mapper that includes Bank service, link contention, FIFO stalls, and reduction dependencies;
2. joint selection of partition, placement, reduction root, and reduction topology;
3. a synthesizable endpoint reduction buffer whose behavior is represented in both the event model and RTL.

Router-internal reduction, runtime remapping, and a full FPGA deployment remain possible later extensions. They are outside the initial claim.

## 3. System Design

### 3.1 NMC Tile microarchitecture

Each Tile contains local memory resources, vector compute, control, and a network endpoint. INT8 operands are multiplied in parallel and accumulated with defined INT32 semantics. Tagged partial sums are matched by transaction and output index at the endpoint reduction buffer. All queue transfers use valid/ready flow control.

![Figure 1. Planned NMC Tile microarchitecture and cycle-level data path.](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig1_nmc_tile_microarchitecture.png)

**Figure 1.** Planned NMC Tile microarchitecture. Commands configure local memory and vector execution; local Banks feed buffered vector MAC lanes; the accumulator and reduction buffer produce tagged results for the endpoint NI. The initial drawing shows four MAC lanes, but lane count, capacity, and latency remain configuration parameters.

The initial command set will support configuration, local load/store, vector add, reduction, GEMV launch, send, wait, and completion. A shared specification will define opcode fields, addresses, tensor shapes, signed integer behavior, accumulator width, overflow behavior, transaction IDs, and completion rules before model or RTL implementation.

### 3.2 Multi-Tile organization and communication

Tiles are connected by a finite-buffer 2D Mesh using deterministic XY routing in the baseline. The NI packetizes operands and partial sums. When a downstream FIFO cannot accept a flit, valid/ready backpressure propagates upstream. Reduction is initially performed at Tile endpoints; baseline Routers only route and arbitrate.

![Figure 2. Planned multi-Tile NMC Mesh and GEMV communication patterns.](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig2_multitile_mesh_organization.png)

**Figure 2.** Planned multi-Tile NMC organization. Solid orange paths illustrate operand distribution, dashed green paths illustrate hierarchical partial-sum reduction, and the dotted path illustrates backpressure from a full endpoint FIFO. The lower panels contrast output-row and K-dimension partitioning. The 3×3 layout is explanatory; experiments use the configurations defined in Section 4.

For each legal mapping candidate, the mapper chooses:

- output-row, K-dimension, or two-dimensional partitioning;
- weight and task placement on Tiles and Banks;
- communication routes implied by placement;
- centralized, tree, or grouped-tree reduction;
- reduction root and execution order.

The first cost model is

\[
T_{total}=T_{host}+T_{bank}+T_{MAC}+T_{NoC}+T_{reduce}+T_{sync}.
\]

It estimates Bank availability and conflicts, MAC occupancy, packet and flit-hop counts, link contention, FIFO blocking, reduction dependencies, and the slowest Tile’s completion time. The event model then evaluates shortlisted candidates with cycle-level queues and resource constraints.

### 3.3 Unified software and validation path

The mapper emits one versioned ISA and Packet trace. The same trace is consumed by the functional interpreter, event model, RTL testbench, and optional future host program. This prevents the software model and RTL from silently implementing different commands or completion semantics.

The implementation order is:

1. freeze architecture, numeric semantics, ISA, and Packet version 0.1;
2. implement NumPy Golden results and a functional interpreter;
3. generate row, K-dimension, and two-dimensional mapping traces;
4. add Bank, MAC, link, FIFO, arbitration, backpressure, and reduction events;
5. implement Vector MAC, Tile, endpoint NI/reduction, Router, and a 2×2 RTL Mesh;
6. run the predeclared benchmark and baseline matrix.

### 3.4 Team responsibilities

| Role | Main responsibility | Required artifact |
|---|---|---|
| A — architecture lead | Overall design, NoC/RTL, Tile and endpoint reduction, integration, main experiments | Specifications, RTL/testbenches, waveforms, network counters, final integration |
| B — ISA and host | Command encoding, address space, synchronization, completion, errors | ISA table, encoder/decoder tests, command and completion interface |
| C — mapper and workloads | Operator IR, partitioning, placement, reduction plans, workloads | Mapper, executable traces, communication matrices |
| D — models and experiments | Golden model, functional interpreter, event model, result processing | Correctness tests, event model, parameter sweeps, CSV and plots |
| E — project support | Run defined regressions, organize logs and figures, maintain progress records | Regression checklist, result index, meeting and presentation material |

## 4. Experiments

The evaluation is designed to prove correctness first and compare performance second. Figure 3 shows how a selected mapping is turned into a common executable trace and checked across the three backends.

![Figure 3. Mapping, executable trace, and cross-layer validation flow.](https://raw.githubusercontent.com/linelyxzz11/digital-nmc-research-platform/main/docs/figures/fig3_mapping_and_validation_flow.png)

**Figure 3.** Proposed mapping and validation flow. Legal GEMV decompositions are placed on the Mesh and ranked with Bank, NoC, FIFO, and reduction costs. The selected plan emits one trace for the Golden model, event model, and RTL testbench. The selected plan and bars in the diagram are illustrative data structures, not measured results.

### 4.1 Research questions

- **RQ1 — Correctness:** Do the functional model, event trace, and RTL produce identical defined INT32 results and completion events?
- **RQ2 — Mapping quality:** Does joint partition-placement-reduction selection reduce total cycles and congestion compared with simpler heuristics under the same hardware budget?
- **RQ3 — Model fidelity:** Does the event model predict the ordering and cycle trends of RTL mappings on small configurations?
- **RQ4 — Scalability:** At what Tile count does speedup saturate, and is the limiting resource the Bank, MAC array, NoC, FIFO, reduction, or host transfer?

### 4.2 Benchmark design

| Benchmark group | Cases | Purpose |
|---|---|---|
| Hand-checkable correctness | VADD, REDUCE, 2×2 GEMV, 4×4 GEMV | Verify arithmetic, commands, packet fields, ordering, completion, and waveforms by hand. |
| Synthetic GEMV | Square and rectangular \((M,K)\): 128–4096 dimensions, including high-\(M/K\) and high-\(K/M\) cases | Expose compute-balanced, Bank-limited, broadcast-heavy, and reduction-heavy regimes. |
| Small RTL performance set | 16×16, 32×32, 64×64 and asymmetric scaled cases | Compare event-model and RTL cycles without excessive simulation time. |
| Two-layer MLP | Scaled MLP for RTL; 4096→11008→4096 trace for the event model | Exercise consecutive GEMVs, persistent weights, intermediate activations, and synchronization. |
| Llama-class decode shapes | 4096×4096 projection, 11008×4096 up/gate, 4096×11008 down projection; optional fused 12288×4096 QKV | Test realistic batch-1 GEMV aspect ratios without running a full language model. |
| Repeated requests | batch/request groups 1, 4, and 16; cold and weight-resident modes | Separate first-load cost, steady-state execution, reuse, and contention. |

Batch 1 is the core GEMV case. Groups of 4 and 16 initially mean independent GEMV requests sharing the system; tiled GEMM is a later extension and will not be silently treated as identical to GEMV.

### 4.3 Baselines

| Baseline | Comparison purpose |
|---|---|
| NumPy Golden | Arithmetic reference; CPU time is reported separately and is not used as a cycle-equivalent hardware baseline. |
| Single-Tile sequential execution | Establish correctness and the no-network reference. |
| Output-row partition | Standard broadcast-input mapping with independent outputs. |
| K partition + centralized reduction | Standard partial-sum communication baseline. |
| Uniform 2D partition | Conventional load-balanced two-dimensional mapping. |
| Compute-balanced heuristic | Shows the consequence of ignoring communication. |
| Minimum-average-hop placement | Shows the consequence of ignoring hotspots, FIFO stalls, and reduction serialization. |
| Ideal contention-free NoC | Provides an upper bound and separates compute/Bank delay from network contention. |
| Proposed joint mapper | Searches partition, placement, reduction root/topology, and order with the resource-aware cost. |

Mapping policies are compared only under identical data precision, total MAC count, Tile/Bank capacity, FIFO capacity, link width, routing rule, workload, and weight-residency condition. Scaling studies that intentionally change the number of Tiles are reported separately from policy comparisons.

### 4.4 Experimental matrix

The initial sweep uses Tile counts of 1, 4, and 16; MAC lanes per Tile of 1, 4, and 8; FIFO depths of 2, 4, 8, and 16 flits; and multiple Bank-service and link-latency settings. Exhaustive combinations are not required. The team will first use a small factorial subset to locate compute-, Bank-, and network-limited regions, then increase resolution around transition points.

Each experiment records its configuration, workload identifier, random seed where applicable, Git commit, generated trace hash, and result CSV. Kernel-only and end-to-end time are reported separately. Warm-up, weight loading, mapping time, and host transfer are never silently excluded.

### 4.5 Metrics

Primary and diagnostic metrics are:

- total and kernel cycles, speedup, and throughput;
- packets, flits, bytes, and flit-hops;
- maximum and mean link utilization;
- FIFO-full and upstream-stall cycles;
- Bank busy/conflict cycles and MAC utilization;
- reduction waiting time and root load;
- mapper runtime and number of candidates evaluated;
- event-model error relative to RTL for shared small cases.

### 4.6 Proof and acceptance criteria

The project will use the following evidence before claiming that the approach works or is better:

1. **Arithmetic correctness:** all hand-checkable and randomized functional cases must match the NumPy Golden result exactly under the frozen INT8/INT32 and overflow semantics.
2. **Protocol correctness:** under forced backpressure, the RTL must neither lose nor duplicate packets; every issued transaction must complete exactly once or produce a defined error.
3. **Cross-layer consistency:** the functional interpreter, event model, and RTL must agree on instruction order, packet dependencies, and completion for the common small test set.
4. **Model usefulness:** the event model should preserve the RTL ranking of candidate mappings. The initial target is Spearman rank correlation of at least 0.8 and median absolute cycle error no greater than 15% on the declared RTL set; misses will be reported and traced to omitted timing effects.
5. **Optimization evidence:** a performance claim is supported only if the proposed mapper reduces geometric-mean total cycles against the strongest implemented non-oracle baseline on the predeclared communication-stressed set, while all workloads—including regressions—are reported. Flit-hop, hotspot, and stall reductions are explanatory metrics, not substitutes for total-cycle improvement.
6. **Scaling evidence:** Tile-scaling curves must include both kernel and end-to-end cycles and identify the first saturation point with resource counters.

If output-row partitioning is already best in an uncongested case, that result is expected and will be retained. If the joint mapper does not improve total cycles, the project will report the failed hypothesis and use the counters to explain why rather than selecting only favorable workloads.

## 5. Conclusion

This proposal targets a configurable digital NMC system in which GEMV mapping decisions are evaluated together with realistic local-memory and NoC constraints. Its main contribution is not the existence of a MAC-enabled Tile by itself. The contribution to be tested is a transparent, executable connection between partitioning, placement, endpoint reduction, finite-buffer network behavior, and RTL-observable results.

The final project should deliver a frozen ISA and Packet specification, a Golden and functional model, a resource-constrained event model, a multi-Tile RTL baseline, reproducible benchmarks, and a fair comparison of mapping strategies. The project succeeds as a course design when the complete execution path is correct and explainable. It supports a stronger research claim only when the predeclared experiments show where communication-aware joint mapping improves performance and where its benefit ends.

## References

1. X. Wang et al., “PIMSIM-NN: An ISA-based Simulation Framework for Processing-in-Memory Accelerators,” DATE Late Breaking Results, 2024.
2. “UniNDP: A Unified Compilation and Simulation Tool for Near-DRAM Processing Architectures,” HPCA, 2025.
3. “Scalability Limitations of Processing-in-Memory using Real System Evaluations,” POMACS, 2024.
4. “Allspark: Workload Orchestration for Visual Transformers on Processing In-Memory Systems,” 2024.
5. “PID-Comm: A Fast and Flexible Collective Communication Framework for Commodity Processing-in-DIMM Devices,” ISCA, 2024.
6. “Architectural Modeling and Benchmarking for Digital DRAM PIM,” IISWC, 2024.
