# llm-os-project
OS final project — profiling TinyLlama inference under CPU/memory constraints on Ubuntu VM

The main question I was trying to answer: does cgroup v2 throttling actually show up in TTFT and p99 latency, and can I prove it with OS-level signals (cpu.stat, PSI) rather than just "it feels slower."

---

## what i used

- TinyLlama 1.1B Q4_K_M (~700MB when loaded)
- llama.cpp build b9138, running as `llama-server`
- Ubuntu 24.04 in VirtualBox, 4 vCPUs, cgroup v2
- 5 reps per config, seed=42, 1 warmup discarded each time

**heads up on llama.cpp version:** b9138 completely broke the old llama-cli batch mode. flags like `--no-display-prompt` and `--no-cnv` dont exist anymore — the CLI just drops into interactive chat and never exits. had to switch to `llama-server` + curl which actually gives cleaner JSON timing output anyway (`timings.prompt_ms`, `timings.predicted_per_second`).

---

## prompts

used two fixed prompts. same prompt every rep within an experiment so the KV cache behavior is consistent (and controllable).

**PROMPT_256** — everything except the prompt length sweep:
```
"You are a computer science professor. Write a detailed explanation of how
virtual memory works in modern operating systems, including page tables,
TLB, and the role of the MMU in address translation. Include examples of
page faults and how the OS handles them."
```

**PROMPT_512** — prompt length sweep only:
```
"You are a systems programming expert. Write a comprehensive tutorial on
CPU scheduling algorithms used in modern operating systems. Cover FCFS,
SJF, Round Robin, and CFS. For each, explain the algorithm, advantages,
disadvantages, and how it handles IO-bound vs CPU-bound processes.
Include pseudo-code and examples."
```

---

## how to run

need sudo for the cgroup experiments (04, 05, 06). run in tmux, takes like 2-3 hours total.

```bash
bash 01_setup.sh               # builds llama.cpp, downloads model
bash 02_baseline.sh            # thread sweep + prompt length
bash 03_concurrency.sh         # conc=1/2/4
sudo bash 04_cpu_throttle.sh   # cpu.max 25/50/75/100%
sudo bash 05_mem_pressure.sh   # memory.max 512/768MB/unlimited
sudo bash 06_mitigations.sh    # queue depth + affinity
python3 07_analyze.py          # generates charts + summary.csv
python3 08_report.py           # report skeleton
```

check cgroup v2 is on before running 04/05:
```bash
mount | grep cgroup2
```

---

## files

```
├── 01_setup.sh
├── 02_baseline.sh
├── 03_concurrency.sh
├── 04_cpu_throttle.sh
├── 05_mem_pressure.sh
├── 06_mitigations.sh
├── 07_analyze.py
├── 08_report.py
├── config.env                    # written by 01_setup.sh
├── scripts/lib_infer.sh          # shared functions, sourced by all experiment scripts
├── models/
├── results/
│   ├── baseline/
│   ├── concurrency/
│   ├── concurrency_nocache/      # failed cache isolation attempts, see below
│   ├── cpu_throttle/
│   ├── mem_pressure/
│   ├── mitigations/
│   └── vcpu_comparison/
└── report/                       # charts, summary.csv, final_report.md
```

---

## results

### thread sweep (4 vCPUs, prompt=256tok)

| threads | tok/s | TTFT | p99 |
|---|---|---|---|
| 1 | 17.86 | 56.9ms | 7,248ms |
| 2 | 30.59 | 34.2ms | 4,242ms |
| 4 | **34.14** | **29.5ms** | **3,842ms** |
| 8 | 5.72 | 179ms | 22,928ms |

t=8 falls off a cliff because 8 threads > 4 vCPUs, scheduler starts thrashing. basically set threads = nproc and don't go higher.

---

### vcpu comparison

dropped the VM to 2 vCPUs in VirtualBox settings and reran the sweep.

| threads | 4 vCPU tok/s | 2 vCPU tok/s | delta |
|---|---|---|---|
| 1 | 17.86 | 10.38 | −42% |
| 2 | 30.59 | 17.80 | −42% |
| 4 | **34.14** | 7.15 | **−79%** |

t=4 on 2 vCPUs is slower than t=1 on 4 vCPUs. the VM config matters more than the thread count setting. also tried taskset to pin to physical cores but VirtualBox sits above the guest OS so it doesn't actually help — taskset just pins to guest vCPUs which the hypervisor can still move around.

---

### cpu throttle — main finding

| quota | TTFT | p99 |
|---|---|---|
| 25% | 1,009ms | 96,744ms |
| 50% | 271ms | 21,742ms |
| 75% | 197ms | 10,733ms |
| 100% | **38ms** | **3,999ms** |

25% quota turns a ~4s inference into up to 96s. the kernel parks the process for up to 100ms every period and that stall time stacks directly into TTFT. `cpu.stat throttled_usec` goes up in proportion to the latency increase which is the OS-level proof. PSI `cpu.pressure` also shows real stall time not just slower throughput.

when the thread is parked, new requests queue behind it. so TTFT ends up being queue wait time + prompt eval time, which is why it blows up so much.

raw cgroup stats in `results/cpu_throttle/cgroup_stats_<quota>pct.txt`

---

### concurrency

#### warm cache (main dataset)

reusing PROMPT_256 every rep means the server KV cache kicks in. mean TTFT is low because of cache hits. the peak TTFT spikes are the real signal — those are requests that had to wait.

| conc | peak TTFT | p99 | ctx switches |
|---|---|---|---|
| 1 | 71ms | ~7,400ms | 14,184,186 |
| 2 | 793ms | ~9,053ms | 14,189,776 |
| 4 | **1,513ms** | **~12,866ms** | 14,195,946 |

context switches went up by like 0.08% between conc=1 and conc=4. the bottleneck is just raw CPU time — 4 requests × 4 threads = 16 threads fighting for 4 vCPUs.

#### cold cache attempts (both failed)

tried to isolate actual prompt eval cost by clearing the KV cache. two approaches, both useless:

**attempt 1: restart the server between reps**
turns out server restart = model reloads from disk. TTFT was 5-47 seconds, which is just disk I/O not prompt eval.
```
conc=1 rep1: TTFT=4,837ms  total=72,838ms
conc=1 rep3: TTFT=8,685ms  total=86,288ms
```

**attempt 2: different prompt every rep**
the unique prompts I used tokenized way longer than 256 tokens on TinyLlama's tokenizer. requests were taking 5-8 minutes each.
```
conc=1 rep1: TTFT=1,919ms  total=322,692ms
conc=2 rep3: TTFT=46,395ms  total=482,259ms
```

kept the warm cache data as the primary results. it's actually the more realistic scenario anyway — production servers run continuously and cache prompts.

---

### memory pressure

| limit | TTFT | pgmajfault | PSI some avg10 |
|---|---|---|---|
| unlimited | 30.5ms | 5,435 | 0.00% |
| 768MB | 31.7ms | 5,435 | 0.06% |
| 512MB | 30.4ms | 5,170 | **0.86%** |

TTFT didn't go up at 512MB because the model was already loaded before the limit got applied — pages were already warm. but PSI shows 0.86% real stall time at 512MB vs 0% unlimited. so the pressure was there, just didn't hit TTFT in this scenario. cold start with 512MB active would be a different story.

---

### mitigations

**queue depth = 1 (serialized)**

| config | p99 | peak TTFT | tok/s |
|---|---|---|---|
| conc=4 | 12,866ms | 1,513ms | ~16 |
| queue=1 | **4,059ms** | **98ms** | ~34 |

p99 down 68%, peak TTFT down 94%. the catch is 4 serial requests take ~15.5s total vs ~7.7s concurrent, so throughput takes a hit.

**CPU affinity (taskset)**

| config | p99 |
|---|---|
| 50% quota, no affinity | 10,979ms |
| 50% quota + taskset 0-1 | ~32,395ms |

made things worse. VirtualBox controls the physical core mapping above the guest OS so taskset doesn't actually do anything meaningful here.

---

## metrics

| what | where it comes from |
|---|---|
| TTFT | `timings.prompt_ms` in server JSON |
| tok/s | `timings.predicted_per_second` |
| p99 | computed from 5 reps |
| throttled_usec | `cgroup/cpu.stat` |
| PSI cpu | `cgroup/cpu.pressure` |
| page faults | `cgroup/memory.stat` pgmajfault |
| PSI memory | `cgroup/memory.pressure` |
| context switches | `/proc/stat` |

disk I/O verified as not a bottleneck via `iostat -x` — drops to ~0 after model load. output in `results/baseline/iostat_check.txt`.
