import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests off the user's real server and folder, whatever the shell exports."""
    for name in ("OLOOP_AGENT_CREDENTIALS", "OLOOP_ROOT", "OLOOP_ACTOR"):
        monkeypatch.delenv(name, raising=False)
