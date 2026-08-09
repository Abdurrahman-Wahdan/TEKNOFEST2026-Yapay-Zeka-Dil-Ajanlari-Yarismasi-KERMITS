"""What a run did, and whether it is allowed to publish.

Shaped like `banks.health.HealthReport`: a dataclass with `as_dict()` and
`text()`, a `healthy` property, and the verdict in the first line -- which is
the only part anyone reads.

The gates matter more than the counts. A run that would shrink the corpus, or
that lost a whole site, writes **nothing** and leaves yesterday's artifact in
place. Stale but correct beats fresh but empty, because whatever reads
`documents.jsonl` next has no way to tell the difference.
"""

from dataclasses import dataclass, field

from config.settings import settings


@dataclass
class SiteResult:
    """One site's contribution to a run."""

    site: str
    fetched: int = 0
    unchanged: int = 0
    changed: int = 0
    new: int = 0
    missing: int = 0
    errors: int = 0
    documents: int = 0
    refused: int = 0
    sections_changed: int = 0
    pdfs_seen: int = 0
    pdfs_selected: int = 0
    pdfs_classified: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class BuildReport:
    """The whole run."""

    started_at: str = ""
    sites: dict[str, SiteResult] = field(default_factory=dict)
    refusals: list[tuple[str, str]] = field(default_factory=list)
    gate: str = ""                      # why the run refused to publish, if it did
    written: bool = False
    blobs_collected: int = 0
    bytes_freed: int = 0
    campaigns_total: int = 0
    campaigns_active: int = 0
    campaigns_expired: int = 0
    previous_documents: int = 0

    @property
    def documents(self) -> int:
        return sum(s.documents for s in self.sites.values())

    @property
    def errors(self) -> int:
        return sum(s.errors for s in self.sites.values())

    @property
    def healthy(self) -> bool:
        return not self.gate

    def result(self, site: str) -> SiteResult:
        return self.sites.setdefault(site, SiteResult(site=site))

    def refuse(self, url: str, reason: str) -> None:
        """Record a document that will not be written, and why."""
        self.refusals.append((url, reason))

    # ----- gates -----

    def check_gates(self) -> str:
        """Whether this run may replace the published artifact.

        Returns the reason it may not, or "" when it may.
        """
        if self.documents == 0:
            return "the run produced no documents at all"

        for site in self.sites.values():
            if site.fetched and site.errors / max(site.fetched, 1) > (
                    settings.CORPUS_MAX_ERROR_PCT / 100):
                return (f"{site.site}: {site.errors} of {site.fetched} fetches "
                        f"failed, over the {settings.CORPUS_MAX_ERROR_PCT}% limit")

        if self.previous_documents:
            shrink = 100 * (1 - self.documents / self.previous_documents)
            if shrink > settings.CORPUS_MAX_SHRINK_PCT:
                return (f"the corpus would shrink {shrink:.0f}% "
                        f"({self.previous_documents} -> {self.documents} documents), "
                        f"over the {settings.CORPUS_MAX_SHRINK_PCT}% limit")
        return ""

    # ----- output -----

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "documents": self.documents,
            "previous_documents": self.previous_documents,
            "errors": self.errors,
            "written": self.written,
            "gate": self.gate,
            "healthy": self.healthy,
            "blobs_collected": self.blobs_collected,
            "bytes_freed": self.bytes_freed,
            "campaigns": {
                "total": self.campaigns_total,
                "active": self.campaigns_active,
                "expired": self.campaigns_expired,
            },
            "sites": {name: s.as_dict() for name, s in sorted(self.sites.items())},
            "refusals": [{"url": u, "reason": r} for u, r in self.refusals[:50]],
            "refusals_total": len(self.refusals),
        }

    def text(self) -> str:
        """Human-readable, verdict first."""
        if self.gate:
            verdict = f"REFUSED to publish: {self.gate}"
        elif self.errors:
            verdict = f"published with {self.errors} fetch error(s)"
        else:
            verdict = "All well."

        lines = [
            f"corpus {self.started_at[:10]} — {self.documents} documents, "
            f"{len(self.refusals)} refused, {self.errors} errors. {verdict}",
            "",
        ]
        if self.sites:
            lines.append(f"{'site':16}{'docs':>6}{'new':>6}{'chg':>6}{'same':>6}"
                         f"{'miss':>6}{'err':>6}{'ref':>6}{'sect':>6}{'pdf':>6}")
            for name, s in sorted(self.sites.items()):
                lines.append(
                    f"{name:16}{s.documents:6}{s.new:6}{s.changed:6}{s.unchanged:6}"
                    f"{s.missing:6}{s.errors:6}{s.refused:6}{s.sections_changed:6}"
                    f"{s.pdfs_selected:6}")
            lines.append("")

        if self.campaigns_total:
            lines.append(
                f"campaigns: {self.campaigns_total} total, "
                f"{self.campaigns_active} active, {self.campaigns_expired} expired")
        if self.blobs_collected:
            lines.append(f"store: {self.blobs_collected} orphaned blob(s) collected, "
                         f"{self.bytes_freed / 1_000_000:.1f} MB freed")
        if self.refusals:
            lines.append("")
            lines.append(f"refused ({len(self.refusals)}):")
            for url, reason in self.refusals[:20]:
                lines.append(f"  {reason:42} {url[:70]}")
            if len(self.refusals) > 20:
                lines.append(f"  ... and {len(self.refusals) - 20} more")
        return "\n".join(lines)
