"""Cross-engine functional coverage for the adaptive vmux workspace."""

from __future__ import annotations

import json
import re

import pytest

from .conftest import BrowserRuntime, FixtureServer
from .fixture_app import fixture_panes


def open_app(page, server: FixtureServer, path: str = "/") -> None:
    page.goto(f"{server.url}{path}", wait_until="domcontentloaded")
    page.locator(".app-shell, .token-gate").first.wait_for(state="visible", timeout=15_000)


def wait_for_connection(page, label: str = "Live") -> None:
    page.get_by_role(
        "button",
        name=re.compile(rf"^Connection: {re.escape(label)}\."),
    ).wait_for(state="visible", timeout=15_000)


def test_done_queue_order_and_direct_open_acknowledgment(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    panes = fixture_panes()
    panes[3]["lifecycle"] = {
        **panes[3]["lifecycle"], "state": "done", "reason": "work_became_idle",
    }
    fixture_server.set_panes(panes)
    page = page_factory(browser_runtime, viewport=(1024, 768))
    open_app(page, fixture_server)
    wait_for_connection(page)

    cards = page.locator(".attention-card .attention-heading strong")
    assert cards.all_text_contents()[:3] == [
        "Release captain", "API investigator", "Test watcher",
    ]
    assert not any(
        row["endpoint"] == "/api/panes/lifecycle/acknowledge"
        for row in fixture_server.action_requests()
    )

    done_card = page.locator(".attention-card").filter(has_text="Test watcher")
    done_card.get_by_role("button", name="Inspect").click()
    page.wait_for_timeout(500)
    acknowledgments = [
        row for row in fixture_server.action_requests()
        if row["endpoint"] == "/api/panes/lifecycle/acknowledge"
    ]
    assert acknowledgments[-1]["body"] == {"id": "%4", "expected_revision": 8}


def test_live_sort_order_is_coalesced_but_urgent_attention_moves_immediately(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    panes = fixture_panes()
    for index, pane in enumerate(panes):
        pane["starred"] = False
        pane["updated"] = 100 - index
    fixture_server.set_panes(panes)
    page = page_factory(
        browser_runtime,
        viewport=(1024, 768),
        prefs={"defaultFilter": "all", "sort": "active"},
    )
    open_app(page, fixture_server)
    wait_for_connection(page)

    rows = page.locator(".pane-row .pane-row-copy strong")
    assert rows.all_text_contents()[:4] == [
        "Release captain", "API investigator", "Docs researcher", "Test watcher",
    ]

    panes[3]["updated"] = 300
    panes[3]["lines"].append("new terminal output")
    fixture_server.set_panes(panes)
    page.wait_for_timeout(750)
    assert rows.all_text_contents()[:4] == [
        "Release captain", "API investigator", "Docs researcher", "Test watcher",
    ]

    # The fixture emits on a 500 ms cadence; allow that delivery plus the
    # fixed two-second order window.
    page.wait_for_timeout(2200)
    assert rows.all_text_contents()[0] == "Test watcher"

    panes[2]["status"] = "needs_input"
    panes[2]["lifecycle"] = {
        **panes[2]["lifecycle"], "state": "blocked", "reason": "configured_prompt_visible",
        "authority": "terminal_ui", "confidence": "high",
        "revision": panes[2]["lifecycle"]["revision"] + 1,
    }
    panes[2]["updated"] = 400
    fixture_server.set_panes(panes)
    page.wait_for_timeout(1000)
    assert rows.all_text_contents()[0] == "Docs researcher"


def test_tmux_creation_supports_all_target_types_and_opens_the_new_pane(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(browser_runtime, viewport=(390, 844), touch=True)

    def reopen() -> None:
        fixture_server.reset()
        fixture_server.scenario("creation")
        open_app(page, fixture_server)
        wait_for_connection(page)
        page.get_by_role("button", name="Create tmux target").click()
        page.get_by_role("dialog", name=re.compile("Create tmux target")).wait_for()

    def created_body() -> dict:
        rows = [
            row for row in fixture_server.action_requests()
            if row["endpoint"] == "/api/tmux/create"
        ]
        assert len(rows) == 1
        page.get_by_role("dialog", name="vmux", exact=True).get_by_role(
            "heading", name="vmux", exact=True, level=1,
        ).wait_for(timeout=15_000)
        return rows[0]["body"]

    reopen()
    dialog = page.get_by_role("dialog", name=re.compile("Create tmux target"))
    assert dialog.get_by_role("button", name="Claude").is_disabled()
    assert dialog.get_by_text("not installed", exact=True).count() >= 1
    dialog.get_by_role("button", name="Create session").click()
    assert created_body() == {
        "type": "session",
        "cwd": "/fixture/products/vmux",
        "runtime": "shell",
        "name": None,
    }

    reopen()
    dialog = page.get_by_role("dialog", name=re.compile("Create tmux target"))
    dialog.get_by_role("group", name="Creation type").get_by_role("button", name="Window").click()
    dialog.locator(".creation-shortcuts").get_by_role("button", name="Products").click()
    dialog.get_by_label("Creation directory").fill("/fixture/products/website")
    dialog.get_by_role("button", name="Browse").click()
    dialog.get_by_role("button", name="Create window").click()
    assert created_body() == {
        "type": "window",
        "cwd": "/fixture/products/website",
        "runtime": "shell",
        "name": None,
        "parent_session": "launch",
    }

    reopen()
    dialog = page.get_by_role("dialog", name=re.compile("Create tmux target"))
    dialog.get_by_role("group", name="Creation type").get_by_role("button", name="Pane").click()
    dialog.get_by_role("group", name="Split direction").get_by_role("button", name="Stacked").click()
    slider = dialog.get_by_role("slider", name="New pane size")
    slider.fill("65")
    dialog.get_by_role("button", name="Create pane").click()
    assert created_body() == {
        "type": "pane",
        "cwd": "/fixture/products/vmux",
        "runtime": "shell",
        "parent_pane_id": "%1",
        "split": "stacked",
        "size_percent": 65,
    }

    reopen()
    dialog = page.get_by_role("dialog", name=re.compile("Create tmux target"))
    dialog.get_by_role("button", name=re.compile(r"^Antigravity")).click()
    dialog.get_by_role("button", name="Create session").click()
    assert created_body() == {
        "type": "session",
        "cwd": "/fixture/products/vmux",
        "runtime": "agy",
        "name": None,
    }

    reopen()
    dialog = page.get_by_role("dialog", name=re.compile("Create tmux target"))
    dialog.get_by_role("button", name=re.compile(r"^Grok Build")).click()
    dialog.get_by_role("button", name="Create session").click()
    assert created_body() == {
        "type": "session",
        "cwd": "/fixture/products/vmux",
        "runtime": "grok",
        "name": None,
    }


def test_width_breakpoints_ignore_pointer_type_and_reflow_at_320(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(browser_runtime, viewport=(390, 844), touch=True)
    open_app(page, fixture_server)
    wait_for_connection(page)
    assert page.locator(".compact-shell").count() == 1
    assert page.get_by_role("navigation", name="Primary").get_by_role("button").all_text_contents() == [
        "Queue2",
        "Active1",
        "All5",
        "Stats2",
    ]

    page.set_viewport_size({"width": 819, "height": 768})
    page.locator(".compact-shell").wait_for()
    page.set_viewport_size({"width": 820, "height": 768})
    page.locator(".medium-shell").wait_for()
    page.set_viewport_size({"width": 1199, "height": 800})
    page.locator(".medium-shell").wait_for()
    page.set_viewport_size({"width": 1200, "height": 800})
    page.locator(".wide-shell").wait_for()
    page.keyboard.press("Control+k")
    palette = page.get_by_role("dialog", name=re.compile("Jump to pane"))
    palette.wait_for()
    palette.get_by_role("combobox").fill("Docs researcher")
    palette.get_by_role("option", name=re.compile("Docs researcher")).wait_for()
    page.keyboard.press("Escape")
    palette.wait_for(state="detached")

    page.set_viewport_size({"width": 320, "height": 568})
    page.locator(".compact-shell").wait_for()
    dimensions = page.evaluate(
        "({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert dimensions["scroll"] <= dimensions["client"]
    assert page.locator(".compact-dock").bounding_box()["width"] <= 320


def test_agent_workspace_resume_chat_decision_and_manual_binding_are_structured(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_workspace")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/agents")
    wait_for_connection(page)

    workspace_nav = page.get_by_role("navigation", name="Workspace")
    assert workspace_nav.get_by_role("button").all_text_contents() == [
        "Agents", "Inbox1", "Panes", "Timeline", "Stats",
    ]
    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    page.get_by_role("heading", name="Authentication refactor", exact=True).wait_for()
    page.get_by_text("Integration tests passed", exact=True).first.wait_for()

    # Rendering Resume advances the shared visit, while the button itself only
    # clears/focuses the composer and never submits a message.
    page.get_by_role("button", name="Resume", exact=True).click()
    composer = page.get_by_role("textbox", name="Message this agent")
    assert composer.input_value() == ""
    page.wait_for_function("node => document.activeElement === node", arg=composer.element_handle())
    requests = fixture_server.action_requests()
    assert any(row["endpoint"].endswith("/visit") for row in requests)
    assert not any(row["endpoint"].endswith("/messages") for row in requests)

    composer.fill("Continue from current context")
    page.get_by_role("button", name="Send", exact=True).click()
    page.locator(".agent-messages").get_by_text("Continue from current context", exact=True).wait_for()
    sent = [row for row in fixture_server.action_requests() if row["endpoint"].endswith("/messages")]
    assert len(sent) == 1
    assert sent[0]["body"]["expected_binding_revision"] == 4

    workspace_nav.get_by_role("button", name=re.compile("^Inbox")).click()
    page.get_by_role("heading", name="Decision inbox", exact=True).wait_for()
    page.locator(".decision-card").filter(has_text="Choose refresh-token strategy").click()
    page.get_by_role("heading", name="Choose refresh-token strategy", exact=True).wait_for()
    page.get_by_role("radio", name=re.compile("Rotate on every use")).check()
    page.get_by_role("button", name="Send response", exact=True).click()
    page.get_by_text("This decision is resolved", exact=False).wait_for()
    reply = [row for row in fixture_server.action_requests() if row["endpoint"].endswith("/reply")]
    assert reply[-1]["body"]["option_id"] == "rotate"
    assert reply[-1]["body"]["expected_revision"] == 3
    assert reply[-1]["body"]["prompt_fingerprint"] == "fixture-refresh-v3"

    workspace_nav.get_by_role("button", name="Agents", exact=True).click()
    page.locator(".agent-card").filter(has_text="Frontend polish").get_by_role("button").click()
    page.get_by_role("heading", name="Choose the running pane", exact=True).wait_for()
    page.get_by_label("Candidate pane").select_option("%1")
    page.get_by_role("button", name="Link session", exact=True).click()
    page.get_by_text("Linked terminal", exact=True).wait_for()
    binding = [row for row in fixture_server.action_requests() if row["endpoint"].endswith("/binding")]
    assert binding[-1]["body"]["pane_id"] == "%1"
    page.get_by_role("button", name="Unlink", exact=True).click()
    page.get_by_role("heading", name="Choose the running pane", exact=True).wait_for()
    binding = [row for row in fixture_server.action_requests() if row["endpoint"].endswith("/binding")]
    assert binding[-1]["body"]["expected_binding_revision"] == 3


def test_agent_image_paste_appends_a_path_without_implicit_submission(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_workspace")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/agents")
    wait_for_connection(page)
    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    composer = page.get_by_role("textbox", name="Message this agent")
    composer.fill("Please inspect")

    prevented = composer.evaluate(
        """(node, bytes) => {
          const transfer = new DataTransfer();
          transfer.items.add(new File([new Uint8Array(bytes)], "clipboard.png", {type: "image/png"}));
          const event = new Event("paste", {bubbles: true, cancelable: true});
          Object.defineProperty(event, "clipboardData", {value: transfer});
          node.dispatchEvent(event);
          return event.defaultPrevented;
        }""",
        list(b"\x89PNG\r\n\x1a\nclipboard"),
    )
    assert prevented is True
    page.get_by_text("Image path added.", exact=False).wait_for(timeout=10_000)
    assert composer.input_value() == "Please inspect /private/tmp/vmux-fixture-image-1.png"
    dimensions = page.evaluate(
        "({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert dimensions["scroll"] <= dimensions["client"]
    requests = fixture_server.action_requests()
    image_posts = [row for row in requests if row["endpoint"] == "/api/images"]
    assert image_posts == [{
        "endpoint": "/api/images",
        "body": {"content_type": "image/png", "size": len(b"\x89PNG\r\n\x1a\nclipboard")},
    }]
    assert not any(row["endpoint"].endswith("/messages") for row in requests)

    page.get_by_role("button", name="Send", exact=True).click()
    sent = [row for row in fixture_server.action_requests() if row["endpoint"].endswith("/messages")]
    assert sent[-1]["body"]["text"] == "Please inspect /private/tmp/vmux-fixture-image-1.png"


def test_terminal_image_picker_preserves_draft_and_requires_explicit_send(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.locator(".attention-card").filter(has_text="Release captain").locator(
        ".attention-open"
    ).click()
    dialog = page.get_by_role("dialog")
    composer = dialog.get_by_role("textbox", name="Message for Release captain")
    composer.fill("Review")

    text_paste_prevented = composer.evaluate(
        """node => {
          const transfer = new DataTransfer();
          transfer.setData("text/plain", " normal text");
          const event = new Event("paste", {bubbles: true, cancelable: true});
          Object.defineProperty(event, "clipboardData", {value: transfer});
          node.dispatchEvent(event);
          return event.defaultPrevented;
        }"""
    )
    assert text_paste_prevented is False

    picker = dialog.locator("input.image-file-input")
    assert picker.get_attribute("accept") == "image/*"
    picker.set_input_files(
        {
            "name": "screen.png",
            "mimeType": "image/png",
            "buffer": b"\x89PNG\r\n\x1a\npicker",
        }
    )
    dialog.get_by_text("Image path added.", exact=False).wait_for(timeout=10_000)
    assert composer.input_value() == "Review /private/tmp/vmux-fixture-image-1.png"
    dimensions = page.evaluate(
        "({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
    )
    assert dimensions["scroll"] <= dimensions["client"]
    requests = fixture_server.action_requests()
    assert [row["body"] for row in requests if row["endpoint"] == "/api/images"] == [
        {"content_type": "image/png", "size": len(b"\x89PNG\r\n\x1a\npicker")}
    ]
    assert not any(row["endpoint"] == "/api/text" for row in requests)

    dialog.get_by_role("button", name="Send", exact=True).click()
    sent = [row for row in fixture_server.action_requests() if row["endpoint"] == "/api/text"]
    assert sent[-1]["body"] == {
        "id": "%1",
        "text": "Review /private/tmp/vmux-fixture-image-1.png",
        "enter": False,
    }


def test_image_upload_failure_retry_and_format_errors_keep_the_draft(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("image_failure")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.locator(".attention-card").filter(has_text="Release captain").locator(
        ".attention-open"
    ).click()
    dialog = page.get_by_role("dialog")
    composer = dialog.get_by_role("textbox", name="Message for Release captain")
    composer.fill("Keep this draft")
    picker = dialog.locator("input.image-file-input")
    image = {
        "name": "screen.png",
        "mimeType": "image/png",
        "buffer": b"\x89PNG\r\n\x1a\nretry",
    }
    picker.set_input_files(image)
    dialog.get_by_role("alert").get_by_text("fixture image storage unavailable", exact=False).wait_for(
        timeout=10_000
    )
    assert composer.input_value() == "Keep this draft"

    fixture_server.scenario("live")
    dialog.get_by_role("button", name="Retry image upload").click()
    dialog.get_by_text("Image path added.", exact=False).wait_for(timeout=10_000)
    assert composer.input_value().startswith("Keep this draft /private/tmp/vmux-fixture-image-")
    assert not any(row["endpoint"] == "/api/text" for row in fixture_server.action_requests())

    fixture_server.scenario("image_too_large")
    picker.set_input_files(image)
    dialog.get_by_text("larger than the 20 MiB limit", exact=False).wait_for(timeout=10_000)
    retained = composer.input_value()
    fixture_server.scenario("image_unsupported")
    picker.set_input_files(image)
    dialog.get_by_text("Use a PNG, JPEG, WebP, or GIF", exact=False).wait_for(timeout=10_000)
    assert composer.input_value() == retained


def test_agent_controls_fail_closed_without_exact_capabilities_or_reported_binding_candidates(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_safety_locked")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/agents")
    wait_for_connection(page)

    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    composer = page.get_by_role("textbox", name="Message this agent")
    composer.wait_for()
    assert composer.is_disabled()
    assert page.get_by_role("button", name="Add image").is_disabled()
    assert page.get_by_role("button", name="Send", exact=True).is_disabled()
    assert page.get_by_role("button", name="Summarize changes", exact=True).is_disabled()

    page.get_by_role("navigation", name="Workspace").get_by_role(
        "button", name=re.compile("^Inbox")
    ).click()
    page.locator(".decision-card").filter(has_text="Choose refresh-token strategy").click()
    page.get_by_text("Verified terminal reply unavailable", exact=True).wait_for()
    assert page.get_by_role("radio", name=re.compile("Rotate on every use")).is_disabled()
    assert page.get_by_role("button", name="Send response", exact=True).is_disabled()

    page.get_by_role("navigation", name="Workspace").get_by_role(
        "button", name="Agents", exact=True
    ).click()
    page.locator(".agent-card").filter(has_text="Frontend polish").get_by_role("button").click()
    page.get_by_role("heading", name="No verified pane candidates", exact=True).wait_for()
    assert page.get_by_label("Candidate pane").count() == 0
    assert page.get_by_role("button", name="Link session", exact=True).count() == 0
    assert page.get_by_role("link", name="Open terminal workspace", exact=True).count() == 1

    writes = fixture_server.action_requests()
    assert not any(row["endpoint"].endswith(("/messages", "/reply", "/binding")) for row in writes)


def test_agent_workspace_loads_all_cursor_pages_deduplicated_and_in_wire_order(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_pagination")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/agents")
    wait_for_connection(page)

    page.locator(".agent-card").filter(has_text="Database migration").wait_for()
    assert page.locator(".agent-card").count() == 3
    workspace_nav = page.get_by_role("navigation", name="Workspace")
    inbox = workspace_nav.get_by_role("button", name=re.compile(r"^Inbox\s*3$"))
    inbox.wait_for()

    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    page.get_by_text("I mapped the existing authentication flow.", exact=True).wait_for()
    assert page.locator(".agent-messages li p").all_text_contents() == [
        "I mapped the existing authentication flow.",
        "Refactor authentication safely.",
        "The API and compatibility tests are complete.",
    ]
    page.locator(".agent-local-timeline").get_by_text(
        "Authentication API updated", exact=True
    ).wait_for()
    assert page.locator(".agent-local-timeline .agent-timeline-list li").count() == 3

    inbox.click()
    page.get_by_text("Approve database migration window", exact=True).wait_for()
    page.get_by_text("Approve emergency rollback plan", exact=True).wait_for()
    assert page.locator(".decision-card").count() == 3

    workspace_nav.get_by_role("button", name="Timeline", exact=True).click()
    page.locator(".timeline-destination").get_by_text(
        "Authentication API updated", exact=True
    ).wait_for()
    assert page.locator(".timeline-destination .agent-timeline-list li").count() == 3

    reads = fixture_server.action_requests()
    expected_cursors = {
        "/api/agents": "agents-page-2",
        "/api/decisions": "decisions-page-2",
        "/api/timeline": "timeline-page-2",
        "/api/agents/agent-codex/messages": "messages-page-2",
        "/api/agents/agent-codex/timeline": "agent-timeline-page-2",
    }
    for endpoint, second_cursor in expected_cursors.items():
        cursors = [
            row["body"].get("cursor")
            for row in reads
            if row["endpoint"] == endpoint and row["body"].get("status") is None
        ]
        assert cursors[:2] == [None, second_cursor]
    decision_reads = [row["body"] for row in reads if row["endpoint"] == "/api/decisions"]
    assert {body.get("status") for body in decision_reads} == {None, "pending", "submitting"}
    assert any(
        body == {"cursor": "pending-decisions-page-2", "status": "pending"}
        for body in decision_reads
    )


def test_agent_cursor_loop_stops_after_the_repeated_page(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_cursor_loop")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/timeline")
    wait_for_connection(page)

    page.get_by_text("Cursor loop page retrieved", exact=True).wait_for()
    assert page.locator(".timeline-destination .agent-timeline-list li").count() == 2
    timeline_reads = [
        row for row in fixture_server.action_requests() if row["endpoint"] == "/api/timeline"
    ]
    assert [row["body"]["cursor"] for row in timeline_reads] == [None, "timeline-loop"]


def test_review_quick_deep_link_context_skip_and_explicit_acknowledgement(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_review")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/#/decisions/decision-refresh")
    wait_for_connection(page)

    page.get_by_role("heading", name="Review", exact=True).wait_for()
    workspace_nav = page.get_by_role("navigation", name="Workspace")
    assert workspace_nav.get_by_role("button").all_text_contents() == [
        "Agents",
        "Review2",
        "Panes",
        "Timeline",
        "Stats",
    ]
    page.get_by_text("Agents changed", exact=True).wait_for()
    page.get_by_text("Pending decisions", exact=True).wait_for()
    page.get_by_role("heading", name="Terminal review", exact=True).wait_for()
    open_pane = page.get_by_role("link", name="Open Pane", exact=True)
    assert open_pane.count() == 1
    open_pane.click()
    terminal = page.get_by_role("dialog", name=re.compile("Release captain"))
    terminal.get_by_text("Ship the reviewed change to production?", exact=True).wait_for()
    # A lifecycle acknowledgment can replace the compact detail dialog as the
    # fresh frame arrives. Close either generation before using navigation.
    for _ in range(2):
        if not page.locator(".dialog-scrim").count():
            break
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)
    page.locator(".dialog-scrim").wait_for(state="detached")
    workspace_nav.get_by_role("button", name=re.compile("^Review")).click()
    page.get_by_role("heading", name="Review", exact=True).wait_for()
    page.get_by_text("Review schedule", exact=True).click()
    schedule = page.locator(".review-schedule")
    interval = page.get_by_label("Batch normal requests")
    interval.select_option("custom")
    page.get_by_label("Minutes (5–1440)").fill("15")
    save_interval = schedule.get_by_role("button", name="Save", exact=True)
    save_interval.click()
    page.wait_for_function("node => !node.disabled", arg=interval.element_handle())
    interval.select_option("30")
    page.get_by_text("Next review", exact=False).wait_for()
    pane_error_toggle = page.get_by_label("Let pane errors bypass the timer")
    pane_error_toggle.click()
    page.wait_for_function("node => node.checked", arg=pane_error_toggle.element_handle())

    # Deep-linking, opening an agent, and inspecting retained context are reads.
    workspace_nav.get_by_role("button", name="Agents", exact=True).click()
    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    page.get_by_role("button", name="Deep Context", exact=True).click()
    dialog = page.get_by_role("dialog", name=re.compile("Deep Context"))
    dialog.get_by_text("Visible context only", exact=True).wait_for()
    assert "hidden reasoning" in dialog.text_content()
    dialog.get_by_text(
        "The API and compatibility tests are complete.", exact=True
    ).wait_for()
    assert dialog.locator(".agent-messages li").count() == 2
    dialog.get_by_role("button", name="Load older retained messages").click()
    page.wait_for_function(
        "() => document.querySelectorAll('.deep-context .agent-messages li').length === 3"
    )
    dialog.get_by_role("searchbox", name="Search").fill("API and compatibility")
    page.wait_for_function(
        "() => document.querySelectorAll('.deep-context .agent-messages li').length === 1"
    )
    assert dialog.locator(".agent-messages li").count() == 1
    # A slower obsolete search must not overwrite a newer filter response.
    dialog.get_by_role("searchbox", name="Search").fill("slow query")
    page.wait_for_timeout(320)
    dialog.get_by_role("searchbox", name="Search").fill("API and compatibility")
    page.wait_for_function(
        "() => document.querySelectorAll('.deep-context .agent-messages li').length === 1"
    )
    page.wait_for_timeout(800)
    assert dialog.locator(".agent-messages li").count() == 1
    dialog.get_by_text(
        "The API and compatibility tests are complete.", exact=True
    ).wait_for()
    dialog.get_by_role("button", name="Since last review").click()
    dialog.get_by_text("Older transcript content is outside", exact=False).wait_for()
    page.keyboard.press("Escape")
    dialog.wait_for(state="detached")
    assert not any(
        row["endpoint"].endswith(("/visit", "/review"))
        for row in fixture_server.action_requests()
    )

    workspace_nav.get_by_role("button", name=re.compile("^Review")).click()
    page.get_by_role("button", name=re.compile("Quick Review")).click()
    page.get_by_label("Custom response").wait_for()
    page.get_by_role("button", name="Ask more", exact=True).first.click()
    composer = page.get_by_label("Message this agent")
    composer.wait_for()
    page.wait_for_function("node => document.activeElement === node", arg=composer.element_handle())
    workspace_nav.get_by_role("button", name=re.compile("^Review")).click()
    page.get_by_role("button", name=re.compile("Quick Review")).click()
    page.get_by_role("button", name="Skip", exact=True).click()
    page.get_by_role("heading", name="Review session complete", exact=True).wait_for()
    assert "1 skipped" in page.locator(".review-complete").text_content()
    assert not any(
        row["endpoint"].endswith("/review")
        for row in fixture_server.action_requests()
    )

    page.get_by_role("button", name="Start another review").click()
    page.get_by_role("heading", name="Review session complete", exact=True).wait_for(
        state="detached"
    )
    page.get_by_role("button", name=re.compile("Quick Review")).click()
    custom = page.get_by_label("Custom response")
    custom.fill("Use one-time tokens after the compatibility window.")
    send_custom = page.get_by_role("button", name="Send custom response", exact=True)
    send_custom.evaluate("node => { node.click(); node.click(); }")
    page.get_by_text("Response submitted.", exact=True).wait_for()
    page.get_by_role("button", name=re.compile("^Use a staged rollout")).click()
    page.get_by_role("heading", name="Review session complete", exact=True).wait_for()

    # The acknowledged snapshot remains the monotonic transcript jump baseline
    # even when an older transcript response races in afterward.
    workspace_nav.get_by_role("button", name="Agents", exact=True).click()
    page.locator(".agent-card").filter(has_text="Authentication refactor").get_by_role(
        "button"
    ).click()
    page.get_by_role("button", name="Deep Context", exact=True).click()
    reviewed_dialog = page.get_by_role("dialog", name=re.compile("Deep Context"))
    since_review = reviewed_dialog.get_by_role("button", name="Since last review")
    since_review.wait_for()
    assert since_review.get_attribute("aria-pressed") == "false"
    since_review.click()
    page.wait_for_timeout(350)

    writes = fixture_server.action_requests()
    settings_writes = [
        row for row in writes if row["endpoint"] == "/api/review/settings"
    ]
    assert settings_writes[:3] == [
        {
            "endpoint": "/api/review/settings",
            "body": {"interval_minutes": 15},
        },
        {
            "endpoint": "/api/review/settings",
            "body": {"interval_minutes": 30},
        },
        {
            "endpoint": "/api/review/settings",
            "body": {"urgent_pane_errors": True},
        },
    ]
    replies = [row for row in writes if row["endpoint"].endswith("/reply")]
    assert [row["endpoint"] for row in replies] == [
        "/api/decisions/decision-refresh/reply",
        "/api/decisions/decision-rollout/reply",
    ]
    assert replies[0]["body"]["option_id"] is None
    assert (
        replies[0]["body"]["custom_text"]
        == "Use one-time tokens after the compatibility window."
    )
    assert replies[0]["body"]["expected_revision"] == 3
    reviews = [row for row in writes if row["endpoint"].endswith("/review")]
    assert reviews == [
        {
            "endpoint": "/api/agents/agent-codex/review",
            "body": {"snapshot_id": "snapshot-12"},
        }
    ]
    reviewed_searches = [
        row["body"]
        for row in writes
        if row["endpoint"] == "/api/agents/agent-codex/messages"
        and row["body"].get("after") is not None
    ]
    assert reviewed_searches[-1]["after"] == pytest.approx(1_784_044_768.0)
    assert not any(row["endpoint"] == "/api/broadcast" for row in writes)


def test_plan_review_restores_metadata_only_drafts_and_reports_partial_conflict(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("agent_review_plan")
    page = page_factory(chromium_runtime, viewport=(1024, 768), touch=True)
    open_app(page, fixture_server, "/#/review")
    wait_for_connection(page)

    page.get_by_role("button", name=re.compile("Plan Review")).click()
    page.get_by_role("radio", name=re.compile("^Rotate on every use")).click()
    page.get_by_role("radio", name=re.compile("^Use a staged rollout")).click()
    stored = page.evaluate("localStorage.getItem('vmux_review_drafts_v1')")
    payload = json.loads(stored)
    assert payload["version"] == 1
    assert len(payload["drafts"]) == 2
    assert set(payload["drafts"][0]) == {
        "server_instance_id",
        "decision_id",
        "option_id",
        "revision",
        "binding_revision",
        "prompt_fingerprint",
        "options_fingerprint",
        "updated_at",
    }
    for sensitive in (
        "Choose refresh-token strategy",
        "Rotate on every use",
        "Refactor authentication safely",
        "API and compatibility",
    ):
        assert sensitive not in stored

    # Device-local choices survive a reload, then every item is re-fetched.
    page.reload(wait_until="domcontentloaded")
    page.get_by_role("heading", name="Review", exact=True).wait_for()
    page.get_by_role("button", name=re.compile("Plan Review")).click()
    page.get_by_text("2 staged choices", exact=False).wait_for()
    assert page.locator(".review-options button.selected").count() == 2
    page.get_by_role("button", name="Submit staged plan", exact=True).click()
    page.get_by_role("heading", name="Review session complete", exact=True).wait_for()
    summary = page.locator(".review-complete").text_content()
    assert "1 submitted" in summary
    assert "1 conflicted" in summary

    writes = fixture_server.action_requests()
    replies = [row for row in writes if row["endpoint"].endswith("/reply")]
    assert [row["endpoint"] for row in replies] == [
        "/api/decisions/decision-refresh/reply"
    ]
    assert page.evaluate("localStorage.getItem('vmux_review_drafts_v1')") is None
    assert not any(row["endpoint"] == "/api/broadcast" for row in writes)


def test_review_store_preserves_uncertain_draft_and_clears_server_scope(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(chromium_runtime, viewport=(1024, 768))
    open_app(page, fixture_server)
    result = page.evaluate(
        """async () => {
          const { createAgentStore } = await import("/js/agent-state.js");
          const { attentionNotificationIDs } = await import("/js/app.js");
          const agent = {
            id: "agent-uncertain",
            name: "Uncertain delivery",
            capabilities: { decision_reply: "verified_terminal" },
          };
          const decision = {
            id: "decision-uncertain",
            agent_id: agent.id,
            title: "Choose delivery",
            status: "pending",
            priority: "high",
            revision: 1,
            binding_revision: 2,
            prompt_fingerprint: "prompt-v1",
            options_fingerprint: "options-v1",
            options: [{ id: "approve", label: "Approve" }],
          };
          let deliveryUnknown = false;
          let replyPosts = 0;
          const reviewPayload = () => ({
            settings: { enabled: true, interval_minutes: 30 },
            groups: [{
              agent_id: agent.id,
              agent,
              as_of_snapshot_id: "snapshot-1",
              decisions: [{
                ...decision,
                review_status: deliveryUnknown ? "unknown" : "actionable",
              }],
            }],
            terminal_items: [],
          });
          const request = async (path, body, method) => {
            if (path === "/agents") return { agents: [agent] };
            if (path === "/decisions") return { decisions: [decision] };
            if (path === "/decisions?status=pending") return { decisions: [decision] };
            if (path === "/decisions?status=submitting") return { decisions: [] };
            if (path === "/timeline") return { events: [] };
            if (path === "/review") return reviewPayload();
            if (path === "/decisions/decision-uncertain") {
              return {
                ...decision,
                review_status: deliveryUnknown ? "unknown" : "actionable",
              };
            }
            if (path === "/decisions/decision-uncertain/reply" && method === "POST") {
              replyPosts += 1;
              deliveryUnknown = true;
              const error = new Error("delivery uncertain");
              error.status = 409;
              throw error;
            }
            throw new Error(`unexpected request: ${method || "GET"} ${path}`);
          };
          const reviewConfig = {
            experimental_agent_workspace_enabled: true,
            _info: {
              server_instance_id: "review-scope-a",
              capabilities: {
                agent_context_v1: { enabled: true },
                agent_review_v1: { enabled: true },
              },
            },
          };
          const store = createAgentStore({ request, WebSocketImpl: null });
          await store.configure(reviewConfig);
          store.stagePlanDecision(store.getSnapshot().review.groups[0].decisions[0], "approve");
          const outcomes = await store.submitPlanReview();
          const uncertain = {
            outcome: outcomes[0],
            drafts: store.getSnapshot().planDrafts.length,
            replyPosts,
          };
          await store.configure({
            experimental_agent_workspace_enabled: true,
            _info: {
              server_instance_id: "review-scope-b",
              capabilities: { agent_context_v1: { enabled: true } },
            },
          });
          const switched = {
            review: store.getSnapshot().review,
            drafts: store.getSnapshot().planDrafts.length,
            results: store.getSnapshot().planResults.length,
          };
          const batched = [...attentionNotificationIDs(
            [{ id: "needs", status: "needs_input" }, { id: "error", status: "error" }],
            {
              settings: { enabled: true, urgent_bypass: { pane_errors: false } },
              groups: [{
                decisions: [
                  { id: "high", status: "pending", priority: "high" },
                  { id: "normal", status: "pending", priority: "normal" },
                  { id: "resolved", status: "resolved", priority: "critical" },
                ],
              }],
            },
            true,
          )].sort();
          store.stop();
          return { uncertain, switched, batched };
        }"""
    )
    assert result["uncertain"]["outcome"]["status"] == "terminal_required"
    assert "may have been delivered" in result["uncertain"]["outcome"]["message"]
    assert result["uncertain"]["drafts"] == 1
    assert result["uncertain"]["replyPosts"] == 1
    assert result["switched"] == {"review": None, "drafts": 0, "results": 0}
    assert result["batched"] == ["decision:high"]


def test_queue_actions_are_deduplicated_and_terminal_output_stays_plain_text(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("slow_actions")
    page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(page, fixture_server)
    wait_for_connection(page)

    card = page.locator(".attention-card").filter(has_text="Release captain")
    assert card.locator(".attention-open").evaluate("node => node.closest('button') === node")
    assert card.locator(".star-button").evaluate("node => !node.parentElement.closest('button')")
    answer = card.get_by_role("button", name=re.compile("Ship now"))
    answer.evaluate("node => { node.click(); node.click(); }")
    card.locator(".action-success").filter(has_text="Answer sent.").wait_for(timeout=10_000)

    requests = [row for row in fixture_server.action_requests() if row["endpoint"] == "/api/select"]
    assert requests == [{"endpoint": "/api/select", "body": {"id": "%1", "key": "1"}}]

    fixture_server.scenario("slow_action_failure")
    error_card = page.locator(".attention-card").filter(has_text="API investigator")
    star = error_card.locator(".star-button")
    assert star.get_attribute("aria-label") == "Star pane"
    star.click()
    assert star.get_attribute("aria-pressed") == "true"
    error_card.locator(".action-error").wait_for(timeout=10_000)
    error_card.locator(".star-button[aria-pressed='false']").wait_for(timeout=10_000)
    assert star.get_attribute("aria-label") == "Star pane"
    assert star.get_attribute("aria-pressed") == "false"

    fixture_server.scenario("slow_actions")
    card.locator(".attention-open").click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for()
    action_card = dialog.locator(".pane-action-card")
    action_card.get_by_text("Ship the reviewed change to production?", exact=True).wait_for()
    action_card.get_by_text(
        "Repeat the focused validation before making any production change.", exact=True
    ).wait_for()
    assert action_card.get_by_text("Recommended", exact=True).count() == 1
    assert action_card.get_by_text("Current", exact=True).count() == 1
    terminal = dialog.locator("pre.terminal-output")
    assert action_card.evaluate(
        "card => Boolean(card.compareDocumentPosition(card.closest('.pane-detail').querySelector('pre.terminal-output')) & Node.DOCUMENT_POSITION_FOLLOWING)",
    )
    option = action_card.get_by_role("button", name=re.compile("Run checks again"))
    assert option.evaluate("node => node.scrollWidth <= node.clientWidth")
    option.click()
    dialog.locator(".action-success").filter(has_text="Answer sent.").wait_for(timeout=10_000)

    composer = dialog.get_by_role("textbox", name="Message for Release captain")
    action_card.get_by_role("button", name=re.compile("Tell Claude what to change")).click()
    page.wait_for_function(
        "element => document.activeElement === element",
        arg=composer.element_handle(),
        timeout=10_000,
    )
    requests = [row for row in fixture_server.action_requests() if row["endpoint"] == "/api/select"]
    assert [row["body"] for row in requests] == [
        {"id": "%1", "key": "1"},
        {"id": "%1", "key": "2"},
        {"id": "%1", "key": "3"},
    ]

    assert "<script>window.fixtureInjected = true</script>" in terminal.text_content()
    assert page.evaluate("window.fixtureInjected") is None
    assert "no-wrap" in terminal.get_attribute("class")
    dialog.get_by_role("button", name="Wrap", exact=True).click()
    assert "wrap" in terminal.get_attribute("class").split()
    dialog.get_by_role("button", name="Open full screen terminal").click()
    page.locator(".terminal-dialog").wait_for()
    page.keyboard.press("Escape")
    page.locator(".terminal-dialog").wait_for(state="detached")


def test_stats_settings_and_partial_broadcast_use_real_fixture_endpoints(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(browser_runtime, viewport=(1024, 768), touch=True)
    open_app(page, fixture_server)
    wait_for_connection(page)

    page.get_by_role("group", name="Pane filter").get_by_role(
        "button", name=re.compile("^Stats")
    ).click()
    page.get_by_role("heading", name="Stats", exact=True).wait_for()
    assert page.locator(".metric-card.primary").get_by_text("$4.80", exact=True).count() == 1
    assert page.get_by_role("progressbar", name="Claude Five-hour window remaining").get_attribute(
        "aria-valuenow"
    ) == "14"
    history_date = page.get_by_role("table").get_by_text("2026-07-14", exact=True)
    history_date.wait_for()
    assert history_date.count() == 1
    page.get_by_role("group", name="Metric").get_by_role("button", name="Tokens").click()
    page.get_by_role("group", name="Date range").get_by_role("button", name="Today").click()
    page.wait_for_function("() => document.querySelectorAll('.usage-table tbody tr').length === 1")

    page.get_by_role("button", name="Settings").click()
    settings = page.get_by_role("dialog", name=re.compile("Settings"))
    settings.get_by_role("button", name="Experimental", exact=True).click()
    workspace_switch = settings.get_by_role(
        "checkbox", name="Enable Agent Context", exact=True
    )
    assert not workspace_switch.is_checked()
    workspace_switch.click()
    page.get_by_role("navigation", name="Workspace").get_by_role(
        "button", name="Agents", exact=True
    ).wait_for()
    page.wait_for_function("node => node.checked", arg=workspace_switch.element_handle())
    assert workspace_switch.is_checked()
    workspace_switch.click()
    page.locator(".medium-shell").wait_for()
    page.wait_for_function("node => !node.checked", arg=workspace_switch.element_handle())
    assert not workspace_switch.is_checked()
    workspace_patches = [
        request["body"]["experimental_agent_workspace_enabled"]
        for request in fixture_server.action_requests()
        if request["endpoint"] == "/api/config"
        and "experimental_agent_workspace_enabled" in request["body"]
    ]
    assert workspace_patches == [True, False]

    settings.get_by_role("button", name="Usage", exact=True).click()
    for label in ["Enable usage tracking", "Quota refresh", "Report refresh", "Warning threshold"]:
        assert settings.get_by_text(label, exact=True).count() >= 1
    settings.get_by_role("button", name="Sessions", exact=True).click()
    settings.get_by_text("Browser session", exact=True).wait_for()
    assert "127.0.0.1 · connected 2m ago" in settings.text_content()
    page.keyboard.press("Escape")
    settings.wait_for(state="detached")

    fixture_server.scenario("partial_broadcast")
    page.get_by_role("button", name="Broadcast").click()
    broadcast = page.get_by_role("dialog", name=re.compile("Broadcast"))
    broadcast.get_by_role("group", name="Broadcast recipients").get_by_role(
        "button", name=re.compile("^All")
    ).click()
    assert "4 actionable" in broadcast.locator(".recipient-summary").text_content()
    assert "1 offline or unavailable excluded" in broadcast.locator(".recipient-summary").text_content()
    broadcast.get_by_role("textbox", name="Message").fill("Please save progress.")
    broadcast.get_by_role("button", name="Send to 4").click()
    broadcast.get_by_text("Broadcast partially completed", exact=True).wait_for(timeout=10_000)
    assert "Sent to 3 of 4" in broadcast.text_content()
    assert broadcast.get_by_role("button", name="Retry 1 failed").count() == 1


def test_quota_visibility_persists_and_hidden_warnings_still_count(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(chromium_runtime, viewport=(1024, 768), touch=True)
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.get_by_role("group", name="Pane filter").get_by_role(
        "button", name=re.compile("^Stats")
    ).click()
    page.get_by_role("heading", name="Stats", exact=True).wait_for()

    page.get_by_role("button", name="Settings").click()
    settings = page.get_by_role("dialog", name=re.compile("Settings"))
    settings.get_by_role("button", name="Usage", exact=True).click()
    copilot = settings.get_by_role("checkbox", name="Show Copilot quotas", exact=True)
    copilot_chat = settings.get_by_role(
        "checkbox", name="Show Copilot Chat quota", exact=True
    )
    claude_low = settings.get_by_role(
        "checkbox", name="Show Claude Five-hour window quota", exact=True
    )
    assert copilot.is_checked() and copilot_chat.is_checked() and claude_low.is_checked()
    copilot.click()
    assert copilot_chat.is_disabled()
    assert copilot_chat.is_checked()  # parent hiding preserves the child choice
    claude_low.click()
    settings.get_by_role("button", name="Save", exact=True).click()
    settings.get_by_text("Saved", exact=True).wait_for()
    page.keyboard.press("Escape")
    settings.wait_for(state="detached")

    assert page.get_by_role("heading", name="Copilot", exact=True).count() == 0
    assert page.get_by_role(
        "progressbar", name="Claude Five-hour window remaining"
    ).count() == 0
    page.get_by_role("heading", name="Antigravity", exact=True).wait_for()
    assert "includes 2 hidden meters" in page.locator(".inline-notice").filter(
        has_text="provider quota"
    ).text_content().lower()
    stats_tab = page.get_by_role("group", name="Pane filter").get_by_role(
        "button", name=re.compile("^Stats")
    )
    assert stats_tab.locator(".tab-count").text_content() == "2"

    fixture_server.scenario("usage_new_quota")
    page.get_by_role("button", name="Refresh", exact=True).click()
    page.get_by_role("heading", name="NewVendor", exact=True).wait_for(timeout=10_000)

    # A newly opened client reads the same server-wide visibility settings.
    other = page_factory(chromium_runtime, viewport=(1024, 768), touch=True)
    open_app(other, fixture_server)
    wait_for_connection(other)
    other.get_by_role("group", name="Pane filter").get_by_role(
        "button", name=re.compile("^Stats")
    ).click()
    other.get_by_role("heading", name="Stats", exact=True).wait_for()
    assert other.get_by_role("heading", name="Copilot", exact=True).count() == 0
    other.get_by_role("heading", name="NewVendor", exact=True).wait_for()

    other.get_by_role("button", name="Settings").click()
    other_settings = other.get_by_role("dialog", name=re.compile("Settings"))
    other_settings.get_by_role("button", name="Usage", exact=True).click()
    other_settings.get_by_role("button", name="Show all", exact=True).click()
    other_settings.get_by_role("button", name="Save", exact=True).click()
    other_settings.get_by_text("Saved", exact=True).wait_for()
    other.keyboard.press("Escape")
    other_settings.wait_for(state="detached")
    other.get_by_role("heading", name="Copilot", exact=True).wait_for()
    other.get_by_role("progressbar", name="Claude Five-hour window remaining").wait_for()


def test_all_hidden_quota_state_is_accessible_on_compact_layout(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(chromium_runtime, viewport=(390, 844), touch=True)
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.get_by_role("button", name="Settings").click()
    settings = page.get_by_role("dialog", name=re.compile("Settings"))
    settings.get_by_role("button", name="Usage", exact=True).click()
    for provider in ("Claude", "OpenAI", "Antigravity", "Copilot"):
        settings.get_by_role(
            "checkbox", name=f"Show {provider} quotas", exact=True
        ).click()
    settings.get_by_role("button", name="Save", exact=True).click()
    settings.get_by_text("Saved", exact=True).wait_for()
    page.keyboard.press("Escape")
    settings.wait_for(state="detached")

    page.locator(".compact-dock").get_by_role(
        "button", name=re.compile("^Stats")
    ).click()
    page.get_by_text("All provider quotas are hidden", exact=True).wait_for()
    assert "Settings → Usage" in page.get_by_text(
        "Choose displayed quotas", exact=False
    ).text_content()
    assert page.locator(".compact-dock").get_by_role(
        "button", name=re.compile("^Stats")
    ).locator("b").text_content() == "2"


def test_degraded_and_incompatible_connection_modes_are_explicit(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("rest_fallback")
    rest_page = page_factory(browser_runtime, viewport=(1024, 768))
    open_app(rest_page, fixture_server)
    wait_for_connection(rest_page, "Updating via REST")
    assert rest_page.locator(".attention-card").count() == 2

    fixture_server.scenario("incompatible")
    incompatible_page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(incompatible_page, fixture_server)
    wait_for_connection(incompatible_page, "Incompatible")
    answer = incompatible_page.get_by_role("button", name=re.compile("Ship now"))
    assert answer.is_disabled()
    incompatible_page.locator(".attention-card").first.locator(".attention-open").click()
    assert incompatible_page.get_by_role("dialog").get_by_role(
        "button", name="Add image"
    ).is_disabled()
    incompatible_page.keyboard.press("Escape")
    incompatible_page.get_by_role("button", name=re.compile("Connection: Incompatible")).click()
    details = incompatible_page.get_by_role("dialog", name=re.compile("Connection"))
    assert "Protocol9" in details.text_content()
    assert "Expected protocol1" in details.text_content()
    assert "fixture" not in details.locator(".technical-details").text_content().lower()

    fixture_server.scenario("malformed_compatibility")
    malformed_page = page_factory(browser_runtime, viewport=(390, 844))
    open_app(malformed_page, fixture_server)
    wait_for_connection(malformed_page, "Incompatible")
    assert malformed_page.get_by_role("button", name=re.compile("Ship now")).is_disabled()


def test_legacy_filter_and_url_token_are_migrated_without_losing_other_url_parts(
    browser_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(
        browser_runtime,
        viewport=(390, 844),
        prefs={"defaultFilter": "working"},
    )
    open_app(page, fixture_server, "/?keep=1&token=fixture-secret#anchor")
    wait_for_connection(page)
    assert "token=" not in page.url
    assert "keep=1" in page.url
    assert page.url.endswith("#anchor")
    saved = page.evaluate(
        "({token: localStorage.getItem('vmux_token'), prefs: JSON.parse(localStorage.getItem('vmux_prefs'))})"
    )
    assert saved["token"] == "fixture-secret"
    assert saved["prefs"]["defaultFilter"] == "active"
    assert page.locator(".compact-dock button[aria-current='page'] span").inner_text() == "Active"


def test_offline_grace_retains_snapshot_and_recovers(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(chromium_runtime, viewport=(390, 844))
    open_app(page, fixture_server)
    wait_for_connection(page)
    assert page.locator(".attention-card").count() == 2

    fixture_server.scenario("offline")
    wait_for_connection(page, "Offline")
    assert page.locator(".attention-card").count() == 2
    assert page.get_by_role("button", name=re.compile("Ship now")).is_disabled()
    page.locator(".attention-card").first.locator(".attention-open").click()
    assert page.get_by_role("dialog").get_by_role("button", name="Add image").is_disabled()
    page.keyboard.press("Escape")

    fixture_server.scenario("live")
    page.get_by_role("button", name=re.compile("Connection: Offline")).click()
    details = page.get_by_role("dialog", name=re.compile("Connection"))
    details.get_by_role("button", name="Retry", exact=True).click()
    wait_for_connection(page)
    assert page.locator(".attention-card").count() == 2


def test_unauthorized_bootstrap_shows_token_gate_and_scrubs_url(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario("unauthorized")
    page = page_factory(chromium_runtime, viewport=(390, 844))
    open_app(page, fixture_server, "/?keep=1&token=fixture-secret#gate")
    page.get_by_role("heading", name="Connect to vmux").wait_for()
    assert "token=" not in page.url
    assert "keep=1" in page.url and page.url.endswith("#gate")
    assert page.get_by_role("textbox", name="Access token").count() == 1
    assert page.get_by_role("button", name="Add image").count() == 0
    assert "fixture-secret" not in page.locator("body").inner_text()


def test_service_worker_keeps_credentials_and_pane_snapshots_out_of_offline_cache(
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    page = page_factory(
        chromium_runtime,
        viewport=(390, 844),
        service_workers="allow",
    )
    open_app(page, fixture_server, "/?keep=1&token=cache-secret")
    wait_for_connection(page)
    page.evaluate("navigator.serviceWorker.ready")
    page.wait_for_function("() => Boolean(navigator.serviceWorker.controller)")

    entries = page.evaluate(
        """async () => {
          const rows = [];
          for (const name of await caches.keys()) {
            const cache = await caches.open(name);
            for (const request of await cache.keys()) {
              rows.push({url: request.url, authorization: request.headers.has('authorization')});
            }
          }
          return rows;
        }"""
    )
    assert entries
    assert all(not row["authorization"] for row in entries)
    assert all("token=" not in row["url"] and "/api/" not in row["url"] for row in entries)
    assert all("?" not in row["url"] for row in entries)

    page.context.set_offline(True)
    page.goto(f"{fixture_server.url}/recovery?keep=1", wait_until="domcontentloaded")
    page.locator(".app-shell").wait_for(timeout=10_000)
    assert page.locator(".attention-card").count() == 0
    assert page.get_by_role("heading", name="Queue", exact=True).count() == 1
    page.context.set_offline(False)


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        ("usage_disabled", "Usage tracking is disabled"),
        ("usage_not_installed", "tokscale is not installed"),
        ("usage_timeout", "Usage collection timed out"),
        ("usage_error", "Usage is unavailable"),
        ("usage_stale", "Showing the last successful snapshot"),
        ("usage_empty", "No usage data yet"),
    ],
)
def test_usage_availability_states_are_actionable(
    scenario: str,
    message: str,
    chromium_runtime: BrowserRuntime,
    fixture_server: FixtureServer,
    page_factory,
) -> None:
    fixture_server.scenario(scenario)
    page = page_factory(chromium_runtime, viewport=(390, 844))
    open_app(page, fixture_server)
    wait_for_connection(page)
    page.locator(".compact-dock").get_by_role("button", name=re.compile("^Stats")).click()
    page.get_by_text(message, exact=False).first.wait_for(timeout=10_000)
