import json
import logging

from common_platform.logger import JsonFormatter, get_logger, log_event


def test_logger_redacts_sensitive_keys_including_nested(caplog):
    caplog.set_level(logging.INFO, logger="common_platform")
    logger = get_logger()

    log_event(
        logger,
        logging.INFO,
        "custom_audit_event",
        authorization="Bearer secret-123",
        nested={"api_key": "nested-secret-456", "safe_field": "visible_value"},
    )

    formatter = JsonFormatter()
    parsed = json.loads(formatter.format(caplog.records[-1]))

    assert parsed["authorization"] == "[REDACTED]"
    assert parsed["nested"]["api_key"] == "[REDACTED]"
    assert parsed["nested"]["safe_field"] == "visible_value"
    assert "secret-123" not in json.dumps(parsed)
    assert "nested-secret-456" not in json.dumps(parsed)
