#!/usr/bin/env bash
# =============================================================================
#  ros2_network_fixer — quick-start bootstrap (Linux / macOS / WSL2)
#
#  Run this script to install and launch the tool without any manual steps:
#
#    curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/ros2-network-fixer/main/scripts/quickstart.sh | bash
#
#  Or, if you cloned the repo:
#
#    bash scripts/quickstart.sh
# =============================================================================

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
RESET="\033[0m"

info()  { echo -e "  ${GREEN}→${RESET}  $*"; }
warn()  { echo -e "  ${YELLOW}⚠${RESET}  $*"; }
error() { echo -e "  ${RED}✘${RESET}  $*" >&2; }
step()  { echo -e "\n  ${BOLD}$*${RESET}"; }

# ---------------------------------------------------------------------------
step "ROS 2 Cross-Platform Network Fixer — Quick Start"
# ---------------------------------------------------------------------------

# Detect Python 3 (prefer python3, then python)
PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major="${version%%.*}"
        minor="${version##*.}"
        if [ "$major" -ge 3 ] && [ "$minor" -ge 8 ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.8 or newer is required but was not found."
    error "Install it from https://python.org and re-run this script."
    exit 1
fi

info "Using: $($PYTHON --version)"

# ---------------------------------------------------------------------------
# Determine script directory (works for both curl-pipe and repo clone)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$REPO_ROOT/pyproject.toml" ]; then
    step "Installing from local clone..."
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet "$REPO_ROOT"
else
    step "Installing from PyPI..."
    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet ros2-network-fixer
fi

info "Installation complete."

# ---------------------------------------------------------------------------
step "Launching ros2_network_fixer..."
echo ""

"$PYTHON" -m ros2_network_fixer.cli "$@"
