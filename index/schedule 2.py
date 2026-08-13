"""Turn INDEX_SCHEDULE into a scheduler entry.

    python -m index.schedule              # a crontab line
    python -m index.schedule --launchd    # a launchd plist (macOS)

Prints, never installs — the same contract as `corpus.schedule` and
`banks.schedule`, whose cron parser is reused here. Staggered after the corpus
build so `documents.jsonl` is finished before the index reads it.
"""

import argparse
import sys

from banks.schedule import _parse
from config.settings import PROJECT_ROOT, settings

LABEL = "com.tf26.index"
COMMAND = "-m index --quiet"
PLIST_KEYS = {"minute": "Minute", "hour": "Hour", "day": "Day",
              "month": "Month", "weekday": "Weekday"}


def command() -> str:
    return f"{sys.executable} {COMMAND}"


def crontab_line(expression: str | None = None) -> str:
    schedule = expression or settings.INDEX_SCHEDULE
    _parse(schedule)
    log = PROJECT_ROOT / "index.log"
    return f"{schedule} cd {PROJECT_ROOT} && {command()} >> {log} 2>&1"


def launchd_plist(expression: str | None = None, label: str = LABEL) -> str:
    schedule = expression or settings.INDEX_SCHEDULE
    parsed = _parse(schedule)

    entries = []
    lists = {name: values for name, values in parsed.items() if values}
    count = max((len(v) for v in lists.values()), default=1)
    for index in range(count or 1):
        pairs = "".join(
            f"\n            <key>{PLIST_KEYS[name]}</key>"
            f"<integer>{values[index % len(values)]}</integer>"
            for name, values in lists.items()
        )
        entries.append(f"        <dict>{pairs}\n        </dict>")
    calendar = "\n".join(entries) or "        <dict/>"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string><string>index</string><string>--quiet</string>
    </array>
    <key>WorkingDirectory</key><string>{PROJECT_ROOT}</string>
    <key>StandardOutPath</key><string>{PROJECT_ROOT / 'index.log'}</string>
    <key>StandardErrorPath</key><string>{PROJECT_ROOT / 'index.log'}</string>
    <key>StartCalendarInterval</key>
    <array>
{calendar}
    </array>
</dict>
</plist>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m index.schedule",
        description="Print the schedule entry for the configured INDEX_SCHEDULE.",
    )
    parser.add_argument("--launchd", action="store_true")
    parser.add_argument("--schedule", help="Override INDEX_SCHEDULE for this run.")
    args = parser.parse_args(argv)

    try:
        if args.launchd:
            print(launchd_plist(args.schedule))
            print(f"\n# Save as ~/Library/LaunchAgents/{LABEL}.plist, then:",
                  f"#   launchctl load ~/Library/LaunchAgents/{LABEL}.plist", sep="\n")
        else:
            print(crontab_line(args.schedule))
            print("\n# Add with: crontab -e")
    except ValueError as exc:
        print(exc)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
