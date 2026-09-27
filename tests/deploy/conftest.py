import shutil

import pytest
from deploy_harness import Agent, Sandbox, stack_conf


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


@pytest.fixture
def agent(tmp_path) -> Agent:
    if shutil.which("jq") is None:
        pytest.skip("the deploy agent needs jq")
    a = Agent(tmp_path)
    a.write_conf("backend", stack_conf())
    return a
