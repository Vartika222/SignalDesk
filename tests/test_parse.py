from signaldesk.parse import html_to_text, sectionize, sectionize_10k, sectionize_10q

TENK = (
    "<html><body>"
    "<ix:header><ix:hidden>should vanish</ix:hidden></ix:header>"
    "<p>TABLE OF CONTENTS</p>"
    "<table>"
    "<tr><td>Item 1.</td><td>Business</td><td>3</td></tr>"
    "<tr><td>Item 1A.</td><td>Risk Factors</td><td>10</td></tr>"
    "<tr><td>Item 7.</td><td>Management's Discussion</td><td>20</td></tr>"
    "</table>"
    "<p>Item 1. Business</p>"
    "<p>We make widgets. " + "Widgets are great. " * 40 + "</p>"
    "<p>Item 1A. Risk Factors</p>"
    "<p>Demand for widgets may fall. " + "Risk text. " * 40 + "</p>"
    "<p>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</p>"
    "<p>Revenue grew 12%.</p>"
    "<table><tr><td>Revenue</td><td>$</td><td>1,234</td><td></td><td>(5</td><td>)%</td></tr></table>"
    "<p>See Item 1A for risks.</p>"
    "</body></html>"
)


def test_html_to_text_keeps_tables_and_drops_hidden_xbrl():
    text = html_to_text(TENK)
    assert "should vanish" not in text
    assert "Revenue | $1,234 | (5)%" in text


def test_10k_sections_skip_table_of_contents():
    secs = sectionize_10k(html_to_text(TENK))
    assert [s.section for s in secs] == ["front_matter", "business", "risk_factors", "mdna"]
    by = {s.section: s.text for s in secs}
    assert by["business"].startswith("Item 1. Business")
    assert "Demand for widgets" in by["risk_factors"]
    assert "See Item 1A for risks." in by["mdna"]  # mid-line cross reference is not a heading


TENQ = (
    "PART I - FINANCIAL INFORMATION\nItem 1. Financial Statements\n"
    + "Balance sheet line.\n" * 30
    + "Item 2. Management's Discussion and Analysis\nSales were flat.\n"
    + "MD&A text.\n" * 30
    + "PART II - OTHER INFORMATION\nItem 1. Legal Proceedings\nNone.\n"
    + "Legal text.\n" * 10
    + "Item 1A. Risk Factors\nNo material changes.\n"
    + "Risk text.\n" * 10
)


def test_10q_item_numbers_are_scoped_by_part():
    secs = {s.section: s.text for s in sectionize_10q(TENQ)}
    assert "Sales were flat." in secs["mdna"]
    assert secs["legal_proceedings"].startswith("Item 1. Legal Proceedings")
    assert "Balance sheet line." in secs["financial_statements"]
    assert "No material changes." in secs["risk_factors"]


def test_press_release_outlook_and_safe_harbor():
    text = (
        "Acme reports record quarter\nRevenue rose 10%.\n\n"
        "Business Outlook\nFor fiscal 2025 we now expect revenue of $5.0 to $5.2 billion.\n\n"
        "Forward-Looking Statements\nThis release contains forward-looking statements."
    )
    secs = sectionize(text, "8-K")
    assert [s.section for s in secs] == ["press_release", "outlook", "safe_harbor"]
    assert "now expect revenue" in secs[1].text


def test_unknown_layout_falls_back_to_body():
    assert [s.section for s in sectionize("just some text", "10-K")] == ["body"]


TOC_10Q_RUNNING_HEADERS = (
    "Part I. Financial Information\n"
    "Item 1. Financial Statements | 3\n"
    "Item 2. Management's Discussion and Analysis | 18\n"
    "Item 4. Controls and Procedures | 33\n"
    "Part II. Other Information\n"
    "Item 1. Legal Proceedings | 33\n"
    "Item 1A. Risk Factors | 33\n\n"
    "Consolidated Balance Sheets\n" + "Cash 100 | 200\n" * 60 +
    "Item 2. MD&A\nITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS\nRevenue fell 3%.\n" + "MD&A text.\n" * 40 +
    "Item 2. MD&A - Liquidity\nWe have ample liquidity.\n" + "More MD&A.\n" * 40 +
    "ITEM 4. CONTROLS AND PROCEDURES\nEffective.\n"
    "PART II. OTHER INFORMATION\nITEM 1. LEGAL PROCEEDINGS\nNone.\n"
    "ITEM 1A. RISK FACTORS\nNo material changes.\n"
)


def test_10q_toc_is_skipped_and_running_headers_stay_inside_mdna():
    secs = sectionize_10q(TOC_10Q_RUNNING_HEADERS)
    by = {s.section: s.text for s in secs}
    # body MD&A, not the TOC line, and not mislabelled as a Part II item
    assert "Revenue fell 3%." in by["mdna"] and "We have ample liquidity." in by["mdna"]
    assert "equity_repurchases" not in by
    # financial statements had no Item 1 heading in the body: kept, not dropped as front matter
    assert "Consolidated Balance Sheets" in by["unlabelled"]
    assert "Consolidated Balance Sheets" not in by["front_matter"]
    assert by["controls"].startswith("ITEM 4.")
    assert by["legal_proceedings"].startswith("ITEM 1. LEGAL")
    assert "No material changes." in by["risk_factors"]


def test_10q_part_two_inferred_when_heading_missing():
    text = ("Item 2. Management's Discussion\n" + "x\n" * 400 + "Item 4. Controls\nok\n"
            "Item 1. Legal Proceedings\nNone.\nItem 1A. Risk Factors\nSame as 10-K.\n")
    by = {s.section: s.text for s in sectionize_10q(text)}
    assert by["legal_proceedings"].startswith("Item 1. Legal")
    assert "financial_statements" not in by


def test_10k_cross_reference_layout_keeps_text_unlabelled():
    # JPM-style: TOC with page ranges, Item 1 in body, then the rest lives under other headings.
    text = ("Part I | Page\nItem 1. | Business. | 1\nItem 1A. | Risk Factors. | 9-31\n"
            "Item 7. | MD&A. | 33\n\n" + "Glossary.\n" * 80 +
            "Item 1. Business.\nWe are a bank.\n" + "b\n" * 50 + "Item 1A. Risk Factors.\nRisky.\n")
    by = {s.section: s.text for s in sectionize(text, "10-K")}
    assert by["business"].startswith("Item 1. Business.")
    assert "Glossary." in by["unlabelled"]
    assert "mdna" not in by


def test_toc_with_bare_item_lines_is_skipped():
    # PFE-style: "Item 1." alone on a line; title and page on following lines.
    toc = "PART I. FINANCIAL INFORMATION | Page\n" + "".join(
        f"Item {n}.\nSome Title\n{p}\n" for n, p in [("1", 3), ("2", 40), ("3", 70), ("4", 70)]
    ) + "PART II. OTHER INFORMATION\nItem 1.\nLegal\n71\nItem 1A.\nRisk\n71\n"
    body = (
        "\n" + "Cover note.\n" * 300 +
        "PART I. FINANCIAL INFORMATION\nITEM 1. FINANCIAL STATEMENTS\n" + "Balance sheet.\n" * 200 +
        "ITEM 2. MANAGEMENT'S DISCUSSION AND ANALYSIS\nRevenue rose.\n" + "MD&A.\n" * 200 +
        "ITEM 4. CONTROLS AND PROCEDURES\nEffective.\n"
        "PART II. OTHER INFORMATION\nITEM 1. LEGAL PROCEEDINGS\nNone.\nITEM 1A. RISK FACTORS\nSee 10-K.\n"
    )
    by = {s.section: s.text for s in sectionize_10q(toc + body)}
    assert by["mdna"].startswith("ITEM 2. MANAGEMENT") and "Revenue rose." in by["mdna"]
    assert by["financial_statements"].startswith("ITEM 1. FINANCIAL STATEMENTS")
    assert "Item 2.\nSome Title" in by["front_matter"]


def test_bare_extension_stops_at_titled_body_heading():
    # JPM-style: TOC ends, a bare "Part I" running header, then the real titled "Item 1. Business."
    text = ("Item 1. | Business . | 1\nItem 1A. | Risk Factors. | 9-31\nItem 7. | MD&A | 33\n"
            "Part I\nItem 1. Business.\nWe are a bank.\n" + "b\n" * 400 + "Item 1A. Risk Factors.\nRisky.\n")
    by = {s.section: s.text for s in sectionize(text, "10-K")}
    assert by["business"].startswith("Item 1. Business.")
