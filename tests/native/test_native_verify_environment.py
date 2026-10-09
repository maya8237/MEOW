"""Native verification reports unresolved environment references accurately."""

import os
import sys
from unittest.mock import patch

from meow.native.native import _unresolved_env, _verify_mcp


def test_unresolved_environment_supports_percent_braced_and_suffix_references():
    with patch.dict(os.environ, {}, clear=True):
        assert _unresolved_env(
            {
                "percent": "%MEOW_MISSING%",
                "braced": "${MEOW_MISSING}/suffix",
                "plain": "$MEOW_MISSING",
            }
        ) == ["MEOW_MISSING"]


def test_mcp_self_reference_is_not_satisfied_by_the_config_overlay():
    with patch.dict(os.environ, {}, clear=True):
        result = _verify_mcp(
            "test",
            {"command": sys.executable, "args": []},
            config_env={"TOKEN": "$TOKEN"},
        )

    assert result["status"] == "missing_environment"
    assert result["referenced_environment_missing"] == ["TOKEN"]
