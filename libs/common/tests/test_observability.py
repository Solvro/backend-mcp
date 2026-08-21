import sys
import types

import pytest
from common import observability
from common.observability import (
    get_langfuse,
    is_langfuse_enabled,
    shutdown_langfuse,
)
from common.settings import CommonSettings

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_singleton():
    observability._reset_for_tests()
    yield
    observability._reset_for_tests()


def _settings(**overrides) -> CommonSettings:
    base = dict(langfuse_secret_key="", langfuse_public_key="", langfuse_host="http://lf")
    base.update(overrides)
    return CommonSettings(**base)


class FakeLangfuse:
    instances: list["FakeLangfuse"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.shutdown_called = False
        FakeLangfuse.instances.append(self)

    def shutdown(self):
        self.shutdown_called = True


@pytest.fixture
def fake_langfuse(monkeypatch):
    FakeLangfuse.instances = []
    module = types.ModuleType("langfuse")
    module.Langfuse = FakeLangfuse
    monkeypatch.setitem(sys.modules, "langfuse", module)
    return FakeLangfuse


def test_disabled_without_keys() -> None:
    assert get_langfuse(_settings()) is None
    assert is_langfuse_enabled(_settings()) is False


def test_disabled_with_only_public_key() -> None:
    assert get_langfuse(_settings(langfuse_public_key="pk")) is None


def test_disabled_with_only_secret_key() -> None:
    assert get_langfuse(_settings(langfuse_secret_key="sk")) is None


def test_enabled_with_both_keys(fake_langfuse):
    client = get_langfuse(
        _settings(langfuse_secret_key="sk", langfuse_public_key="pk", langfuse_host="http://lf")
    )

    assert client is not None
    assert client.kwargs == {
        "public_key": "pk",
        "secret_key": "sk",
        "host": "http://lf",
    }


def test_single_init_per_process(fake_langfuse) -> None:
    settings = _settings(langfuse_secret_key="sk", langfuse_public_key="pk")

    first = get_langfuse(settings)
    second = get_langfuse(settings)

    assert first is second
    assert len(fake_langfuse.instances) == 1


def test_missing_package_degrades_to_disabled(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)

    assert get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk")) is None


def test_init_failure_degrades_to_disabled(monkeypatch) -> None:
    class Boom:
        def __init__(self, **kwargs):
            raise RuntimeError("cannot reach langfuse")

    module = types.ModuleType("langfuse")
    module.Langfuse = Boom
    monkeypatch.setitem(sys.modules, "langfuse", module)

    assert get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk")) is None


def test_shutdown_flushes_and_resets(fake_langfuse):
    client = get_langfuse(_settings(langfuse_secret_key="sk", langfuse_public_key="pk"))

    shutdown_langfuse()

    assert client.shutdown_called is True
    assert get_langfuse(_settings()) is None


def test_shutdown_is_safe_when_disabled() -> None:
    assert get_langfuse(_settings()) is None
    shutdown_langfuse()
