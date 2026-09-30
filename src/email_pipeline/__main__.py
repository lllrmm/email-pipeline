from __future__ import annotations

import sys

from .agent_tools import main as tools_main
from .cli.aggregate import main as aggregate_main
from .cli.daily_aggregation import main as aggregate_day_main
from .cli.lookup import main as lookup_main
from .cli.init import main as init_main
from .cli.service import main as service_main
from .cli.scan import main as scan_main
from .cli.summarize import main as summarize_main
from .registry import main as register_main

COMMANDS = {
    "scan": scan_main,
    "register": register_main,
    "lookup": lookup_main,
    "summarize": summarize_main,
    "aggregate": aggregate_main,
    "aggregate-day": aggregate_day_main,
    "tools": tools_main,
    "init": init_main,
    "service": service_main,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("usage: python -m email_pipeline <" + "|".join(COMMANDS) + "> ...", file=sys.stderr)
        return 2
    command = sys.argv.pop(1)
    return COMMANDS[command]()


if __name__ == "__main__":
    raise SystemExit(main())
