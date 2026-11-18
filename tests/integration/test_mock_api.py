import pytest
from apps.mock_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_counters():
    """各テスト実行前にモックの呼び出し回数をリセットする。"""
    client.post("/mock/reset")
    yield
    client.post("/mock/reset")


def test_mock_get_success():
    response = client.get("/mock/external/sample")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["sample_id"] == "SAMPLE-001"


def test_mock_post_success_does_not_echo_or_store_value():
    secret_input = "SENSITIVE_TEST_VALUE_999"
    response = client.post("/mock/external/sample", json={"value": secret_input})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "accepted",
        "source": "mock_external_api",
        "received": True,
    }
    assert secret_input not in response.text


def test_mock_validation_error_scenario():
    response = client.get(
        "/mock/external/sample", params={"scenario": "validation_error"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_INPUT"


def test_mock_post_empty_value_error():
    response = client.post("/mock/external/sample", json={"value": "   "})
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_INPUT"


def test_mock_undefined_scenario_returns_400():
    response = client.get(
        "/mock/external/sample", params={"scenario": "unknown_scenario_xyz"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_INPUT"


def test_mock_transient_error_scenario():
    response = client.get(
        "/mock/external/sample", params={"scenario": "transient_error"}
    )
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "EXTERNAL_SERVICE_UNAVAILABLE"


def test_mock_retry_then_success_scenario():
    params = {
        "scenario": "retry_then_success",
        "attempt_key": "test-key-1",
        "fail_until": 2,
    }

    # 1回目・2回目は503エラーになること
    res1 = client.get("/mock/external/sample", params=params)
    assert res1.status_code == 503

    res2 = client.get("/mock/external/sample", params=params)
    assert res2.status_code == 503

    # 3回目は200で成功すること
    res3 = client.get("/mock/external/sample", params=params)
    assert res3.status_code == 200
    assert res3.json()["status"] == "success"
