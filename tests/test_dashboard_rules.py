"""Tests for the rules and feedback dashboard surfaces.

These are the approval surface for Phases 2 and 3: a proposed rule has to be
promotable by a human, and an observation has to be markable useful or not. Without
them the store exists but nothing can act on it.

Verbs live in the URL path rather than a form body deliberately — `request.form()`
requires python-multipart, which the dashboard extra does not depend on.
"""

import pytest

from smollama.config import Config
from smollama.memory import LocalStore, MockEmbeddings
from smollama.rules import RuleStore

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from smollama.dashboard.app import create_app  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    cfg = Config()
    cfg.memory.db_path = str(tmp_path / "memory.db")
    store = LocalStore(cfg.memory.db_path, "test-node", MockEmbeddings())
    store.connect()
    rules = RuleStore(cfg.memory.db_path)
    rules.connect()
    client = TestClient(create_app(cfg, store=store, rules=rules))
    yield client, store, rules
    rules.close()
    store.close()


class TestRulesPage:
    def test_renders_with_no_rules(self, ctx):
        client, _, _ = ctx
        resp = client.get("/rules")
        assert resp.status_code == 200
        assert "No rules yet" in resp.text

    def test_degrades_gracefully_without_a_rule_store(self, tmp_path):
        cfg = Config()
        cfg.memory.db_path = str(tmp_path / "m.db")
        client = TestClient(create_app(cfg))
        resp = client.get("/rules")
        assert resp.status_code == 200
        assert "not connected" in resp.text

    def test_groups_rules_by_state(self, ctx):
        client, _, rules = ctx
        a = rules.propose("s:one", "flatline", "flat")
        b = rules.propose("s:two", "trend", "above")
        rules.promote(b.id)
        html = client.get("/rules").text
        assert "s:one" in html and "s:two" in html
        assert "Proposed" in html and "Active" in html

    def test_shows_the_rationale_and_reason(self, ctx):
        client, _, rules = ctx
        r = rules.propose("s:one", "flatline", "flat", rationale="stuck sensor check")
        rules.promote(r.id)
        rules.mute(r.id, "fires constantly")
        html = client.get("/rules").text
        assert "stuck sensor check" in html
        assert "fires constantly" in html

    def test_structural_rules_show_no_threshold(self, ctx):
        """flatline and stale have nothing to compare against a number."""
        rules = ctx[2]
        rules.propose("s:one", "flatline", "flat")
        assert "structural" in ctx[0].get("/rules").text


class TestRuleActions:
    def test_promote(self, ctx):
        client, _, rules = ctx
        r = rules.propose("s", "trend", "above")
        assert client.post(f"/api/rules/{r.id}/promote",
                           follow_redirects=False).status_code == 303
        assert rules.get(r.id).state == "active"

    def test_mute_records_an_auditable_reason(self, ctx):
        client, _, rules = ctx
        r = rules.propose("s", "trend", "above")
        rules.promote(r.id)
        client.post(f"/api/rules/{r.id}/mute", follow_redirects=False)
        assert rules.get(r.id).state == "muted"
        assert rules.get(r.id).state_reason, "state change with no reason is unauditable"

    def test_retire_keeps_the_row(self, ctx):
        client, _, rules = ctx
        r = rules.propose("s", "trend", "above")
        client.post(f"/api/rules/{r.id}/retire", follow_redirects=False)
        assert rules.get(r.id) is not None
        assert rules.get(r.id).state == "retired"

    def test_unknown_action_rejected(self, ctx):
        client, _, rules = ctx
        r = rules.propose("s", "trend", "above")
        assert client.post(f"/api/rules/{r.id}/explode",
                           follow_redirects=False).status_code == 400

    def test_missing_rule_is_404_not_500(self, ctx):
        client, _, _ = ctx
        assert client.post("/api/rules/9999/promote",
                           follow_redirects=False).status_code == 404

    def test_returns_503_without_a_store(self, tmp_path):
        cfg = Config()
        cfg.memory.db_path = str(tmp_path / "m.db")
        client = TestClient(create_app(cfg))
        assert client.post("/api/rules/1/promote",
                           follow_redirects=False).status_code == 503


class TestFeedbackControl:
    def test_keep_and_dismiss_are_recorded(self, ctx):
        client, store, _ = ctx
        oid = store.add_observation("something", "anomaly", 0.9, ["a:b"])
        client.post(f"/api/observations/{oid}/feedback/keep", follow_redirects=False)
        assert store.get_feedback(oid) == "keep"
        client.post(f"/api/observations/{oid}/feedback/dismiss", follow_redirects=False)
        assert store.get_feedback(oid) == "dismiss"

    def test_invalid_verdict_rejected(self, ctx):
        client, store, _ = ctx
        oid = store.add_observation("x", "anomaly", 0.9, ["a:b"])
        assert client.post(f"/api/observations/{oid}/feedback/wat",
                           follow_redirects=False).status_code == 400

    def test_control_renders_current_verdict(self, ctx):
        client, store, _ = ctx
        oid = store.add_observation("x", "anomaly", 0.9, ["a:b"])
        assert "Not useful" in client.get("/observations").text
        client.post(f"/api/observations/{oid}/feedback/keep", follow_redirects=False)
        assert "click either to change" in client.get("/observations").text

    def test_redirects_back_to_the_referring_page(self, ctx):
        client, store, _ = ctx
        oid = store.add_observation("x", "anomaly", 0.9, ["a:b"])
        resp = client.post(
            f"/api/observations/{oid}/feedback/keep",
            headers={"referer": "/observations?hours=24"},
            follow_redirects=False,
        )
        assert resp.headers["location"] == "/observations?hours=24"


class TestCreatingARuleFromASignal:
    """Before this, nothing in the running system could call `store.propose` —
    `author_rules` is the only caller and nothing calls *it*. With no rule able to
    exist, `uncovered_signals` could never filter anything, so every firing signal
    cost an inference every cycle forever. This is the surface that closes it.
    """

    PARAMS = {
        "source": "system:mem_percent",
        "detector": "level_shift",
        "direction": "above",
    }

    def test_watch_creates_an_active_rule(self, ctx):
        """Active is the point of the click: only active rules cover a signal, so
        proposing alone would leave it still being narrated."""
        client, _, rules = ctx
        resp = client.post("/api/signals/watch", params=self.PARAMS,
                           follow_redirects=False)
        assert resp.status_code == 303
        rule = rules.find(**{"full_id": self.PARAMS["source"],
                             "detector": "level_shift", "direction": "above"})
        assert rule is not None
        assert rule.state == "active"

    def test_a_watched_signal_stops_being_narrated(self, ctx):
        from smollama.detectors import Signal
        from smollama.rules import uncovered_signals
        from datetime import datetime, timezone

        client, _, rules = ctx
        client.post("/api/signals/watch", params=self.PARAMS)
        sig = Signal(source="system:mem_percent", detector="level_shift",
                     score=4.0, direction="above", detail="moved",
                     first_seen=datetime.now(timezone.utc))
        assert uncovered_signals([sig], rules) == []

    def test_propose_leaves_it_for_later(self, ctx):
        client, _, rules = ctx
        client.post("/api/signals/propose", params=self.PARAMS)
        rule = rules.find(self.PARAMS["source"], "level_shift", "above")
        assert rule.state == "proposed"

    def test_the_rationale_records_who_decided(self, ctx):
        """The reason log is the only audit trail for why a source stopped being
        reported on."""
        client, _, rules = ctx
        client.post("/api/signals/watch", params=self.PARAMS)
        rule = rules.find(self.PARAMS["source"], "level_shift", "above")
        assert "dashboard" in (rule.rationale or "").lower()

    def test_watching_twice_is_harmless(self, ctx):
        client, _, rules = ctx
        client.post("/api/signals/watch", params=self.PARAMS)
        client.post("/api/signals/watch", params=self.PARAMS)
        assert len(rules.all_rules()) == 1

    def test_re_proposing_does_not_reactivate_a_muted_rule(self, ctx):
        """`propose` is documented not to resurrect state, and the dashboard must
        not route around that by promoting on every click."""
        client, _, rules = ctx
        r = rules.propose(self.PARAMS["source"], "level_shift", "above")
        rules.mute(r.id, "known normal")
        client.post("/api/signals/propose", params=self.PARAMS)
        assert rules.get(r.id).state == "muted"

    def test_unknown_action_is_rejected(self, ctx):
        client, _, _ = ctx
        resp = client.post("/api/signals/frobnicate", params=self.PARAMS)
        assert resp.status_code == 400

    def test_requires_a_rule_store(self, tmp_path):
        cfg = Config()
        cfg.memory.db_path = str(tmp_path / "m.db")
        client = TestClient(create_app(cfg))
        resp = client.post("/api/signals/watch", params=self.PARAMS)
        assert resp.status_code == 503

    def test_the_signal_list_renders_its_controls(self, ctx):
        """The other tests here run against an empty database, where the signal
        list is empty and this whole block is skipped."""
        from datetime import datetime, timezone
        from unittest.mock import patch

        from smollama.detectors import Signal

        client, _, _ = ctx
        sig = Signal(
            source="system:mem_percent", detector="level_shift", score=4.0,
            direction="above", detail="moved to 42.2 from a baseline of 86",
            first_seen=datetime.now(timezone.utc),
            meta={"correlated": ["system:mem_available_mb"]},
        )
        with patch("smollama.detectors.detect_all", return_value=[sig]):
            resp = client.get("/rules")

        assert resp.status_code == 200
        # Colons must survive into the query string, or the rule identity is wrong.
        assert "/api/signals/watch?source=system%3Amem_percent" in resp.text
        assert "/api/signals/propose?source=system%3Amem_percent" in resp.text
        # The suppressed twin is shown, not silently dropped.
        assert "same event as system:mem_available_mb" in resp.text
