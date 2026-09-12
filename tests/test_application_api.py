from agent_runtime import ApplicationRuntime


class _Provider:
    def manifest(self):
        return {
            "provider": "fixture",
            "capabilities": [
                {"capability_id": "fixture.echo"},
                {"capability_id": "fixture.read"},
            ],
        }

    def invoke(self, capability_id, payload=None):
        return {"ok": True, "capability_id": capability_id, "payload": dict(payload or {})}


def test_application_runtime_routes_registered_capability_and_restores(tmp_path):
    app = ApplicationRuntime.bootstrap(tmp_path)
    agent_id = app.agent_id
    app.register_provider(_Provider())

    assert app.available_capabilities() == ("fixture.echo", "fixture.read")
    result = app.invoke("fixture.echo", {"value": 3})
    assert result.provider == "fixture"
    assert result.payload == {
        "ok": True,
        "capability_id": "fixture.echo",
        "payload": {"value": 3},
    }

    app.step()
    app.persist()
    restored = ApplicationRuntime.bootstrap(tmp_path)
    assert restored.agent_id == agent_id
    assert restored.cycle == app.cycle


def test_application_runtime_rejects_duplicate_capabilities(tmp_path):
    app = ApplicationRuntime.bootstrap(tmp_path)
    app.register_provider(_Provider())

    class Duplicate:
        def manifest(self):
            return {"provider": "duplicate", "capabilities": [{"capability_id": "fixture.echo"}]}

        def invoke(self, capability_id, payload=None):
            return {}

    try:
        app.register_provider(Duplicate())
    except ValueError as exc:
        assert "duplicate capability_id" in str(exc)
    else:
        raise AssertionError("duplicate capability registration must fail")
