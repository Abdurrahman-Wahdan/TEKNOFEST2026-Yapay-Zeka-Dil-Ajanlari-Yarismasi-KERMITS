"""Turn CORPUS_SCHEDULE into something a scheduler will accept.

    python -m corpus.schedule              # a crontab line
    python -m corpus.schedule --launchd    # a launchd plist (macOS)

Same contract as `banks.schedule`: it prints, it never installs, and it refuses
a cron expression a calendar entry cannot express exactly rather than quietly
running at a different time. The cron parsing is reused from there rather than
copied -- there is one right way to reject `*/15`, and it already exists.
"""

import argparse
import sys

from banks.schedule import _parse
from config.settings import PROJECT_ROOT, settings

LABEL = "com.tf26.corpus"
COMMAND = "-m corpus.build --quiet"
PLIST_KEYS = {"minute": "Minute", "hour": "Hour", "day": "Day",
              "month": "Month", "weekday": "Weekday"}


def command() -> str:
    """The command a scheduler should run, with absolute paths."""
    return f"{sys.executable} {COMMAND}"


def crontab_line(expression: str | None = None) -> str:
    schedule = expression or settings.CORPUS_SCHEDULE
    _parse(schedule)  # refuse an expression cron itself would not take
    log = PROJECT_ROOT / "corpus.log"
    return f"{schedule} cd {PROJECT_ROOT} && {command()} >> {log} 2>&1"


def launchd_plist(expression: str | None = None, label: str = LABEL) -> str:
    schedule = expression or settings.CORPUS_SCHEDULE
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
        <string>-m</string><string>corpus.build</string><string>--quiet</string>
    </array>
    <key>WorkingDirectory</key><string>{PROJECT_ROOT}</string>
    <key>StandardOutPath</key><string>{PROJECT_ROOT / 'corpus.log'}</string>
    <key>StandardErrorPath</key><string>{PROJECT_ROOT / 'corpus.log'}</string>
    <key>StartCalendarInterval</key>
    <array>
{calendar}
    </array>
</dict>
</plist>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpus.schedule",
        description="Print the schedule entry for the configured CORPUS_SCHEDULE.",
    )
    parser.add_argument("--launchd", action="store_true",
                        help="A launchd plist instead of a crontab line.")
    parser.add_argument("--schedule", help="Override CORPUS_SCHEDULE for this run.")
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
