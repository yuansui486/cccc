from __future__ import annotations

from no1.ports.web.routes.account import _api_v1_base_url


def test_api_v1_base_url_accepts_service_root() -> None:
    assert (
        _api_v1_base_url(
            "http://dongdongkc.shierkeji.com:6201/ocs",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "http://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )


def test_api_v1_base_url_accepts_api_root() -> None:
    assert (
        _api_v1_base_url(
            "http://dongdongkc.shierkeji.com:6201/ocs/api/v1",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "http://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )


def test_api_v1_base_url_accepts_smart_ops_root() -> None:
    assert (
        _api_v1_base_url(
            "https://dongdongkc.shierkeji.com:6201",
            service_path="ua2",
            code="invalid",
            label="UA2",
        )
        == "https://dongdongkc.shierkeji.com:6201/ua2/api/v1"
    )


def test_api_v1_base_url_accepts_smart_ops_root_for_ocs() -> None:
    assert (
        _api_v1_base_url(
            "https://dongdongkc.shierkeji.com:6201",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "https://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )
