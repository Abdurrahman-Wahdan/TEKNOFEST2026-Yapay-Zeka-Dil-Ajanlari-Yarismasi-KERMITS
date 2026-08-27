"""Dedicated metadata agent for tables the user keeps."""

from .agent import build_table_metadata_agent, generate_table_metadata
from .models import TableMetadata

__all__ = ["TableMetadata", "build_table_metadata_agent", "generate_table_metadata"]
