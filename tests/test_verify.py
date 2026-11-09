from signaldesk.verify import check_quote, verify_claims

PASSAGE = (
    "Outlook\n• Revenues are expected to decline high-single digits in fiscal 2027.\n"
    "• Tax rate “mid-20” percent."
)


def test_exact_and_normalized():
    assert check_quote("Revenues are expected to decline high-single digits", PASSAGE) == "exact"
    # whitespace and curly-quote differences are forgiven, case is not
    assert check_quote('Tax rate "mid-20"   percent.', PASSAGE) == "normalized"
    assert check_quote("revenues are expected to decline high-single digits", PASSAGE) == "fail_not_found"


def test_fabricated_or_misattributed_quotes_fail():
    assert check_quote("Revenues are expected to grow strongly in fiscal 2027.", PASSAGE) == "fail_not_found"
    assert check_quote("Revenues are expected to decline", None) == "fail_missing_passage"
    assert check_quote("Revenues", PASSAGE) == "fail_too_short"


def test_verify_claims_maps_passage_ids():
    checks = verify_claims(
        [
            {"text": "down", "passage_id": "P1", "quote": "expected to decline high-single digits"},
            {"text": "up", "passage_id": "P2", "quote": "expected to decline high-single digits"},
        ],
        {"P1": PASSAGE, "P2": "unrelated text about something else entirely"},
    )
    assert [c.ok for c in checks] == [True, False]
