"""Turn HEALTH_SCHEDULE into something a scheduler will accept.

    python -m banks.schedule              # a crontab line
    python -m banks.schedule --launchd    # a launchd plist (macOS)

The admin sets the time by editing HEALTH_SCHEDULE, runs this, and pastes the
result into `crontab -e` or `~/Library/LaunchAgents`. It prints; it never
installs anything, because a tool that edits your scheduler behind your back is
harder to trust than one you paste from.

Paths come from the running interpreter and the project root, so the output is
correct for this machine without anyone hand-editing it.
"""

import argparse
import sys

from config.settings import PROJECT_ROOT, settings

LABEL = "com.tf26.bankaudit"
COMMAND = "-m banks.audit --quiet"

FIELDS = ("minute", "hour", "day", "month", "weekday")
PLIST_KEYS = {"minute": "Minute", "hour": "Hour", "day": "Day",
              "month": "Month", "weekday": "Weekday"}


def _parse(expression: str) -> dict[str, list[int] | None]:
    """A cron expression as fields, or an error saying why not.

    Only the part that translates exactly is accepted: a number, `*`, or a
    comma-separated list. Ranges and steps are refused rather than approximated,
    because a plist that runs at a different time from the one configured is
    worse than no plist.
    """
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(
            f"{expression!r} is not a cron expression. Five fields are needed: "
            f"{' '.join(FIELDS)} — for example '0 6 * * *' for 06:00 daily."
        )
    parsed: dict[str, list[int] | None] = {}
    for name, part in zip(FIELDS, parts):
        if part == "*":
            parsed[name] = None
            continue
        values = []
        for piece in part.split(","):
            if not piece.isdigit():
                raise ValueError(
                    f"{expression!r} uses {piece!r} in the {name} field. This "
                    f"writes calendar entries, which cannot express ranges or "
                    f"steps like '*/15' exactly — and a schedule that quietly "
                    f"runs at a different time is worse than none. Use a "
                    f"number, '*', or a comma-separated list."
                )
            values.append(int(piece))
        parsed[name] = values
    return parsed


def command() -> str:
    """The command a scheduler should run, with absolute paths."""
    return f"{sys.executable} {COMMAND}"


def crontab_line(expression: str | None = None) -> str:
    """The crontab line for the configured schedule."""
    schedule = expression or settings.HEALTH_SCHEDULE
    _parse(schedule)  # refuse an expression cron itself would not take
    log = PROJECT_ROOT / "audit.log"
    return f"{schedule} cd {PROJECT_ROOT} && {command()} >> {log} 2>&1"


def launchd_plist(expression: str | None = None, label: str = LABEL) -> str:
    """The same schedule as a launchd job, for macOS."""
    schedule = expression or settings.HEALTH_SCHEDULE
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
        <string>-m</string><string>banks.audit</string><string>--quiet</string>
    </array>
    <key>WorkingDirectory</key><string>{PROJECT_ROOT}</string>
    <key>StandardOutPath</key><string>{PROJECT_ROOT / 'audit.log'}</string>
    <key>StandardErrorPath</key><string>{PROJECT_ROOT / 'audit.log'}</string>
    <key>StartCalendarInterval</key>
    <array>
{calendar}
    </array>
</dict>
</plist>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m banks.schedule",
        description="Print the schedule entry for the configured HEALTH_SCHEDULE.",
    )
    parser.add_argument("--launchd", action="store_true",
                        help="A launchd plist instead of a crontab line.")
    parser.add_argument("--schedule", help="Override HEALTH_SCHEDULE for this run.")
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
