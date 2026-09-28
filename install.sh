#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
code_root=${EMAIL_PIPELINE_CODE_ROOT:-"$HOME/email-pipeline-code"}
config_root=${EMAIL_PIPELINE_CONFIG_ROOT:-"$HOME/.email-pipeline"}
data_root=${EMAIL_PIPELINE_DATA_ROOT:-"$HOME/email-pipeline"}
package_dir="$code_root/email_pipeline"
runtime_python=${EMAIL_PIPELINE_PYTHON:-"$code_root/.venv/bin/python"}
service_dir="$HOME/.config/systemd/user"
if [[ ! -x "$runtime_python" ]]; then
  python3 -m venv "$code_root/.venv"
fi

install -d -m 700 "$code_root" "$config_root" "$data_root" "$service_dir"
"$runtime_python" -m pip install -q 'IMAPClient>=3.0,<4' 'requests>=2.31'

rm -rf "$package_dir"
cp -R "$repo_dir/src/email_pipeline" "$package_dir"
find "$package_dir" -type d -exec chmod 700 {} +
find "$package_dir" -type f -exec chmod 600 {} +
install -m 700 "$repo_dir/email-pipeline.py" "$code_root/email-pipeline.py"
install -m 600 "$repo_dir/hermes-email-watch.service" "$service_dir/hermes-email-watch.service"
install -m 600 "$repo_dir/hermes-email-queue.service" "$service_dir/hermes-email-queue.service"

if [[ ! -f "$config_root/daily-mail-pipeline.toml" ]]; then
  install -m 600 "$repo_dir/daily-mail-pipeline.toml.example" "$config_root/daily-mail-pipeline.toml"
fi
for name in summarizer-system-prompt.txt aggregator-system-prompt.txt; do
  [[ -f "$config_root/$name" ]] || install -m 600 "$repo_dir/$name" "$config_root/$name"
done

for dir in "$HOME/.config/opencode" "$data_root/opencode-runtime/config/opencode"; do
  install -d -m 700 "$dir/agents" "$dir/tools"
  install -m 600 "$repo_dir"/opencode/agents/*.md "$dir/agents/"
  install -m 600 "$repo_dir"/opencode/tools/*.ts "$dir/tools/"
done

rm -rf "$code_root/__pycache__"
"$runtime_python" -m compileall -q "$package_dir" "$code_root/email-pipeline.py"
printf 'Installed code=%s config=%s data=%s\n' "$code_root" "$config_root" "$data_root"
