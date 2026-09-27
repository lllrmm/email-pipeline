#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hermes_home=${HERMES_HOME:-"$HOME/.hermes"}
script_dir="$hermes_home/scripts"
target="$script_dir/daily-mail-pipeline.py"
package_dir="$script_dir/email_pipeline"

install -d -m 700 "$script_dir"

if [[ -f "$target" ]]; then
  cp -p "$target" "$target.bak-pre-mime-v2"
  chmod 600 "$target.bak-pre-mime-v2"
fi

install -m 700 "$repo_dir/daily-mail-pipeline.py" "$target"
install -d -m 700 "$package_dir"
install -m 600 "$repo_dir/src/email_pipeline/__init__.py" "$package_dir/__init__.py"
install -m 600 "$repo_dir/src/email_pipeline/mime_extract.py" "$package_dir/mime_extract.py"

python3 -m py_compile "$target" "$package_dir/__init__.py" "$package_dir/mime_extract.py"

printf 'Installed read-only mail pipeline to %s\n' "$target"
printf 'Existing YAML configuration was not modified.\n'
