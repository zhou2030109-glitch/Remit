"""Shared file-type rules for uploaded modeling datasets."""

from pathlib import Path


DATA_FILE_SUFFIXES = frozenset(
    {".blocks", ".csv", ".nets", ".pl", ".xls", ".xlsx", ".txt"}
)
FONT_FILE_SUFFIXES = frozenset({".otf", ".ttc", ".ttf"})


def is_data_file(filename: str) -> bool:
    """Return whether a filename is a supported modeling dataset."""
    return Path(filename).suffix.lower() in DATA_FILE_SUFFIXES


def is_sandbox_upload_file(filename: str) -> bool:
    """Return whether a work-dir file must be copied into the E2B sandbox."""
    suffix = Path(filename).suffix.lower()
    return suffix in DATA_FILE_SUFFIXES or suffix in FONT_FILE_SUFFIXES
