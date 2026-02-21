from services.policy.app import main as policy_main

def test_admin_allowed(monkeypatch):
    def tags(_w, _s): return ([], [])
    monkeypatch.setattr(policy_main, "_lookup_tags", tags)
    res = policy_main.ENGINE.evaluate(
        wall_id=1, source_id=1, operator_id="x",
        operator_roles=["admin"], operator_tags=[],
        source_tags=["S"], wall_tags=[]
    )
    assert res.allowed is True
    assert "admin_bypass" in res.reason

def test_operator_matching_tags_allowed(monkeypatch):
    def tags(_w, _s): return (["ops"], ["C","ops","briefing"])
    monkeypatch.setattr(policy_main, "_lookup_tags", tags)
    res = policy_main.evaluate(policy_main.EvalRequest(
        wall_id=1, source_id=1, operator_id="bob",
        operator_roles=["operator"], operator_tags=["C","ops","briefing","analysis"]
    ))
    assert res.allowed is True

def test_operator_missing_tags_denied(monkeypatch):
    def tags(_w, _s): return (["ops"], ["S","ops"])
    monkeypatch.setattr(policy_main, "_lookup_tags", tags)
    res = policy_main.evaluate(policy_main.EvalRequest(
        wall_id=1, source_id=1, operator_id="bob",
        operator_roles=["operator"], operator_tags=["C","ops"]
    ))
    assert res.allowed is False

def test_explicit_allow_list(monkeypatch):
    # make subset/intersect false, but explicit allow list true
    def tags(_w, _s): return (["intel"], ["S","intel"])
    monkeypatch.setattr(policy_main, "_lookup_tags", tags)
    # patch allow list to include this tuple
    policy_main.ENGINE._policy["allow_list"] = [{"operator_id":"eve","wall_id":9,"source_id":9}]
    res = policy_main.ENGINE.evaluate(
        wall_id=9, source_id=9, operator_id="eve",
        operator_roles=["viewer"], operator_tags=[],
        source_tags=["S","intel"], wall_tags=["training"]
    )
    assert res.allowed is True
    assert "rule-3-explicit-allow-list" in res.reason

def test_default_deny(monkeypatch):
    def tags(_w, _s): return ([], ["C"])
    monkeypatch.setattr(policy_main, "_lookup_tags", tags)
    res = policy_main.evaluate(policy_main.EvalRequest(
        wall_id=1, source_id=1, operator_id="v",
        operator_roles=["viewer"], operator_tags=[]
    ))
    assert res.allowed is False
