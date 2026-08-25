"""The public answer guard edits only selected, mechanically safe segments."""

from collections.abc import Iterator

import pytest

from agents.output_guard import agent as guard
from agents.output_guard.models import GuardReview, PolicyCheck, TextPatch

pytestmark = pytest.mark.unit


def _checks(status: str = "pass") -> list[PolicyCheck]:
    return [
        PolicyCheck(policy_id=policy.id, status=status)
        for policy in guard.load_policies()
    ]


def _review(*, patches: list[TextPatch] | None = None) -> GuardReview:
    patches = patches or []
    violated = {
        policy_id for patch in patches for policy_id in patch.policy_ids
    }
    checks = [
        PolicyCheck(
            policy_id=policy.id,
            status="violation" if policy.id in violated else "pass",
        )
        for policy in guard.load_policies()
    ]
    return GuardReview(
        checks=checks,
        patches=patches,
        safe_after_patches=True,
    )


def test_clean_answer_costs_one_review_and_is_unchanged(monkeypatch):
    calls: list[bool] = []

    def review(text, policies, *, verification, **kwargs):  # noqa: ARG001
        calls.append(verification)
        return _review()

    monkeypatch.setattr(guard, "_review", review)
    result = guard.guard_output("Kuveyt Türk için güncel bilgileri derledim.")

    assert result.text == "Kuveyt Türk için güncel bilgileri derledim."
    assert result.changed is False
    assert result.passes == 1
    assert calls == [False]


def test_local_patch_is_rechecked_without_rewriting_the_answer(monkeypatch):
    reviews: Iterator[GuardReview] = iter([
        _review(patches=[TextPatch(
            policy_ids=["internal_implementation"],
            segment_id="S1",
            replacement="bilgi kaynaklarımdan Kuveyt Türk bilgilerini buldum.",
        )]),
        _review(),
    ])
    monkeypatch.setattr(guard, "_review", lambda *args, **kwargs: next(reviews))

    result = guard.guard_output(
        "Qdrant veri tabanımdan Kuveyt Türk bilgilerini buldum."
    )

    assert result.text == "bilgi kaynaklarımdan Kuveyt Türk bilgilerini buldum."
    assert result.changed is True
    assert result.passes == 2
    assert len(result.patches) == 1


def test_patch_cannot_change_a_source_url():
    with pytest.raises(guard.OutputGuardError, match="source URL"):
        guard.apply_patches(
            "[Kaynak](https://bank.example/a)",
            [TextPatch(
                policy_ids=["answer_integrity"],
                segment_id="S1",
                replacement="[Kaynak](https://bank.example/b)",
            )],
        )


def test_patch_cannot_introduce_a_numeric_claim():
    with pytest.raises(guard.OutputGuardError, match="numeric claim"):
        guard.apply_patches(
            "Kâr payı bilgisi mevcut.",
            [TextPatch(
                policy_ids=["factual_conservatism"],
                segment_id="S1",
                replacement="Kâr payı bilgisi %42 seviyesinde.",
            )],
        )


def test_checklist_must_cover_the_editable_policy_set():
    policies = guard.load_policies()
    incomplete = GuardReview(
        checks=[PolicyCheck(policy_id=policies[0].id, status="pass")],
        safe_after_patches=True,
    )

    with pytest.raises(guard.OutputGuardError, match="checklist mismatch"):
        guard._validate_checklist(incomplete, policies)


def test_verification_must_be_clean(monkeypatch):
    patch = TextPatch(
        policy_ids=["internal_implementation"],
        segment_id="S1",
        replacement="kaynak",
    )
    reviews: Iterator[GuardReview] = iter([_review(patches=[patch]), _review(patches=[patch])])
    monkeypatch.setattr(guard, "_review", lambda *args, **kwargs: next(reviews))

    with pytest.raises(guard.OutputGuardError, match="remaining policy violation"):
        guard.guard_output("internal tool")
