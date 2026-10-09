#!/usr/bin/env bash
# Condenses a full game run log (raw) to the meaningful lines.
#
# The full log is stdout+stderr of proton/wine: thousands of lines like
#   D 138 Load module comctl32.dll ...            (DLL loading)
#   D 168 Exception: Code: C0000005 ...            (FEX runs x86 code)
#   1262.1:...:warn:vkd3d-proton:...does not exist (shader cache miss)
# They carry no information about why the game failed to start and cover up
# what does: the vkd3d feature level rejection, swapchain creation and
# recreation, the DXVK rejection on vkCreateInstance.
#
#   scripts/curate-game-log.sh <raw.log> [out.log]   # defaults to stdout
set -euo pipefail

IN="${1:?usage: curate-game-log.sh <raw.log> [out.log]}"
[ -r "$IN" ] || { echo "no such file: $IN" >&2; exit 1; }

grep -vE \
  -e 'Load module ' \
  -e '^[A-Z] [0-9A-Fa-f]+ (Exception: Code|Handled self-modifying code|Call-ret stack inbalance|Passing through exception|Loaded volatile metadata|Add (image )?SMC interval|We don'"'"'t support modifying)' \
  -e '^I [0-9A-Fa-f]+ FEX:' \
  -e '^(fixme|trace):' \
  "$IN" \
| awk '
    # Shader cache misses: thousands of lines, and the only difference is the
    # pipeline name. Count them and report the total at the end.
    /d3d12_pipeline_library_load_pipeline: Pipeline .* does not exist\.$/ { n++; next }
    { print }
    END {
      if (n)
        printf "# [folded %d lines: d3d12_pipeline_library_load_pipeline ... does not exist]\n", n
    }
  ' \
> "${2:-/dev/stdout}"
