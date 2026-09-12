from agent_runtime import ApplicationRuntime


class _Provider:
    def manifest(self):
        return {
            "provider": "fixture",
            "capabilities": [
                {
                    "capability_id": "fixture.echo",
                    "side_effect_level": "READ_ONLY",
                    "required_permissions": [],
                },
                {
                    "capability_id": "fixture.write",
                    "side_effect_level": "LOCAL_STATE",
                    "required_permissions": ["fixture.write"],
                },
            ],
        }

    def invoke(self, capability_id, payload=None):
        return {"ok": True, "capability_id": capability_id, "payload": dict(payload or {})}


def test_application_runtime_routes_capability_and_restores_without_second_identity(tmp_path):
    app = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="trader-subject",
        allowed_permissions=frozenset({"fixture.write"}),
    )
    app.register_provider(_Provider())

    assert app.subject_id == "trader-subject"
    assert app.available_capabilities() == ("fixture.echo", "fixture.write")
    assert app.capability_manifest()[0]["capability_id"] == "fixture.echo"
    result = app.invoke("fixture.echo", {"value": 3})
    assert result.provider == "fixture"
    assert result.payload == {
        "ok": True,
        "capability_id": "fixture.echo",
        "payload": {"value": 3},
    }
    assert result.receipt_id.startswith("capability-receipt:v1:")

    app.next_cycle()
    app.persist()
    restored = ApplicationRuntime.bootstrap(
        tmp_path,
        subject_id="trader-subject",
        allowed_permissions=frozenset({"fixture.write"}),
    )
    assert restored.subject_id == "trader-subject"
    assert restored.cycle == 1
    assert restored.receipts[-1]["receipt_id"] == result.receipt_id


def test_application_runtime_enforces_permission_and_side_effect_ceiling(tmp_path):
    without_permission = ApplicationRuntime.bootstrap(tmp_path / "a", subject_id="s1")
    without_permission.register_provider(_Provider())
    try:
        without_permission.invoke("fixture.write", {})
    except PermissionError as exc:
        assert "missing capability permissions" in str(exc)
    else:
        raise AssertionError("missing permission must fail closed")

    read_only = ApplicationRuntime.bootstrap(
        tmp_path / "b",
        subject_id="s2",
        max_side_effect_level="READ_ONLY",
        allowed_permissions=frozenset({"fixture.write"}),
    )
    read_only.register_provider(_Provider())
    try:
        read_only.invoke("fixture.write", {})
    except PermissionError as exc:
        assert "exceeds runtime ceiling" in str(exc)
    else:
        raise AssertionError("side-effect ceiling must fail closed")


def test_application_runtime_rejects_duplicate_capabilities(tmp_path):
    app = ApplicationRuntime.bootstrap(tmp_path, subject_id="s1")
    app.register_provider(_Provider())

    class Duplicate:
        def manifest(self):
            return {
                "provider": "duplicate",
                "capabilities": [
                    {"capability_id": "fixture.echo", "side_effect_level": "READ_ONLY"}
                ],
            }

        def invoke(self, capability_id, payload=None):
            return {}

    try:
        app.register_provider(Duplicate())
    except ValueError as exc:
        assert "duplicate capability_id" in str(exc)
    else:
        raise AssertionError("duplicate capability registration must fail")


def test_application_runtime_rejects_state_for_another_subject(tmp_path):
    app = ApplicationRuntime.bootstrap(tmp_path, subject_id="s1")
    app.persist()
    try:
        ApplicationRuntime.bootstrap(tmp_path, subject_id="s2")
    except ValueError as exc:
        assert "subject_id mismatch" in str(exc)
    else:
        raise AssertionError("runtime state cannot be attached to another subject")
