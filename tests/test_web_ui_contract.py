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


def test_settings_editors_close_fragment_wrappers():
    text = HTML.read_text()
    for name in ["ShortcutEditor", "SnippetEditor"]:
        start = text.index(f"function {name}")
        end = text.index("\n}\n\nfunction", start)
        body = text[start:end]
        assert "<//>`;" in body
