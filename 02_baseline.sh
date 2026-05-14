#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/lib_infer.sh"

OUT="$RESULTS_DIR/baseline"
mkdir -p "$OUT"

echo "============================================"
echo " Experiment 1 — Baseline Sweep"
echo "============================================"

THREADS=(1 2 4 8)
PROMPT_LENS=(64 256 512)
N_PREDICT=128

# I/O exclusion check
iostat -x 2 3 > "$OUT/iostat_check.txt" 2>/dev/null &

# Thread sweep
echo ""
echo "[1/2] Thread sweep: prompt=256tok, reps=$REPS"
for t in "${THREADS[@]}"; do
    echo ""
    echo "  threads=$t ..."
    start_server "$t"
    run_inference "$t" 256 32 > /dev/null  # warmup
    OUTF="$OUT/threads_${t}_prompt256.txt"
    {
        echo "# experiment=thread_sweep threads=$t prompt_len=256 n_predict=$N_PREDICT reps=$REPS"
        echo "# date=$(date -Iseconds)"
        run_reps "$REPS" "$t" 256 "$N_PREDICT"
    } | tee "$OUTF"
    stop_server
    sleep 2
done

# Prompt length sweep
echo ""
echo "[2/2] Prompt sweep: threads=4, reps=$REPS"
start_server 4
run_inference 4 256 32 > /dev/null  # warmup
for p in "${PROMPT_LENS[@]}"; do
    echo ""
    echo "  prompt_len=$p tokens..."
    OUTF="$OUT/threads4_prompt${p}.txt"
    {
        echo "# experiment=prompt_sweep threads=4 prompt_len=$p n_predict=$N_PREDICT reps=$REPS"
        echo "# date=$(date -Iseconds)"
        run_reps "$REPS" 4 "$p" "$N_PREDICT"
    } | tee "$OUTF"
done
stop_server

wait
echo ""
echo "============================================"
echo " Baseline complete! Results in $OUT/"
echo " Next: bash 03_concurrency.sh"
echo "============================================"
