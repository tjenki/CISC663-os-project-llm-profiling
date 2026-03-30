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
- **Week 6 (Proposal Submission):**  
  Submit project plan on GitHub

- **Week 7–8 (Initial Evidence Collection):**  
  Collect baseline performance data (TTFT, p99 latency, tokens/s).  
  Gather initial system-level metrics using tools like pidstat and /proc.  
  Begin early analysis of system behavior.

- **Week 9 (Core Experiments):**  
  Run controlled experiments by varying concurrency, thread count, and prompt length.  
  Begin CPU quota (cgroup) testing and observe impact on performance.

- **Week 10 (Freeze Milestone):**  
  Finalize key results and identify performance bottlenecks.  
  Complete CPU and memory limit experiments.  
  Define mitigation strategies based on observed behavior.

- **Week 11 (Mitigation + Validation):**  
  Implement system-level and application-level mitigations.  
  Validate improvements using before/after comparisons.

- **Week 12 (Final Defense):**  
  Prepare final report and presentation.  
  Ensure experiments are reproducible and results are clearly explained.
