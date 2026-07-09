"""Lightweight contract checks for the no-build React UI."""

from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "vmux" / "web" / "index.html"


def test_command_center_surfaces_exist():
    text = HTML.read_text()
    assert "function AttentionQueue(" in text
    assert "function SwarmNavigator(" in text
    assert "function PaneInspector(" in text
    assert 'class="swarm-nav' in text
    assert 'class="attention-queue' in text
    assert 'class="pane-inspector' in text


def test_mobile_is_attention_first_with_tree_and_detail_sheets():
    text = HTML.read_text()
    assert "function MobileAttentionHome(" in text
    assert 'class="mobile-attention-home' in text
    assert 'sheetMode==="tree"' in text
    assert 'sheetMode==="detail"' in text


def test_mobile_dock_prioritizes_frequent_modes():
    text = HTML.read_text()
    start = text.index('<nav class="mobile-dock glass">')
    end = text.index("</nav>", start)
    dock = text[start:end]
    assert '["queue","Queue"' in text
    assert '["active","Active"' in text
    assert '["all","All"' in text
    assert "setMobileMode(mode)" in dock
    assert "setSheetMode" not in dock
    assert "onBroadcast" not in dock
    assert "function mobileModePanes(" in text
    assert 'mode==="active"' in text
    assert 'p.status==="working"' in text


def test_frontend_keeps_existing_action_endpoints():
    text = HTML.read_text()
    for endpoint in [
        '"/select"',
        '"/key"',
        '"/text"',
        '"/star"',
        '"/broadcast"',
        '"/config"',
        '"/sessions"',
    ]:
        assert endpoint in text


def test_frontend_offers_freeform_reply_from_queue():
    text = HTML.read_text()
    # the select-then-compose path + its focus event exist, reusing /select (no new endpoint)
    assert "selectThenCompose" in text
    assert "vmux:focus-composer" in text
    assert 'api("/select"' in text
    # the queue card renders a composer so you can type a reply without opening detail
    start = text.index("function AttentionCard(")
    end = text.index("\nfunction ", start)
    card = text[start:end]
    assert "Composer" in card


def test_frontend_surfaces_link_extraction_tools():
    text = HTML.read_text()
    assert "function extractLinks(" in text
    assert "function LinksPanel(" in text
    assert "navigator.clipboard.writeText" in text
    assert "window.open(u,\"_blank\",\"noopener\")" in text
    detail = text[text.index("function DetailBody("):text.index("\nfunction", text.index("function DetailBody(") + 1)]
    assert "extractLinks(pane.lines)" in detail
    assert "LinksPanel" in detail


def test_mobile_detail_sheet_surfaces_links_in_fullscreen_chrome():
    text = HTML.read_text()
    sheet = text[text.index("function MobileSheet(") : text.index("\nfunction", text.index("function MobileSheet(") + 1)]
    detail_sheet = text[
        text.index("function PaneDetailSheet(") : text.index("\nfunction", text.index("function PaneDetailSheet(") + 1)
    ]
    assert "actions" in sheet
    assert "sheet-actions" in sheet
    assert "extractLinks(pane.lines)" in detail_sheet
    assert "setLinksOpen" in detail_sheet
    assert "🔗 ${links.length}" in detail_sheet
    assert "linksOpen=${linksOpen}" in detail_sheet


def test_settings_exposes_scrollback_capture_setting():
    text = HTML.read_text()
    assert "capture_lines" in text
    assert "setCap" in text
    assert 'label="Scrollback"' in text
    assert "lines of history captured per pane" in text
    assert "Math.min(2000, Math.max(40" in text


def test_settings_exposes_all_pane_naming_modes():
    text = HTML.read_text()
    start = text.index('<${Row} label="Pane name"')
    end = text.index("<//>", start)
    row = text[start:end]
    assert "<select" in row
    for value in [
        "session_window_pane",
        "session_pane",
        "window_pane",
        "pane",
        "title",
        "window",
        "target",
        "command",
        "smart",
    ]:
        assert value in row


def test_settings_editors_close_fragment_wrappers():
    text = HTML.read_text()
    for name in ["ShortcutEditor", "SnippetEditor"]:
        start = text.index(f"function {name}")
        end = text.index("\n}\n\nfunction", start)
        body = text[start:end]
        assert "<//>`;" in body
