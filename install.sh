#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
script_root="$hermes_home/scripts"
script_dir="$script_root/email-pipeline"
target="$script_dir/daily-mail-pipeline.py"
package_dir="$script_dir/email_pipeline"
opencode_agent_dir="$HOME/.config/opencode/agents"
opencode_tool_dir="$HOME/.config/opencode/tools"
opencode_email_config_dir="$hermes_home/opencode-email-runtime/config/opencode"
runtime_python=${HERMES_PYTHON:-"$hermes_home/hermes-agent/venv/bin/python"}
service_dir="$HOME/.config/systemd/user"
if [[ ! -x "$runtime_python" ]]; then
  runtime_python=python3
fi

install -d -m 700 "$script_root" "$script_dir"
install -d -m 700 "$service_dir"
if ! "$runtime_python" -c 'import imapclient' 2>/dev/null; then
  "$runtime_python" -m pip install 'IMAPClient>=3.0,<4'
fi

install -m 700 "$repo_dir/daily-mail-pipeline.py" "$target"
install -m 700 "$repo_dir/scan_mails.py" "$script_dir/scan_mails.py"
install -m 700 "$repo_dir/index_mail.py" "$script_dir/index_mail.py"
install -m 700 "$repo_dir/watch_mails.py" "$script_dir/watch_mails.py"
install -m 700 "$repo_dir/consume_mail_queue.py" "$script_dir/consume_mail_queue.py"
install -m 700 "$repo_dir/get_daily_aggregation.py" "$script_dir/get_daily_aggregation.py"
rm -f "$script_dir/index-mail.py"
install -m 700 "$repo_dir/mail-index.py" "$script_dir/mail-index.py"
rm -f "$script_dir/unpack-mail.py"
rm -f "$script_dir/summarize-mail.py"
install -m 700 "$repo_dir/summarize-mail-agentic.py" "$script_dir/summarize-mail-agentic.py"
rm -f "$script_dir/opencode-mail.py"
install -m 700 "$repo_dir/aggregate-mails-agentic.py" "$script_dir/aggregate-mails-agentic.py"
rm -f "$script_dir/mails-aggregate-agentic.py"
rm -f "$script_dir/opencode-daily-summary.py"
install -d -m 700 "$package_dir"
install -m 600 "$repo_dir/src/email_pipeline/__init__.py" "$package_dir/__init__.py"
install -m 600 "$repo_dir/src/email_pipeline/mime_extract.py" "$package_dir/mime_extract.py"
install -m 600 "$repo_dir/src/email_pipeline/daily_schema.py" "$package_dir/daily_schema.py"
install -m 600 "$repo_dir/src/email_pipeline/mail_identity.py" "$package_dir/mail_identity.py"
install -m 600 "$repo_dir/src/email_pipeline/imap_backend.py" "$package_dir/imap_backend.py"
install -m 600 "$repo_dir/src/email_pipeline/daily_logging.py" "$package_dir/daily_logging.py"
install -m 600 "$repo_dir/src/email_pipeline/program_time.py" "$package_dir/program_time.py"
install -m 600 "$repo_dir/src/email_pipeline/config.py" "$package_dir/config.py"
install -m 600 "$repo_dir/hermes-email-watch.service" "$service_dir/hermes-email-watch.service"
install -m 600 "$repo_dir/hermes-email-queue.service" "$service_dir/hermes-email-queue.service"
if [[ -f "$script_root/daily-mail-pipeline.toml" && ! -f "$script_dir/daily-mail-pipeline.toml" ]]; then
  install -m 600 "$script_root/daily-mail-pipeline.toml" "$script_dir/daily-mail-pipeline.toml"
fi
if [[ ! -f "$script_dir/daily-mail-pipeline.toml" ]]; then
  install -m 600 "$repo_dir/daily-mail-pipeline.toml.example" "$script_dir/daily-mail-pipeline.toml"
fi
if [[ ! -f "$script_dir/system-prompt.txt" ]]; then
  install -m 600 "$repo_dir/system-prompt.txt" "$script_dir/system-prompt.txt"
fi
install -d -m 700 "$opencode_agent_dir"
install -m 600 "$repo_dir/opencode/agents/mail-analyzer.md" "$opencode_agent_dir/mail-analyzer.md"
install -m 600 "$repo_dir/opencode/agents/mail-daily-aggregator.md" "$opencode_agent_dir/mail-daily-aggregator.md"
install -d -m 700 "$opencode_tool_dir"
install -m 600 "$repo_dir/opencode/tools/mail_fetch.ts" "$opencode_tool_dir/mail_fetch.ts"
install -m 600 "$repo_dir/opencode/tools/mail_unpack.ts" "$opencode_tool_dir/mail_unpack.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_attachment.ts" "$opencode_tool_dir/mail_extract_attachment.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_links.ts" "$opencode_tool_dir/mail_extract_links.ts"
install -m 600 "$repo_dir/opencode/tools/mail_inspect_link.ts" "$opencode_tool_dir/mail_inspect_link.ts"
install -m 600 "$repo_dir/opencode/tools/mail_validate_daily_summary.ts" "$opencode_tool_dir/mail_validate_daily_summary.ts"
install -d -m 700 "$opencode_email_config_dir/agents" "$opencode_email_config_dir/tools"
install -m 600 "$repo_dir/opencode/agents/mail-analyzer.md" "$opencode_email_config_dir/agents/mail-analyzer.md"
install -m 600 "$repo_dir/opencode/agents/mail-daily-aggregator.md" "$opencode_email_config_dir/agents/mail-daily-aggregator.md"
install -m 600 "$repo_dir/opencode/tools/mail_fetch.ts" "$opencode_email_config_dir/tools/mail_fetch.ts"
install -m 600 "$repo_dir/opencode/tools/mail_unpack.ts" "$opencode_email_config_dir/tools/mail_unpack.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_attachment.ts" "$opencode_email_config_dir/tools/mail_extract_attachment.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_links.ts" "$opencode_email_config_dir/tools/mail_extract_links.ts"
install -m 600 "$repo_dir/opencode/tools/mail_inspect_link.ts" "$opencode_email_config_dir/tools/mail_inspect_link.ts"
install -m 600 "$repo_dir/opencode/tools/mail_validate_daily_summary.ts" "$opencode_email_config_dir/tools/mail_validate_daily_summary.ts"
install -m 700 "$repo_dir/mail-agent-tools.py" "$script_dir/mail-agent-tools.py"
install -m 600 "$repo_dir/src/email_pipeline/agent_tools.py" "$package_dir/agent_tools.py"

python3 -m py_compile \
  "$target" \
  "$script_dir/scan_mails.py" \
  "$script_dir/index_mail.py" \
  "$script_dir/watch_mails.py" \
  "$script_dir/consume_mail_queue.py" \
  "$script_dir/get_daily_aggregation.py" \
  "$script_dir/mail-index.py" \
  "$script_dir/summarize-mail-agentic.py" \
  "$script_dir/aggregate-mails-agentic.py" \
  "$script_dir/mail-agent-tools.py" \
  "$package_dir/__init__.py" \
  "$package_dir/mime_extract.py" \
  "$package_dir/agent_tools.py" \
  "$package_dir/daily_schema.py" \
  "$package_dir/mail_identity.py" \
  "$package_dir/imap_backend.py" \
  "$package_dir/daily_logging.py" \
  "$package_dir/program_time.py" \
  "$package_dir/config.py"

rm -f \
  "$script_root/aggregate-mails-agentic.py" \
  "$script_root/consume_mail_queue.py" \
  "$script_root/daily-mail-pipeline.py" \
  "$script_root/get_daily_aggregation.py" \
  "$script_root/index_mail.py" \
  "$script_root/mail-agent-tools.py" \
  "$script_root/mail-index.py" \
  "$script_root/migrate-program-times.py" \
  "$script_root/scan_mails.py" \
  "$script_root/set-email-summary-key.py" \
  "$script_root/summarize-mail-agentic.py" \
  "$script_root/watch_mails.py" \
  "$script_root/daily-mail-pipeline.toml" \
  "$script_root/daily-mail-pipeline.yaml.pre-program-time" \
  "$script_root/daily-mail-pipeline.yaml.pre-toml"
rm -rf "$script_root/email_pipeline" "$script_root/__pycache__"

printf 'Installed read-only mail pipeline to %s\n' "$target"
printf 'Existing TOML configuration was not modified.\n'
