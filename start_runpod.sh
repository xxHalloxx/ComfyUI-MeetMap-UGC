#!/usr/bin/env bash
# Convenience wrapper for standard RunPod ComfyUI images; no version pin is enforced.
set -euo pipefail
exec "$(dirname "$0")/install_runpod.sh" "$@"
