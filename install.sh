#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
script_dir="$hermes_home/scripts/email-pipeline"
package_dir="$script_dir/email_pipeline"
runtime_python=${HERMES_PYTHON:-"$hermes_home/hermes-agent/venv/bin/python"}
service_dir="$HOME/.config/systemd/user"
[[ -x "$runtime_python" ]] || runtime_python=python3

install -d -m 700 "$script_dir" "$service_dir"
if ! "$runtime_python" -c 'import imapclient' 2>/dev/null; then
  "$runtime_python" -m pip install 'IMAPClient>=3.0,<4'
fi

rm -rf "$package_dir"
cp -R "$repo_dir/src/email_pipeline" "$package_dir"
find "$package_dir" -type d -exec chmod 700 {} +
find "$package_dir" -type f -exec chmod 600 {} +
install -m 700 "$repo_dir/email-pipeline.py" "$script_dir/email-pipeline.py"
install -m 600 "$repo_dir/hermes-email-watch.service" "$service_dir/hermes-email-watch.service"
install -m 600 "$repo_dir/hermes-email-queue.service" "$service_dir/hermes-email-queue.service"

if [[ ! -f "$script_dir/daily-mail-pipeline.toml" ]]; then
  install -m 600 "$repo_dir/daily-mail-pipeline.toml.example" "$script_dir/daily-mail-pipeline.toml"
fi
for name in summarizer-system-prompt.txt aggregator-system-prompt.txt; do
  [[ -f "$script_dir/$name" ]] || install -m 600 "$repo_dir/$name" "$script_dir/$name"
done

for dir in "$HOME/.config/opencode" "$hermes_home/opencode-email-runtime/config/opencode"; do
  install -d -m 700 "$dir/agents" "$dir/tools"
  install -m 600 "$repo_dir"/opencode/agents/*.md "$dir/agents/"
  install -m 600 "$repo_dir"/opencode/tools/*.ts "$dir/tools/"
done

rm -f "$script_dir"/{aggregate-mails-agentic.py,consume_mail_queue.py,daily-mail-pipeline.py,get_daily_aggregation.py,index_mail.py,mail-agent-tools.py,mail-index.py,scan_mails.py,summarize-mail-agentic.py,watch_mails.py}
rm -rf "$script_dir/__pycache__"
"$runtime_python" -m compileall -q "$package_dir" "$script_dir/email-pipeline.py"
printf 'Installed unified email pipeline to %s\n' "$script_dir"
