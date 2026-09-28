#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
script_dir="$hermes_home/scripts"
target="$script_dir/daily-mail-pipeline.py"
package_dir="$script_dir/email_pipeline"
opencode_agent_dir="$HOME/.config/opencode/agents"
opencode_tool_dir="$HOME/.config/opencode/tools"
opencode_email_config_dir="$hermes_home/opencode-email-runtime/config/opencode"

install -d -m 700 "$script_dir"

if [[ -f "$target" ]]; then
  cp -p "$target" "$target.bak-pre-mime-v2"
  chmod 600 "$target.bak-pre-mime-v2"
fi

install -m 700 "$repo_dir/daily-mail-pipeline.py" "$target"
install -m 700 "$repo_dir/unpack-mail.py" "$script_dir/unpack-mail.py"
install -m 700 "$repo_dir/summarize-mail.py" "$script_dir/summarize-mail.py"
install -m 700 "$repo_dir/opencode-mail.py" "$script_dir/opencode-mail.py"
install -m 700 "$repo_dir/opencode-daily-summary.py" "$script_dir/opencode-daily-summary.py"
install -d -m 700 "$package_dir"
install -m 600 "$repo_dir/src/email_pipeline/__init__.py" "$package_dir/__init__.py"
install -m 600 "$repo_dir/src/email_pipeline/mime_extract.py" "$package_dir/mime_extract.py"
install -d -m 700 "$opencode_agent_dir"
install -m 600 "$repo_dir/opencode/agents/mail-analyzer.md" "$opencode_agent_dir/mail-analyzer.md"
install -m 600 "$repo_dir/opencode/agents/mail-daily-aggregator.md" "$opencode_agent_dir/mail-daily-aggregator.md"
install -d -m 700 "$opencode_tool_dir"
install -m 600 "$repo_dir/opencode/tools/mail_fetch.ts" "$opencode_tool_dir/mail_fetch.ts"
install -m 600 "$repo_dir/opencode/tools/mail_unpack.ts" "$opencode_tool_dir/mail_unpack.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_attachment.ts" "$opencode_tool_dir/mail_extract_attachment.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_links.ts" "$opencode_tool_dir/mail_extract_links.ts"
install -m 600 "$repo_dir/opencode/tools/mail_inspect_link.ts" "$opencode_tool_dir/mail_inspect_link.ts"
install -d -m 700 "$opencode_email_config_dir/agents" "$opencode_email_config_dir/tools"
install -m 600 "$repo_dir/opencode/agents/mail-analyzer.md" "$opencode_email_config_dir/agents/mail-analyzer.md"
install -m 600 "$repo_dir/opencode/agents/mail-daily-aggregator.md" "$opencode_email_config_dir/agents/mail-daily-aggregator.md"
install -m 600 "$repo_dir/opencode/tools/mail_fetch.ts" "$opencode_email_config_dir/tools/mail_fetch.ts"
install -m 600 "$repo_dir/opencode/tools/mail_unpack.ts" "$opencode_email_config_dir/tools/mail_unpack.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_attachment.ts" "$opencode_email_config_dir/tools/mail_extract_attachment.ts"
install -m 600 "$repo_dir/opencode/tools/mail_extract_links.ts" "$opencode_email_config_dir/tools/mail_extract_links.ts"
install -m 600 "$repo_dir/opencode/tools/mail_inspect_link.ts" "$opencode_email_config_dir/tools/mail_inspect_link.ts"
install -m 700 "$repo_dir/mail-agent-tools.py" "$script_dir/mail-agent-tools.py"
install -m 600 "$repo_dir/src/email_pipeline/agent_tools.py" "$package_dir/agent_tools.py"

python3 -m py_compile \
  "$target" \
  "$script_dir/unpack-mail.py" \
  "$script_dir/summarize-mail.py" \
  "$script_dir/opencode-mail.py" \
  "$script_dir/opencode-daily-summary.py" \
  "$script_dir/mail-agent-tools.py" \
  "$package_dir/__init__.py" \
  "$package_dir/mime_extract.py" \
  "$package_dir/agent_tools.py"

printf 'Installed read-only mail pipeline to %s\n' "$target"
printf 'Existing YAML configuration was not modified.\n'
