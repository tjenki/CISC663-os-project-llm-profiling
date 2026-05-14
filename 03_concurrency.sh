#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/scripts/lib_infer.sh"

OUT="$RESULTS_DIR/concurrency"
mkdir -p "$OUT"

THREADS=4
N_PREDICT=128
PROMPT_LEN=256
CONCURRENCY_LEVELS=(1 2 4)

echo "============================================"
echo " Experiment 2 — Concurrency"
echo "============================================"

for conc in "${CONCURRENCY_LEVELS[@]}"; do
    echo ""
    echo "--- concurrency=$conc ---"
    start_server "$THREADS"
    run_inference "$THREADS" "$PROMPT_LEN" 32 > /dev/null  # warmup

    OUTF="$OUT/concurrency_${conc}.txt"
    {
        echo "# experiment=concurrency conc=$conc threads=$THREADS prompt_len=$PROMPT_LEN n_predict=$N_PREDICT reps=$REPS"
        echo "# date=$(date -Iseconds)"
        echo "# rep ttft_ms tok_per_sec total_ms"

        for rep in $(seq 1 "$REPS"); do
            # Fire $conc requests in parallel
            local_pids=(); tmpfiles=()
            for i in $(seq 1 "$conc"); do
                tmp=$(mktemp)
                tmpfiles+=("$tmp")
                ( read -r ttft tok total <<< "$(run_inference "$THREADS" "$PROMPT_LEN" "$N_PREDICT")"
                  echo "$rep.$i $ttft $tok $total" ) > "$tmp" &
                local_pids+=($!)
            done
            for pid in "${local_pids[@]}"; do wait "$pid"; done
            cat "${tmpfiles[@]}"
            rm -f "${tmpfiles[@]}"
        done

        echo "# vmstat_ctxt=$(grep ctxt /proc/stat | awk '{print $2}')"
    } | tee "$OUTF"

    stop_server
    sleep 2
done

echo ""
echo "============================================"
echo " Concurrency complete!"
echo " Next: sudo bash 04_cpu_throttle.sh"
echo "============================================"
