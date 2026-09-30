#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
code_root=${EMAIL_PIPELINE_CODE_ROOT:-"$HOME/email-pipeline-code"}
config_root=${EMAIL_PIPELINE_CONFIG_ROOT:-"$HOME/.email-pipeline"}
data_root=${EMAIL_PIPELINE_DATA_ROOT:-"$HOME/email-pipeline"}
runtime_python=${EMAIL_PIPELINE_PYTHON:-python3}
install -d -m 700 "$code_root" "$config_root" "$data_root"
if [[ ! -x "$code_root/.venv/bin/python3" ]]; then
  "$runtime_python" -m venv "$code_root/.venv"
fi
venv_python="$code_root/.venv/bin/python3"
if [[ -x "$code_root/.venv/bin/pip" ]]; then
  "$code_root/.venv/bin/pip" install -q --upgrade pip setuptools
  "$code_root/.venv/bin/pip" install -q --no-build-isolation --no-deps "$repo_dir"
  "$code_root/.venv/bin/pip" install -q 'IMAPClient>=3.0,<4' 'requests>=2.31'
else
  uv_bin=$(command -v uv 2>/dev/null || true)
  [[ -n "$uv_bin" ]] || uv_bin="$HOME/.hermes/bin/uv"
  [[ -x "$uv_bin" ]] || { echo "error: venv has no pip and uv was not found" >&2; exit 1; }
  "$uv_bin" pip install -q --python "$venv_python" "$repo_dir" 'IMAPClient>=3.0,<4' 'requests>=2.31'
fi
rm -rf "$code_root/email_pipeline" "$code_root/__pycache__"
install -d -m 700 "$HOME/.local/bin"
ln -sfn "$code_root/.venv/bin/email-pipeline" "$HOME/.local/bin/email-pipeline"

if [[ ! -f "$config_root/daily-mail-pipeline.toml" ]]; then
  install -m 600 "$repo_dir/daily-mail-pipeline.toml.example" "$config_root/daily-mail-pipeline.toml"
fi
for stage in summarizer aggregator; do
  name="${stage}-system-prompt.txt"
  [[ -f "$config_root/$name" ]] || install -m 600 "$repo_dir/opencode/$stage/system-prompt.txt" "$config_root/$name"
done

for dir in "$HOME/.config/opencode" "$data_root/opencode-runtime/config/opencode"; do
  install -d -m 700 "$dir/agents" "$dir/tools"
  install -m 600 "$repo_dir"/opencode/summarizer/agents/*.md "$dir/agents/"
  install -m 600 "$repo_dir"/opencode/aggregator/agents/*.md "$dir/agents/"
  install -m 600 "$repo_dir"/opencode/summarizer/tools/*.ts "$dir/tools/"
  install -m 600 "$repo_dir"/opencode/aggregator/tools/*.ts "$dir/tools/"
done

printf 'Installed code=%s config=%s data=%s\n' "$code_root" "$config_root" "$data_root"
