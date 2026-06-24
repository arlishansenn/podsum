#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RESTART_GATEWAY=0

if [[ "${1:-}" == "--restart" ]]; then
  RESTART_GATEWAY=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $(basename "$0") [--restart]" >&2
  exit 2
fi

sync_skill() {
  local source="$1"
  local destination="$2"

  test -f "$source/SKILL.md"
  mkdir -p "$destination"
  rsync -a \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.bak-*' \
    "$source/" "$destination/"
  echo "Installed: $destination"
}

sync_skill \
  "$PROJECT_ROOT/skills/media/transcript-cleaner" \
  "$HERMES_HOME/skills/media/transcript-cleaner"

sync_skill \
  "$PROJECT_ROOT/skills/markdown-to-epub-converter" \
  "$HERMES_HOME/skills/markdown-to-epub-converter"

sync_skill \
  "$PROJECT_ROOT/skills/social-media/hermes-feishu-file-send" \
  "$HERMES_HOME/skills/social-media/hermes-feishu-file-send"

if [[ "$RESTART_GATEWAY" == "1" ]]; then
  "$HOME/.local/bin/hermes" gateway restart
fi

echo "Hermes skills synchronized from $PROJECT_ROOT/skills"
