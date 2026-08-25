"""Dedicated persistent agent for next-message recommendations."""

from .agent import (
    build_recommendation_agent,
    generate_recommendation,
    recommendation_compaction,
    recommendation_thread_id,
)
from .models import ConversationRecommendation

__all__ = [
    "ConversationRecommendation",
    "build_recommendation_agent",
    "generate_recommendation",
    "recommendation_compaction",
    "recommendation_thread_id",
]
