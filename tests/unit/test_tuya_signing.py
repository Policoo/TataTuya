from tatatuya.infrastructure.tuya.signing import RequestSigner, canonical_path, json_bytes


def test_canonical_path_sorts_and_encodes_query_parameters() -> None:
    assert canonical_path(
        "/v1.0/devices?z=last",
        {"device_ids": ["meter 1", "meter/2"], "a": "first", "none": None},
    ) == "/v1.0/devices?a=first&device_ids=meter+1%2Cmeter%2F2&z=last"


def test_json_serialization_and_signature_are_deterministic() -> None:
    body = json_bytes({"nume": "Casă", "enabled": True})
    assert body == b'{"nume":"Cas\xc4\x83","enabled":true}'
    signature = RequestSigner("client-id", "client-secret").sign(
        "post",
        "/v1.0/devices/meter-1",
        "1721124000000",
        body,
        "access-token",
    )
    assert signature == "52FA4EA6FAED28242B725AF29B61344119E7BE213EC01ED1EE972DC8F8CE6C84"
