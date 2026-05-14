#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/lib_infer.sh"

OUT="$RESULTS_DIR/mem_pressure"
mkdir -p "$OUT"

THREADS=4
N_PREDICT=128
PROMPT_LEN=256
MEM_LIMITS_MB=(512 768 0)

echo "============================================"
echo " Experiment 4 — Memory Pressure (cgroup v2)"
echo "============================================"

trap 'clear_cgroup_limits; echo "  [trap] cgroup limits cleared"' EXIT

setup_cgroup

for mem_mb in "${MEM_LIMITS_MB[@]}"; do
    if [ "$mem_mb" -eq 0 ]; then
        LABEL="unlimited"
        clear_cgroup_limits
        echo ""
        echo "--- Memory: unlimited ---"
    else
        LABEL="${mem_mb}MB"
        echo ""
        echo "--- Memory limit: ${mem_mb}MB ---"
        apply_mem_limit "$mem_mb"
        echo "$((mem_mb * 1024 * 1024))" | sudo tee "$CGROUP_PATH/memory.swap.max" >/dev/null 2>&1 || true
    fi

    echo $$ | sudo tee "$CGROUP_PATH/cgroup.procs" >/dev/null 2>&1 || true

    start_server "$THREADS"
    run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null  # warmup

    # Collect memory stats in background
    {
        for _ in $(seq 1 30); do
            echo "=== $(date -Iseconds) ==="
            grep -E "pgfault|pgmajfault|anon|file" "$CGROUP_PATH/memory.stat" 2>/dev/null || true
            cat "$CGROUP_PATH/memory.pressure" 2>/dev/null || true
            echo "vmstat: $(vmstat 1 1 2>/dev/null | tail -1)"
            sleep 2
        done
    } > "$OUT/mem_stats_${LABEL}.txt" &
    MEMSTAT_PID=$!

    OUTF="$OUT/mem_${LABEL}.txt"
    {
        echo "# experiment=mem_pressure mem_limit=$LABEL threads=$THREADS prompt_len=$PROMPT_LEN n_predict=$N_PREDICT reps=$REPS"
        echo "# date=$(date -Iseconds)"
        run_reps "$REPS" "$THREADS" "$PROMPT_LEN" "$N_PREDICT"
        echo "# final memory.stat:"
        grep -E "pgfault|pgmajfault" "$CGROUP_PATH/memory.stat" 2>/dev/null || true
        echo "# memory.pressure:"
        cat "$CGROUP_PATH/memory.pressure" 2>/dev/null || true
    } | tee "$OUTF"

    stop_server
    kill "$MEMSTAT_PID" 2>/dev/null; wait "$MEMSTAT_PID" 2>/dev/null || true

    echo $$ | sudo tee /sys/fs/cgroup/cgroup.procs >/dev/null 2>&1 || true
    clear_cgroup_limits
    sleep 3
done

echo ""
echo "============================================"
echo " Memory pressure complete!"
echo " Next: bash 06_mitigations.sh"
echo "============================================"
