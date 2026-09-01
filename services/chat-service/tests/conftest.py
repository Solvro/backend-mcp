import pytest


@pytest.fixture(autouse=True)
def _disable_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    import common.observability as observability

    monkeypatch.setattr(observability, "_initialized", True, raising=False)
    monkeypatch.setattr(observability, "_client", None, raising=False)
