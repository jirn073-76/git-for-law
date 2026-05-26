"""Shared pytest fixtures for git-for-law-austria tests."""

import pytest


@pytest.fixture
def sample_diff_ansi_output():
    """Expected ANSI-colored diff output patterns."""
    return {
        "addition_prefix": "\033[32m+",
        "deletion_prefix": "\033[31m-",
        "header_prefix": "\033[1m",
        "reset": "\033[0m",
    }
