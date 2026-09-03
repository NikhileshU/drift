"""P8-X1: read_config's own contract — a non-mapping config.yaml raises ConfigError
rather than handing every `.get(...)` caller an AttributeError, and PyYAML's anchor
sharing (not deep-copying) means the classic "billion laughs" blowup doesn't apply to
`safe_load` here.
"""
import time

import pytest

from getdrift.paths import ConfigError, read_config


def _write(drift, text):
    drift.mkdir(parents=True, exist_ok=True)
    (drift / "config.yaml").write_text(text)


def test_absent_config_returns_empty_dict(tmp_path):
    assert read_config(tmp_path / ".drift") == {}


def test_empty_config_file_returns_empty_dict(tmp_path):
    drift = tmp_path / ".drift"
    _write(drift, "")
    assert read_config(drift) == {}


def test_null_config_returns_empty_dict(tmp_path):
    """A config.yaml containing only `null` (or `~`) parses to None, same as empty —
    still "nothing configured", not a broken shape."""
    drift = tmp_path / ".drift"
    _write(drift, "null\n")
    assert read_config(drift) == {}


def test_ordinary_mapping_config_is_returned_as_is(tmp_path):
    drift = tmp_path / ".drift"
    _write(drift, "diff_threshold: 0.1\nauto_diff: false\n")
    assert read_config(drift) == {"diff_threshold": 0.1, "auto_diff": False}


@pytest.mark.parametrize(
    "yaml_text",
    [
        "- a\n- b\n- c\n",  # top-level list
        "just a plain string\n",  # top-level scalar string
        "42\n",  # top-level number
        "true\n",  # top-level bool
    ],
)
def test_non_mapping_top_level_raises_config_error(tmp_path, yaml_text):
    drift = tmp_path / ".drift"
    _write(drift, yaml_text)
    with pytest.raises(ConfigError) as exc:
        read_config(drift)
    assert "config.yaml" in str(exc.value)


def test_config_error_names_the_actual_type(tmp_path):
    drift = tmp_path / ".drift"
    _write(drift, "- a\n- b\n")
    with pytest.raises(ConfigError, match="list"):
        read_config(drift)


# --- YAML anchor/alias expansion: measured, not assumed ----------------------------


def test_deeply_doubling_anchor_aliases_do_not_blow_up(tmp_path):
    """The "billion laughs" shape: each level's sequence aliases the previous level
    twice, so a naive expander would materialize 2^60 leaf references. PyYAML's
    SafeLoader shares the underlying list object across aliases instead of deep-copying
    it, so this is a non-issue in practice — measured here, not assumed, and this test
    is the regression guard if a future PyYAML version ever changes that.
    """
    lines = ["a0: &a0 [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]"]
    for i in range(1, 61):
        lines.append(f"a{i}: &a{i} [*a{i - 1}, *a{i - 1}]")
    drift = tmp_path / ".drift"
    _write(drift, "\n".join(lines) + "\n")

    start = time.time()
    config = read_config(drift)
    elapsed = time.time() - start

    assert elapsed < 2.0, f"took {elapsed:.2f}s — anchor expansion may no longer be shared"
    assert len(config["a60"]) == 2
    # The whole point: repeated aliases share one object, they aren't copied.
    assert config["a60"][0] is config["a60"][1]
