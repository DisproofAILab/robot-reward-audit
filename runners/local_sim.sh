#!/bin/sh
# Run the simulation scheduler with a host-only LIBERO configuration.
set -eu
: "${LIBERO_PLUS_ROOT:?select the pinned LIBERO-Plus checkout}"
: "${GPT_GEAR_ROOT:?select the pinned GPT-GEAR checkout}"
: "${SIM_PYTHON:?set the simulator Python executable}"
study=$(cd "$(dirname "$0")/.." && pwd)
test "$(git -C "$LIBERO_PLUS_ROOT" rev-parse HEAD)" = 4976dc30028e805ff8094b55501d532c48fec182
test "$(git -C "$GPT_GEAR_ROOT" rev-parse HEAD)" = 77a538afd492b58486741776016578349939c024
git -C "$LIBERO_PLUS_ROOT" diff --quiet HEAD
git -C "$GPT_GEAR_ROOT" diff --quiet HEAD -- src
if [ "$(uname -s)" = Darwin ]; then
    export MUJOCO_GL=cgl
    if [ -n "${MAGICK_HOME:-}" ]; then
        export DYLD_FALLBACK_LIBRARY_PATH="$MAGICK_HOME/lib:/opt/homebrew/lib:/usr/lib"
    fi
    unset PYOPENGL_PLATFORM MUJOCO_EGL_DEVICE_ID
else
    export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYNPUT_BACKEND=dummy
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
export PYTHONPATH="$study/runners:$LIBERO_PLUS_ROOT:$GPT_GEAR_ROOT/src${SIM_EXTRA_PATH:+:$SIM_EXTRA_PATH}"
LIBERO_CONFIG_PATH=$(mktemp -d)
export LIBERO_CONFIG_PATH
trap 'rm "$LIBERO_CONFIG_PATH/config.yaml"; rmdir "$LIBERO_CONFIG_PATH"' EXIT
"$SIM_PYTHON" - "$LIBERO_PLUS_ROOT" "$LIBERO_CONFIG_PATH" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()/"libero/libero"
directory=Path(sys.argv[2])
values=dict(benchmark_root=root,bddl_files=root/"bddl_files",init_states=root/"init_files",assets=root/"assets",datasets=directory)
(directory/"config.yaml").write_text(json.dumps(values,default=str))
PY
cd "$study"
"$SIM_PYTHON" -Bu runners/campaign.py "$@"
