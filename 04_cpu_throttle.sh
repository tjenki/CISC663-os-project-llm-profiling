#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/lib_infer.sh"

OUT="$RESULTS_DIR/cpu_throttle"
mkdir -p "$OUT"

THREADS=4
N_PREDICT=128
PROMPT_LEN=256
CPU_QUOTAS=(25 50 75 100)

echo "============================================"
echo " Experiment 3 — CPU Throttle (cgroup v2)"
echo "============================================"

trap 'clear_cgroup_limits; echo "  [trap] cgroup limits cleared"' EXIT

setup_cgroup

for quota in "${CPU_QUOTAS[@]}"; do
    echo ""
    echo "--- CPU quota: ${quota}% ---"

    if [ "$quota" -eq 100 ]; then
        clear_cgroup_limits
        echo "  [cgroup] No limit (baseline)"
    else
        apply_cpu_quota "$quota"
    fi

    # Move this shell into the cgroup
    echo $$ | sudo tee "$CGROUP_PATH/cgroup.procs" >/dev/null 2>&1 || true

    start_server "$THREADS"
    run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null  # warmup

    # Collect cgroup stats in background
    {
        for _ in $(seq 1 20); do
            echo "=== $(date -Iseconds) ==="
            cat "$CGROUP_PATH/cpu.stat"    2>/dev/null || true
            cat "$CGROUP_PATH/cpu.pressure" 2>/dev/null || true
            sleep 3
        done
    } > "$OUT/cgroup_stats_${quota}pct.txt" &
    CGSTAT_PID=$!

    OUTF="$OUT/cpu_quota_${quota}pct.txt"
    {
        echo "# experiment=cpu_throttle cpu_quota=${quota}pct threads=$THREADS prompt_len=$PROMPT_LEN n_predict=$N_PREDICT reps=$REPS"
        echo "# date=$(date -Iseconds)"
        run_reps "$REPS" "$THREADS" "$PROMPT_LEN" "$N_PREDICT"
    } | tee "$OUTF"

    stop_server
    kill "$CGSTAT_PID" 2>/dev/null; wait "$CGSTAT_PID" 2>/dev/null || true

    # Move back to root cgroup
    echo $$ | sudo tee /sys/fs/cgroup/cgroup.procs >/dev/null 2>&1 || true
    clear_cgroup_limits
    sleep 2
done

echo ""
echo "============================================"
echo " CPU throttle complete!"
echo " Next: sudo bash 05_mem_pressure.sh"
echo "============================================"
