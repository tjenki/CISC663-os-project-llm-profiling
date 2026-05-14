#!/usr/bin/env bash
# =============================================================================
# 01_setup.sh — Install deps, build llama.cpp, download TinyLlama model
# Run once on a fresh Ubuntu VM. Safe to re-run.
# =============================================================================
set -euo pipefail

PROJECT_DIR="$HOME/llm-os-project"
LLAMA_DIR="$HOME/llama.cpp"
MODEL_DIR="$PROJECT_DIR/models"
MODEL_FILE="tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf"
MODEL_URL="https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/$MODEL_FILE"

echo "============================================"
echo " LLM OS Project — Setup"
echo "============================================"

# ── 1. System packages ───────────────────────────────────────────────────────
echo "[1/5] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    build-essential cmake git curl wget \
    python3 python3-pip \
    sysstat \
    linux-tools-common linux-tools-generic \
    util-linux \
    jq \
    2>/dev/null || true

# python deps for analysis/report
pip3 install --user matplotlib pandas numpy scipy 2>/dev/null || true

# ── 2. Clone & build llama.cpp ───────────────────────────────────────────────
echo "[2/5] Building llama.cpp..."
if [ ! -d "$LLAMA_DIR" ]; then
    git clone https://github.com/ggerganov/llama.cpp "$LLAMA_DIR"
fi
cd "$LLAMA_DIR"
git pull --ff-only 2>/dev/null || true
cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
cmake --build build --config Release -j"$(nproc)"

echo "  ✓ llama.cpp built at $LLAMA_DIR/build/bin/llama-cli"

# ── 3. Download model ────────────────────────────────────────────────────────
echo "[3/5] Downloading TinyLlama Q4_K_M (~700 MB)..."
mkdir -p "$MODEL_DIR"
if [ ! -f "$MODEL_DIR/$MODEL_FILE" ]; then
    wget -q --show-progress -O "$MODEL_DIR/$MODEL_FILE" "$MODEL_URL"
else
    echo "  ✓ Model already present, skipping download."
fi

# ── 4. Verify cgroup v2 ──────────────────────────────────────────────────────
echo "[4/5] Checking cgroup v2..."
if mount | grep -q "cgroup2"; then
    echo "  ✓ cgroup v2 is active"
else
    echo "  ⚠ cgroup v2 not detected. Ensure your VM uses cgroup v2:"
    echo "    Add 'systemd.unified_cgroup_hierarchy=1' to GRUB_CMDLINE_LINUX in /etc/default/grub"
    echo "    Then: sudo update-grub && sudo reboot"
fi

# Check that we can write to our own cgroup (rootless cgroup delegation)
if [ -w "/sys/fs/cgroup/user.slice" ] || sudo test -d /sys/fs/cgroup/system.slice 2>/dev/null; then
    echo "  ✓ cgroup access looks good"
fi

# ── 5. Write config ──────────────────────────────────────────────────────────
echo "[5/5] Writing project config..."
cat > "$PROJECT_DIR/config.env" << EOF
LLAMA_CLI=$LLAMA_DIR/build/bin/llama-cli
MODEL=$MODEL_DIR/$MODEL_FILE
RESULTS_DIR=$PROJECT_DIR/results
REPORT_DIR=$PROJECT_DIR/report
REPS=5
EOF

echo ""
echo "============================================"
echo " Setup complete!"
echo " Next step:  bash 02_baseline.sh"
echo "============================================"
