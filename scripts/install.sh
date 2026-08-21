#!/usr/bin/env bash
# 把仓库同步到部署目录，建立运行环境，装好定时任务。
#
# 只负责自己装上去的东西：不删别人的文件，不代装会影响系统或其他应用的依赖，
# 不在别的进程正在工作时打断它。判据见 docs/adr/0001-install-failure-radius.md。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PODSUM_HOME="${PODSUM_HOME:-$HOME/Library/Application Support/Podsum}"
RECEIPT="$PODSUM_HOME/.podsum-install-receipt"
SKIP_VENV=0; SKIP_NODE=0; SKIP_LAUNCHD=0
installed=""
MIN_PYTHON="3.14"

# 真实文件属于部署侧：缺席时从 <name>.example 生成，已存在则永不覆盖。
USER_CONTENT=(outputs/topic.md outputs/email_link_policy.md outputs/interpretation_rules.md)
# 入库但仍归用户所有：没有代码兜底，所以随仓库发布初值，但装过一次就不再覆盖。
USER_OWNED=(outputs/feeds.json)

for arg in "$@"; do
  case "$arg" in
    --skip-venv) SKIP_VENV=1 ;;
    --skip-node) SKIP_NODE=1 ;;
    --skip-launchd) SKIP_LAUNCHD=1 ;;
    *) echo "Usage: $(basename "$0") [--skip-venv] [--skip-node] [--skip-launchd]" >&2; exit 2 ;;
  esac
done

die() { echo "$*" >&2; exit 1; }

need_command() {
  local cmd="$1" install_hint="$2"
  command -v "$cmd" >/dev/null 2>&1 && return 0
  echo "缺少 ${cmd}。请自己执行下面这条，然后重跑本脚本："
  echo
  echo "    $install_hint"
  echo
  return 1
}

preflight() {
  local failed=0
  need_command ffmpeg "brew install ffmpeg" || failed=1
  need_command node "brew install node" || failed=1
  need_command python3 "brew install python@$MIN_PYTHON" || failed=1
  if ! command -v hermes >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/hermes" ]; then
    echo "缺少 hermes。它是另一个应用，本脚本不代装。"
    failed=1
  fi
  [ "$failed" -eq 0 ] || die "preflight 未通过：外部依赖会失败，而失败时人应该在场。"

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$MIN_PYTHON" <<'PY' || die "Python 版本过低。"
import sys
want = tuple(int(part) for part in sys.argv[1].split("."))
sys.exit(0 if sys.version_info[: len(want)] >= want else 1)
PY
  fi
}

# 同步内容 = 版本库中 outputs/ 下被追踪的文件。git 已经知道该排除什么，
# 不维护手工排除列表，也就不存在漏项。
tracked_files() { git -C "$PROJECT_ROOT" ls-files outputs; }

is_user_file() {
  local candidate="$1" entry
  for entry in "${USER_CONTENT[@]}" "${USER_OWNED[@]}"; do
    [ "$entry" = "$candidate" ] && return 0
  done
  return 1
}

sync_tracked() {
  local installed="$1" path dest
  while IFS= read -r path; do
    dest="$PODSUM_HOME/$path"
    case "$path" in *.example) continue ;; esac
    if is_user_file "$path"; then
      if [ -e "$dest" ]; then
        echo "skipped (user file): $path"
      else
        mkdir -p "$(dirname "$dest")"
        cp "$PROJECT_ROOT/$path" "$dest"
        echo "created (user file): $path"
      fi
      # 用户内容永不进回执：仓库一次改名就会带走界面上的编辑。
      continue
    fi
    mkdir -p "$(dirname "$dest")"
    cp "$PROJECT_ROOT/$path" "$dest"
    printf '%s\n' "$path" >> "$installed"
  done < <(tracked_files)
}

generate_user_content() {
  local path dest
  for path in "${USER_CONTENT[@]}"; do
    dest="$PODSUM_HOME/$path"
    [ -e "$dest" ] && continue
    mkdir -p "$(dirname "$dest")"
    cp "$PROJECT_ROOT/$path.example" "$dest"
    echo "created (user file): $path"
  done
}

# 回执是删除的唯一授权来源：装过、且已从追踪集消失的才删，其余一律不碰。
prune_with_receipt() {
  local installed="$1" path
  [ -f "$RECEIPT" ] || return 0
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    grep -qxF "$path" "$installed" && continue
    if [ -e "$PODSUM_HOME/$path" ]; then
      rm -f "$PODSUM_HOME/$path"
      echo "removed (no longer in repo): $path"
    fi
  done < "$RECEIPT"
}

report_strangers() {
  local installed="$1" path rel
  [ -d "$PODSUM_HOME/outputs" ] || return 0
  while IFS= read -r path; do
    rel="${path#"$PODSUM_HOME/"}"
    case "$rel" in
      outputs/node_modules/*|outputs/__pycache__/*|*/__pycache__/*) continue ;;
    esac
    grep -qxF "$rel" "$installed" && continue
    is_user_file "$rel" && continue
    printf '%s\n' "$rel"
    # 按目录折叠：一个实验目录里几十个文件会把要紧的信息挤出屏幕，
    # 而人读不到的输出等于没有输出。
  done < <(find "$PODSUM_HOME/outputs" -type f) | awk -F/ '
      { dir = ""; for (i = 1; i < NF; i++) dir = dir $i "/"; count[dir]++; sample[dir] = $0 }
      END {
        for (dir in count) {
          if (count[dir] > 1) printf "unknown (left alone): %s (%d 个文件)\n", dir, count[dir]
          else printf "unknown (left alone): %s\n", sample[dir]
        }
      }' | sort
}

ensure_env_file() {
  local env_path="$PODSUM_HOME/.env" value missing=()
  if [ ! -e "$env_path" ]; then
    cp "$PROJECT_ROOT/outputs/podsum.env.example" "$env_path"
    chmod 600 "$env_path"
    echo "created: .env（从模板）"
  fi
  local key
  for key in PODSUM_TARGET PODSUM_EMAIL_IMAP_USER PODSUM_EMAIL_IMAP_PASS; do
    value="$(sed -n "s/^$key=//p" "$env_path" | head -1)"
    case "$value" in
      ""|*replace-with*|*example.com*) missing+=("$key") ;;
    esac
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    echo "配置还缺：${missing[*]}（在 $env_path 里填）"
    echo "缺 PODSUM_TARGET 只影响投递，缺 IMAP 只影响邮件摘要；播客主链路照常可用。"
  fi
}

install_venv() {
  local venv="$PODSUM_HOME/.venv"
  [ -d "$venv" ] || python3 -m venv "$venv"
  "$venv/bin/pip" install --quiet --group "$PROJECT_ROOT/pyproject.toml:runtime"
  if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    "$venv/bin/pip" install --quiet --group "$PROJECT_ROOT/pyproject.toml:transcribe"
  fi
  echo "venv ready: $venv"
}

install_node_modules() {
  ( cd "$PODSUM_HOME/outputs" && npm ci --omit=dev --silent )
  echo "node_modules ready"
}

install_launchd() {
  local label="com.local.podsum" plist="$HOME/Library/LaunchAgents/com.local.podsum.plist"
  # 正在跑就不动它：完整流程可能几十分钟，卸载会终止它，而下次执行要等到次日。
  if launchctl list 2>/dev/null | awk -v l="$label" '$3 == l && $1 != "-" {found=1} END {exit !found}'; then
    die "$label 正在执行，拒绝重载。等它跑完再来。"
  fi
  mkdir -p "$(dirname "$plist")"
  sed -e "s|__PODSUM_HOME__|$PODSUM_HOME|g" -e "s|__HOME__|$HOME|g" \
    "$PROJECT_ROOT/outputs/com.local.podsum.plist" > "$plist"
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  launchctl enable "gui/$(id -u)/$label"
  echo "launchd loaded: $label"
}

main() {
  preflight
  mkdir -p "$PODSUM_HOME"
  installed="$(mktemp)"
  trap 'rm -f "$installed"' EXIT

  sync_tracked "$installed"
  generate_user_content
  prune_with_receipt "$installed"
  report_strangers "$installed"
  ensure_env_file

  [ "$SKIP_VENV" -eq 1 ] || install_venv
  [ "$SKIP_NODE" -eq 1 ] || install_node_modules
  [ "$SKIP_LAUNCHD" -eq 1 ] || install_launchd

  sort -u "$installed" > "$RECEIPT"
  echo "done: $PODSUM_HOME"
}

main
