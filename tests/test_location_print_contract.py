from types import SimpleNamespace

import pytest


@pytest.fixture
def app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-123456789012345678901234")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    import app

    app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    return app


def _location_card_version(**overrides):
    values = dict(
        locatiekaart_versie_id=1,
        voorraad_positie_id=91,
        materiaaltype="KANBAN",
        min_level=5,
        status="PENDING_PRINT",
        artikelnaam="Verband",
        artikel_foto_url="data:image/png;base64,QUJD",
        bedrijfslogo_url="data:image/png;base64,REVG",
        vestiging_naam="Vestiging",
        ruimte_naam="1 Behandelkamer",
        opslaglocatie_naam="Kast A",
        kamertype_naam="Behandeling",
        kamertype_kleur="#123456",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_location_card_contract_builds_a4_batch_without_layout_coordinates_or_max(
    app_module,
):
    versions = [
        _location_card_version(),
        _location_card_version(
            locatiekaart_versie_id=2,
            voorraad_positie_id=92,
            materiaaltype="STANDAARD",
            min_level=None,
            artikelnaam="Naaldencontainer",
            opslaglocatie_naam="Lade B",
        ),
    ]

    payload = app_module.build_location_cards_payload(
        versions,
        print_batch_id="batch-123",
    )

    assert payload["printBatchId"] == "batch-123"
    assert payload["printerId"] == "location-cards-a4-01"
    assert payload["cardType"] == "LOCATION_A4_DUPLEX"
    assert payload["options"] == {
        "paper": "A4",
        "orientation": "portrait",
        "cardSize": {"widthMm": 90, "heightMm": 60},
        "cardsPerSheet": 8,
        "color": True,
        "duplex": True,
        "duplexFlip": "long-edge",
    }
    assert payload["cards"][0] == {
        "cardId": "1",
        "product": {
            "name": "Verband",
            "image": {"base64Data": "data:image/png;base64,QUJD"},
        },
        "company": {
            "logo": {"base64Data": "data:image/png;base64,REVG"},
        },
        "materialType": "KANBAN",
        "logistics": {"minLevel": 5},
        "location": {
            "branchName": "Vestiging",
            "roomName": "1 Behandelkamer",
            "storageLocationName": "Kast A",
        },
        "roomType": {
            "name": "Behandeling",
            "color": "#123456",
        },
    }
    assert payload["cards"][1]["cardId"] == "2"
    assert payload["cards"][1]["materialType"] == "STANDAARD"
    assert "logistics" not in payload["cards"][1]
    assert "maxLevel" not in str(payload)
    assert "voorraad_positie_id" not in str(payload)
    assert "x" not in payload["cards"][0]


def test_location_card_contract_inlines_remote_images_as_base64(
    app_module,
    monkeypatch,
):
    class FakeResponse:
        headers = {"Content-Type": "image/png"}
        content = b"png-bytes"

        def raise_for_status(self):
            return None

    requested_urls = []

    def fake_get(url, timeout):
        requested_urls.append((url, timeout))
        return FakeResponse()

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    payload = app_module.build_location_cards_payload([
        _location_card_version(
            artikel_foto_url="https://images.test/product.png",
            bedrijfslogo_url="https://images.test/logo.png",
        ),
    ])

    assert requested_urls == [
        ("https://images.test/product.png", app_module.PRINT_REQUEST_TIMEOUT),
        ("https://images.test/logo.png", app_module.PRINT_REQUEST_TIMEOUT),
    ]
    assert payload["cards"][0]["product"]["image"] == {
        "base64Data": "data:image/png;base64,cG5nLWJ5dGVz",
    }
    assert payload["cards"][0]["company"]["logo"] == {
        "base64Data": "data:image/png;base64,cG5nLWJ5dGVz",
    }


def test_location_card_contract_posts_to_separate_endpoint_and_returns_metadata(
    app_module,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        status_code = 202

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "printBatchId": "batch-123",
                "jobId": "job-456",
                "status": "ACCEPTED",
                "cardCount": 1,
                "sheetCount": 1,
            }

    def fake_post(url, json, headers, timeout, allow_redirects):
        captured.update(
            url=url,
            payload=json,
            headers=headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        return FakeResponse()

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test/api/v1/print-card")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(app_module.requests, "post", fake_post)

    sent, error, metadata = app_module.send_location_cards_to_print_service(
        [_location_card_version()],
        print_batch_id="batch-123",
    )

    assert sent is True
    assert error is None
    assert metadata == {
        "printBatchId": "batch-123",
        "jobId": "job-456",
        "status": "ACCEPTED",
        "cardCount": 1,
        "sheetCount": 1,
    }
    assert captured["url"] == "http://print.test/api/v1/print-location-cards"
    assert captured["payload"]["printBatchId"] == "batch-123"
    assert captured["payload"]["cardType"] == "LOCATION_A4_DUPLEX"
    assert captured["allow_redirects"] is False


def test_location_card_contract_retries_same_batch_id_idempotently(
    app_module,
    monkeypatch,
):
    submitted_batch_ids = []
    physical_jobs = {}

    class FakeResponse:
        status_code = 202

        def __init__(self, metadata):
            self.metadata = metadata

        def raise_for_status(self):
            return None

        def json(self):
            return self.metadata

    def fake_post(url, json, headers, timeout, allow_redirects):
        batch_id = json["printBatchId"]
        submitted_batch_ids.append(batch_id)
        if batch_id not in physical_jobs:
            physical_jobs[batch_id] = {
                "printBatchId": batch_id,
                "jobId": "job-once",
                "status": "ACCEPTED",
                "cardCount": 1,
                "sheetCount": 1,
            }
        return FakeResponse(physical_jobs[batch_id])

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(app_module.requests, "post", fake_post)

    first = app_module.send_location_cards_to_print_service(
        [_location_card_version()],
        print_batch_id="retryable-batch",
    )
    second = app_module.send_location_cards_to_print_service(
        [_location_card_version()],
        print_batch_id="retryable-batch",
    )

    assert first == second == (
        True,
        None,
        {
            "printBatchId": "retryable-batch",
            "jobId": "job-once",
            "status": "ACCEPTED",
            "cardCount": 1,
            "sheetCount": 1,
        },
    )
    assert submitted_batch_ids == ["retryable-batch", "retryable-batch"]
    assert len(physical_jobs) == 1


def test_location_card_contract_supports_more_than_one_a4_sheet_and_configured_printer(
    app_module,
    monkeypatch,
):
    monkeypatch.setattr(
        app_module,
        "LOCATION_CARDS_PRINTER_ID",
        "location-cards-a4-02",
    )
    versions = [
        _location_card_version(
            locatiekaart_versie_id=index,
            voorraad_positie_id=90 + index,
        )
        for index in range(1, 10)
    ]

    payload = app_module.build_location_cards_payload(versions)

    assert payload["printerId"] == "location-cards-a4-02"
    assert len(payload["cards"]) == 9
    assert payload["options"]["cardsPerSheet"] == 8


def test_location_card_contract_generates_a_unique_batch_id_when_omitted(
    app_module,
):
    first = app_module.build_location_cards_payload([_location_card_version()])
    second = app_module.build_location_cards_payload([_location_card_version()])

    assert first["printBatchId"]
    assert first["printBatchId"] != second["printBatchId"]


def test_location_card_contract_rejects_invalid_batch_and_response_metadata(
    app_module,
    monkeypatch,
):
    with pytest.raises(ValueError, match="minimaal één kaart"):
        app_module.build_location_cards_payload([], "batch-1")

    class FakeResponse:
        status_code = 202
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "printBatchId": "other-batch",
                "jobId": "job-1",
                "status": "ACCEPTED",
                "cardCount": 1,
                "sheetCount": 1,
            }

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    sent, error, metadata = app_module.send_location_cards_to_print_service(
        [_location_card_version()],
        print_batch_id="batch-1",
    )

    assert sent is False
    assert "afwijkende printBatchId" in error
    assert metadata is None


def test_location_card_contract_reports_printservice_http_errors(
    app_module,
    monkeypatch,
):
    class FakeResponse:
        status_code = 503
        headers = {}

        def raise_for_status(self):
            raise app_module.requests.HTTPError("service unavailable")

    monkeypatch.setattr(app_module, "PRINT_SERVICE_URL", "http://print.test")
    monkeypatch.setattr(app_module, "PRINT_SERVICE_REQUIRE_API_KEY", False)
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: FakeResponse(),
    )

    sent, error, metadata = app_module.send_location_cards_to_print_service(
        [_location_card_version()],
        print_batch_id="batch-1",
    )

    assert sent is False
    assert "Printservice fout" in error
    assert metadata is None
