from scripts import bench_chat
from app.core.usage import UsageCounter


class FakeAssistant:
    """Reports two model calls the way a RemoteAgent does: into the run's UsageCounter."""

    class checkpointer:
        @staticmethod
        async def adelete_thread(thread_id):
            pass

    async def astream(self, input, *, config, **kwargs):
        for callback in config["callbacks"]:
            if isinstance(callback, UsageCounter):
                callback.add({"model_calls": 2})
        return
        yield


async def test_remote_model_calls_are_counted(monkeypatch):
    monkeypatch.setattr(bench_chat, "recall_facts", lambda user_id: [])
    result = await bench_chat.run_scenario(FakeAssistant(), None, "u", "generic", 1)
    assert result.error == "" and result.model_calls == 2
