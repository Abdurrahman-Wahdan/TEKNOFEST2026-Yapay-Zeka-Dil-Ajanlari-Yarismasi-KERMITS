"""What can go wrong producing a file, as something a router can turn into HTTP."""


class ExportUnavailable(RuntimeError):
    """A format cannot be produced because the machine is missing a tool.

    Distinct from a bug and from a bad request: nothing about the data is wrong
    and retrying will not help until somebody installs something. Carries a
    message written for the person who has to fix it -- naming the binary and the
    install line -- following the rule `requirements.txt` already states for
    poppler: refuse with a clear message rather than failing deep inside a
    subprocess.
    """


class ExportEmpty(ValueError):
    """There is nothing to put in the file.

    A report that failed before it wrote a body, or a table filtered down to
    zero rows. Refused rather than served, because a zero-byte download reads as
    a broken button and sends the user looking for a bug that is not there.
    """
