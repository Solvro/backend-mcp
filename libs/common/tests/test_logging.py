import json  # noqa
import logging
import pytest
from common.logging import setup_logging
from common.context import (
    request_id_var,
    user_id_var,
    session_id_var,
    trace_id_var,
)


def _root_state() -> tuple[int, list[logging.Handler]]:
    root = logging.getLogger()
    ours = [h for h in root.handlers if not type(h).__module__.startswith("_pytest")]
    return root.level, ours


@pytest.fixture(scope="module", autouse=True)
def root_state_before_module() -> tuple[int, list[logging.Handler]]:
    return _root_state()


@pytest.fixture(autouse=True)
def _restore_root_logger(root_state_before_module):
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.unit
def test_json_log_structure(capsys):
    setup_logging(service_name="test_service", log_level=logging.DEBUG)
    logger = logging.getLogger("test_logger")

    logger.debug("Test log message")

    captured = capsys.readouterr()
    log_record = json.loads(captured.out.strip())

    assert log_record["message"] == "Test log message"
    assert log_record["level"] == "DEBUG"
    assert log_record["logger"] == "test_logger"
    assert "timestamp" in log_record


@pytest.mark.unit
def test_context_variables_in_log(capsys):
    setup_logging(service_name="test_service", log_level=logging.DEBUG)

    t1 = request_id_var.set("test_request_id")
    t2 = user_id_var.set("test_user_id")
    t3 = session_id_var.set("test_session_id")
    t4 = trace_id_var.set("test_trace_id")

    try:
        logger = logging.getLogger("test_logger")
        logger.warning("Context test warning")
    finally:
        request_id_var.reset(t1)
        user_id_var.reset(t2)
        session_id_var.reset(t3)
        trace_id_var.reset(t4)

    captured = capsys.readouterr()
    log_record = json.loads(captured.out.strip())

    assert log_record["request_id"] == "test_request_id"
    assert log_record["user_id"] == "test_user_id"
    assert log_record["session_id"] == "test_session_id"
    assert log_record["trace_id"] == "test_trace_id"


@pytest.mark.unit
def test_setup_logging_tests_do_not_leak_root_logger_state(root_state_before_module):
    assert _root_state() == root_state_before_module
