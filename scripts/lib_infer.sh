#!/usr/bin/env bash
source "/home/taylor/llm-os-project/config.env"

LLAMA_SERVER="/home/taylor/llama.cpp/build/bin/llama-server"
SERVER_PORT=8421
SERVER_PID=""

PROMPT_64="Explain the difference between a process and a thread in an operating system."
PROMPT_256="You are a computer science professor. Write a detailed explanation of how virtual memory works in modern operating systems, including page tables, TLB, and the role of the MMU in address translation. Include examples of page faults and how the OS handles them."
PROMPT_512="You are a systems programming expert. Write a comprehensive tutorial on CPU scheduling algorithms used in modern operating systems. Cover FCFS, SJF, Round Robin, and CFS. For each, explain the algorithm, advantages, disadvantages, and how it handles IO-bound vs CPU-bound processes. Include pseudo-code and examples."

get_prompt() {
    case "$1" in
        64)  echo "$PROMPT_64"  ;;
        256) echo "$PROMPT_256" ;;
        512) echo "$PROMPT_512" ;;
        *)   echo "$PROMPT_256" ;;
    esac
}

start_server() {
    local threads="${1:-4}"
    echo "  [server] Starting on port $SERVER_PORT (threads=$threads)..."
    "$LLAMA_SERVER" -m "$MODEL" -t "$threads" --port "$SERVER_PORT" --log-disable -s 42 \
        > /tmp/llama_server_root.log 2>&1 &
    SERVER_PID=$!
    local tries=0
    until curl -sf "http://127.0.0.1:$SERVER_PORT/health" | grep -q "ok"; do
        sleep 1; tries=$((tries+1))
        if [ $tries -gt 30 ]; then echo "Server failed to start:"; cat /tmp/llama_server_root.log; exit 1; fi
    done
    echo "  [server] Ready (pid=$SERVER_PID)"
}

stop_server() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null; wait "$SERVER_PID" 2>/dev/null
        SERVER_PID=""; echo "  [server] Stopped"
    fi
}

run_inference() {
    local threads="$1" prompt_len="$2" n_predict="${3:-128}"
    local prompt; prompt=$(get_prompt "$prompt_len")

    local payload; payload=$(python3 -c "
import json, sys
prompt = sys.argv[1]
n = int(sys.argv[2])
print(json.dumps({'prompt': prompt, 'n_predict': n, 'seed': 42, 'temperature': 0.7, 'ignore_eos': True}))
" "$prompt" "$n_predict")

    local response; response=$(curl -sf http://127.0.0.1:$SERVER_PORT/completion \
        -H "Content-Type: application/json" -d "$payload" 2>/dev/null)

    [ -z "$response" ] && echo "0 0 0" && return

    python3 -c "
import json, sys
data = json.loads(sys.argv[1])
t = data.get('timings', {})
ttft  = t.get('prompt_ms', 0)
toks  = t.get('predicted_per_second', 0)
total = t.get('prompt_ms', 0) + t.get('predicted_ms', 0)
print(f'{ttft:.2f} {toks:.2f} {total:.2f}')
" "$response"
}

run_reps() {
    local reps="$1" threads="$2" prompt_len="$3" n_predict="${4:-128}"
    local ttfts=() toks=() totals=()
    echo "# rep ttft_ms tok_per_sec total_ms"
    for i in $(seq 1 "$reps"); do
        read -r ttft tok total <<< "$(run_inference "$threads" "$prompt_len" "$n_predict")"
        echo "$i $ttft $tok $total"
        ttfts+=("$ttft"); toks+=("$tok"); totals+=("$total")
    done
    python3 - "${ttfts[@]}" "${toks[@]}" "${totals[@]}" "$reps" << 'PYEOF'
import sys, numpy as np
args = sys.argv[1:]
reps = int(args[-1])
ttfts  = [float(x) for x in args[:reps]]
toks   = [float(x) for x in args[reps:2*reps]]
totals = [float(x) for x in args[2*reps:3*reps]]
def st(a, name):
    a = np.array(a)
    print(f"# {name}: mean={a.mean():.2f} p50={np.percentile(a,50):.2f} p95={np.percentile(a,95):.2f} p99={np.percentile(a,99):.2f} stdev={a.std():.2f}")
st(ttfts,"ttft_ms"); st(toks,"tokens_per_sec"); st(totals,"total_ms")
PYEOF
}

CGROUP_NAME="llmexp"
CGROUP_PATH="/sys/fs/cgroup/$CGROUP_NAME"

setup_cgroup() { sudo mkdir -p "$CGROUP_PATH"; echo "+cpu +memory" | sudo tee /sys/fs/cgroup/cgroup.subtree_control >/dev/null 2>&1 || true; }

apply_cpu_quota() {
    local pct="$1" period=100000
    setup_cgroup
    echo "$(( period * pct / 100 )) $period" | sudo tee "$CGROUP_PATH/cpu.max" >/dev/null
    echo "  [cgroup] CPU quota: $pct%"
}

apply_mem_limit() {
    local mb="$1"; setup_cgroup
    echo "$(( mb * 1024 * 1024 ))" | sudo tee "$CGROUP_PATH/memory.max" >/dev/null
    echo "  [cgroup] Memory limit: ${mb}MB"
}

clear_cgroup_limits() {
    [ -d "$CGROUP_PATH" ] || return
    echo "max" | sudo tee "$CGROUP_PATH/cpu.max"    >/dev/null 2>&1 || true
    echo "max" | sudo tee "$CGROUP_PATH/memory.max" >/dev/null 2>&1 || true
}
