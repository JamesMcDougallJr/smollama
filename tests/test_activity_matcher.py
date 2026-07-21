"""Tests for the zero-shot activity matcher (prompt-file -> K x D matrix)."""

import os

import pytest

yaml = pytest.importorskip("yaml")
np = pytest.importorskip("numpy")

from smollama.frames.activity_matcher import ActivityMatcher

DIM = 4


def unit(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


class FakeTextEncoder:
    """Maps known phrases to one-hot embeddings for deterministic tests."""

    def __init__(self, mapping: dict[str, list[float]]):
        self._mapping = mapping

    def embed_floats(self, text: str) -> list[float]:
        return list(self._mapping[text])


PROMPTS_BASIC = """
default_threshold: 0.5
categories:
  - name: crowd
    prompts:
      - "phrase a"
  - name: fire
    threshold: 0.9
    prompts:
      - "phrase b"
  - name: empty_scene
    distractor: true
    prompts:
      - "phrase c"
"""


@pytest.fixture
def prompts_file(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(PROMPTS_BASIC)
    return path


@pytest.fixture
def encoder():
    return FakeTextEncoder({
        "phrase a": unit(0),
        "phrase b": unit(1),
        "phrase c": unit(2),
    })


def test_match_above_threshold(prompts_file, encoder):
    matcher = ActivityMatcher(prompts_file, encoder)
    result = matcher.score(unit(0))
    assert result["top_category"] == "crowd"
    assert result["matched"] is True
    assert result["top_score"] == pytest.approx(1.0)
    assert "empty_scene" in result["distractors"]
    assert "crowd" in result["scores"]


def test_below_threshold_abstains(prompts_file, encoder):
    matcher = ActivityMatcher(prompts_file, encoder)
    # A vector between fire's axis and nothing else won't clear fire's 0.9 threshold.
    half = [0.0] * DIM
    half[1] = 0.5
    half[3] = 0.5
    result = matcher.score(half)
    assert result["top_category"] == "fire"
    assert result["matched"] is False


def test_distractor_wins_abstains(prompts_file, encoder):
    matcher = ActivityMatcher(prompts_file, encoder)
    result = matcher.score(unit(2))
    assert result["top_category"] == "empty_scene"
    assert result["matched"] is False


def test_ensembling_is_renormalized_mean(tmp_path, encoder):
    path = tmp_path / "prompts.yaml"
    path.write_text("""
categories:
  - name: combo
    prompts:
      - "phrase a"
      - "phrase b"
  - name: distractor_cat
    distractor: true
    prompts:
      - "phrase c"
""")
    matcher = ActivityMatcher(path, encoder, default_threshold=0.0)
    matcher.ensure_loaded()
    expected = np.array(unit(0)) + np.array(unit(1))
    expected = expected / np.linalg.norm(expected)
    np.testing.assert_allclose(matcher._matrix[0], expected, atol=1e-6)


def test_missing_file_returns_none(tmp_path, encoder):
    matcher = ActivityMatcher(tmp_path / "does-not-exist.yaml", encoder)
    assert matcher.score(unit(0)) is None
    assert matcher.available is False


def test_corrupt_yaml_returns_none(tmp_path, encoder):
    path = tmp_path / "prompts.yaml"
    path.write_text("not: valid: yaml: at: all: [")
    matcher = ActivityMatcher(path, encoder)
    assert matcher.score(unit(0)) is None


def test_hot_reload_picks_up_edits(prompts_file, encoder):
    matcher = ActivityMatcher(prompts_file, encoder)
    result1 = matcher.score(unit(0))
    hash1 = result1["prompts_hash"]

    prompts_file.write_text(PROMPTS_BASIC + "\n  - name: extra\n    prompts:\n      - \"phrase a\"\n")
    # Ensure mtime actually advances even on fast filesystems/test runs.
    new_time = os.path.getmtime(prompts_file) + 1
    os.utime(prompts_file, (new_time, new_time))

    result2 = matcher.score(unit(0))
    assert result2["prompts_hash"] != hash1
    assert "extra" in result2["scores"]
