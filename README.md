# OS Project Proposal  
**Systematic Profiling of an LLM Inference Server**

**Goal**

The goal of this project is to understand how operating system resource constraints affect the performance of a CPU-based LLM inference server. The focus is on connecting user-visible metrics (latency and throughput) to OS-level mechanisms such as scheduling and memory behavior.

**Workload**

I will run a local LLM inference server using llama.cpp inside an Ubuntu virtual machine. The workload will consist of repeated inference requests with fixed prompts and generation lengths to ensure reproducibility.

**Metrics**

Application-level:

- Time to First Token (TTFT)
- Tokens per second (throughput)
- p95/p99 latency

System-level:

- Memory usage (RSS)
- Page faults
- CPU utilization and context switches

**Experiment Variables**

I will vary at least three parameters:

- Concurrency (single vs multiple requests)
- Thread count
- Prompt length

**Experiments**
1. CPU quota experiment
Apply cgroup v2 CPU limits and measure how throttling affects latency and throughput.
2. Memory limit experiment
Apply cgroup v2 memory limits and observe page faults, reclaim behavior, and performance degradation.

**Methodology (Evidence Plan)**

Each experiment will include:

- Application-level metrics (latency percentiles, throughput)
- OS-level metrics (cgroup stats, pidstat, /proc data)

I will also include at least one exclusion check (e.g., verifying that I/O is not the bottleneck using stable iostat results).

**Hypothesis**
- CPU throttling will increase tail latency (p99, TTFT) due to scheduling delays
- Memory limits will increase page faults and reduce performance
- Higher concurrency will increase contention and degrade latency

**Mitigations**
- System-level: model warmup, CPU affinity
- Application-level: limiting concurrency or prompt size

Each mitigation will be validated with before/after comparisons.

**Timeline**
- Week 1–2: Setup and baseline measurements
- Week 3–5: Run experiments
- Week 6–7: Analyze results
- Week 8–9: Test mitigations
- Week 10: Final report
