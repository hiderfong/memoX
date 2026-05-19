"""API schema regression tests."""

import warnings

from fastapi.routing import APIRoute

from src.web.api import app


def test_http_routes_have_unique_method_path_pairs() -> None:
    seen: set[tuple[str, str]] = set()
    duplicates: list[tuple[str, str]] = []

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            key = (method, route.path)
            if key in seen:
                duplicates.append(key)
            seen.add(key)

    assert duplicates == []


def test_openapi_schema_has_no_duplicate_operation_warnings() -> None:
    app.openapi_schema = None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        schema = app.openapi()

    duplicate_warnings = [
        warning for warning in caught if "Duplicate Operation ID" in str(warning.message)
    ]
    assert duplicate_warnings == []
    assert "/api/memories" in schema["paths"]
