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


@pytest.fixture(autouse=True, scope="session")
def _hermetic_settings():
    classes = _all_settings_classes()

    env_files = [(cls, cls.model_config.get("env_file")) for cls in classes]
    for cls, _ in env_files:
        cls.model_config["env_file"] = None

    names = {f.upper() for cls in classes for f in cls.model_fields}
    names |= {f"{n}_FILE" for n in names}
    removed = {k: os.environ.pop(k) for k in list(os.environ) if k in names}

    yield

    os.environ.update(removed)
    for cls, previous in env_files:
        cls.model_config["env_file"] = previous
