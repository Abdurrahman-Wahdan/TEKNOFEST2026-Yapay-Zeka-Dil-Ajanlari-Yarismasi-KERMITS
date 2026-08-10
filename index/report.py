"""What a sync did, and whether it was allowed to apply.

Shaped like `corpus.report.BuildReport`: a dataclass with `as_dict()` and
`text()`, a `healthy` property, and the verdict in the first line. The one gate
mirrors the corpus shrink gate — a sync that would delete most of the index
refuses, so a truncated `documents.jsonl` cannot wipe it.
"""

from dataclasses import dataclass, field

from config.settings import settings


@dataclass
class IndexReport:
    """One nightly sync."""

    started_at: str = ""
    chunks_total: int = 0          # chunks the artifact produced this run
    held: int = 0                  # points already in the collection
    embedded: int = 0              # new or changed -> embedded and upserted
    skipped: int = 0               # unchanged -> not re-embedded
    deleted: int = 0              # gone from the artifact -> removed
    by_kind: dict = field(default_factory=dict)         # doc_kind -> chunk count
    by_source: dict = field(default_factory=dict)       # page/pdf -> chunk count
    campaigns_active: int = 0
    campaigns_expired: int = 0
    gate: str = ""                 # why the sync refused to apply, if it did
    written: bool = False

    @property
    def healthy(self) -> bool:
        return not self.gate

    def check_gate(self, to_delete: int) -> str:
        """Whether this many deletes may be applied.

        Returns the reason they may not, or "" when they may.
        """
        if self.held and to_delete / self.held > settings.INDEX_MAX_DELETE_PCT / 100:
            return (f"would delete {to_delete} of {self.held} points "
                    f"({100 * to_delete / self.held:.0f}%), over the "
                    f"{settings.INDEX_MAX_DELETE_PCT}% limit")
        return ""

    def as_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "chunks_total": self.chunks_total,
            "held": self.held,
            "embedded": self.embedded,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "written": self.written,
            "gate": self.gate,
            "healthy": self.healthy,
            "campaigns": {"active": self.campaigns_active,
                          "expired": self.campaigns_expired},
            "by_kind": dict(sorted(self.by_kind.items())),
            "by_source": dict(sorted(self.by_source.items())),
        }

    def text(self) -> str:
        if self.gate:
            verdict = f"REFUSED: {self.gate}"
        elif self.embedded or self.deleted:
            verdict = "applied."
        else:
            verdict = "nothing changed."
        lines = [
            f"index {self.started_at[:10]} — {self.chunks_total} chunks, "
            f"{self.embedded} embedded, {self.skipped} unchanged, "
            f"{self.deleted} deleted. {verdict}",
            "",
        ]
        if self.by_source:
            lines.append("by source: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.by_source.items())))
        if self.by_kind:
            lines.append("by kind:   " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.by_kind.items())))
        if self.campaigns_active or self.campaigns_expired:
            lines.append(f"campaigns: {self.campaigns_active} active, "
                         f"{self.campaigns_expired} expired (expired are indexed "
                         f"but filtered out at query time)")
        return "\n".join(lines)
