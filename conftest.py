import os

import pytest


def _all_settings_classes() -> list[type]:
    from common.settings import CommonSettings

    seen: list[type] = []
    stack: list[type] = [CommonSettings]
    while stack:
        cls = stack.pop()
        seen.append(cls)
        stack.extend(cls.__subclasses__())
    return seen


@pytest.fixture(scope="session")
def _settings_surface() -> tuple[list[type], set[str]]:
    classes = _all_settings_classes()
    names = {f.upper() for cls in classes for f in cls.model_fields}
    names |= {f"{n}_FILE" for n in names}
    return classes, names


@pytest.fixture(autouse=True)
def _hermetic_settings(request, _settings_surface):
    # `live` tests exist to reach real services with the developer's real configuration
    # (MCP_SERVER_URL, LANGFUSE_*), so they are the one kind left untouched.
    if request.node.get_closest_marker("live"):
        yield
        return

    classes, names = _settings_surface
    env_files = [(cls, cls.model_config.get("env_file")) for cls in classes]
    for cls, _ in env_files:
        cls.model_config["env_file"] = None
    removed = {k: os.environ.pop(k) for k in list(os.environ) if k in names}

    yield

    os.environ.update(removed)
    for cls, previous in env_files:
        cls.model_config["env_file"] = previous
