from __future__ import annotations

import pytest

from voxora.state import (
    CAMPAIGN_TRANSITIONS,
    InvalidTransition,
    assert_transition,
)


def test_campaign_lifecycle_transitions_are_explicit() -> None:
    assert_transition(CAMPAIGN_TRANSITIONS, "draft", "running")
    assert_transition(CAMPAIGN_TRANSITIONS, "running", "paused")
    assert_transition(CAMPAIGN_TRANSITIONS, "paused", "running")

    with pytest.raises(InvalidTransition):
        assert_transition(CAMPAIGN_TRANSITIONS, "completed", "running")
    with pytest.raises(InvalidTransition):
        assert_transition(CAMPAIGN_TRANSITIONS, "draft", "completed")
