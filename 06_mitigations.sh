#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/lib_infer.sh"

OUT="$RESULTS_DIR/mitigations"
mkdir -p "$OUT"

THREADS=4
N_PREDICT=128
PROMPT_LEN=256

echo "============================================"
echo " Experiment 5 — Mitigations"
echo "============================================"

trap 'clear_cgroup_limits; echo "  [trap] cgroup limits cleared"' EXIT

setup_cgroup

# ── Mitigation 1: CPU affinity under 50% quota ───────────────────────────────
echo ""
echo "[1/2] CPU affinity under 50% quota"

apply_cpu_quota 50
echo $$ | sudo tee "$CGROUP_PATH/cgroup.procs" >/dev/null 2>&1 || true

echo "  BEFORE (50% quota, no affinity)..."
start_server "$THREADS"
run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null
{
    echo "# mitigation=cpu_affinity state=before cpu_quota=50%"
    echo "# date=$(date -Iseconds)"
    run_reps "$REPS" "$THREADS" "$PROMPT_LEN" "$N_PREDICT"
} | tee "$OUT/cpu_quota50_no_affinity.txt"
stop_server

echo "  AFTER (50% quota + taskset 0-1)..."
# Pin to first 2 physical cores only
start_server "$THREADS"
run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null
{
    echo "# mitigation=cpu_affinity state=after cpu_quota=50% affinity=0-1"
    echo "# date=$(date -Iseconds)"
    # Run reps with taskset pinning
    echo "# rep ttft_ms tok_per_sec total_ms"
    for i in $(seq 1 "$REPS"); do
        read -r ttft tok total <<< "$(taskset -c 0-1 bash -c '
            source /home/taylor/llm-os-project/scripts/lib_infer.sh
            run_inference 4 256 128
        ')"
        echo "$i $ttft $tok $total"
    done
} | tee "$OUT/cpu_quota50_with_affinity.txt"
stop_server

echo $$ | sudo tee /sys/fs/cgroup/cgroup.procs >/dev/null 2>&1 || true
clear_cgroup_limits
sleep 2

# ── Mitigation 2: Concurrency limiting ───────────────────────────────────────
echo ""
echo "[2/2] Concurrency limiting"

start_server "$THREADS"
run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null

echo "  BEFORE (4 concurrent requests)..."
{
    echo "# mitigation=queue_limit state=before concurrency=4"
    echo "# date=$(date -Iseconds)"
    echo "# rep ttft_ms tok_per_sec total_ms"
    for rep in $(seq 1 "$REPS"); do
        pids=(); tmpfiles=()
        for i in $(seq 1 4); do
            tmp=$(mktemp); tmpfiles+=("$tmp")
            ( read -r ttft tok total <<< "$(run_inference "$THREADS" "$PROMPT_LEN" "$N_PREDICT")"
              echo "$rep.$i $ttft $tok $total" ) > "$tmp" &
            pids+=($!)
        done
        for pid in "${pids[@]}"; do wait "$pid"; done
        cat "${tmpfiles[@]}"; rm -f "${tmpfiles[@]}"
    done
} | tee "$OUT/conc4_no_limit.txt"

echo "  AFTER (serialized, queue=1)..."
{
    echo "# mitigation=queue_limit state=after concurrency=1"
    echo "# date=$(date -Iseconds)"
    echo "# rep ttft_ms tok_per_sec total_ms"
    for i in $(seq 1 "$((REPS * 4))"); do
        read -r ttft tok total <<< "$(run_inference "$THREADS" "$PROMPT_LEN" "$N_PREDICT")"
        echo "$i $ttft $tok $total"
    done
} | tee "$OUT/conc4_serialized.txt"
stop_server

echo ""
echo "============================================"
echo " Mitigations complete!"
echo " Next: python3 07_analyze.py"
echo "============================================"
