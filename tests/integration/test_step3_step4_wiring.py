"""
End-to-end tests, via Streamlit's AppTest, for VOICOVA_Product_2.0_
Consolidated.docx's Steps 3, 4, and 5:

- Step 3 (Section 9.1 / Section 11): the render-context field defaults
  to the last-used context rather than forcing a fresh choice every
  render.
- Step 4 (Section 9.4), write half: write_render_history is called
  from _run_render's success path, with the actual render text/
  context/mode/scores from that render.
- Step 5 (Section 9.4): the History screen (screen 6) lists what
  Step 4 wrote and renders a before/after reopen view.

Same mocking approach as test_app_render_pipeline.py: only the
Anthropic API call and the Supabase client (via render_history.
get_supabase_client) are mocked - zero cost, no real Supabase writes
or reads from tests.
"""
import html
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness


_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

FAKE_LLM_OUTPUT = (
    "I see it as the clearest way forward—we should leverage this "
    "approach across the team."
)

BASELINE_SAMPLE_1 = (
    "I think we should move fast on this. I want the team to focus on "
    "the core problem first, and then we can look at the edges."
)
BASELINE_SAMPLE_2 = (
    "I believe the data backs this up, and I think it is the right call "
    "for now. We need to move quickly and stay focused."
)


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _seed_screen4(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    metrics_1 = compute_baseline_metrics(BASELINE_SAMPLE_1)
    metrics_2 = compute_baseline_metrics(BASELINE_SAMPLE_2)

    at.session_state["screen"] = 4
    at.session_state["raw_text"] = BASELINE_SAMPLE_1
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["fingerprint_samples"] = [metrics_1, metrics_2]
    at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
    at.session_state["sample2_completions"] = ["", "", "", ""]
    at.session_state["_device_id"] = "test-device-1"
    # Real onboarding sets this at the go_to(4) call site (22 Aug 2026
    # UX audit fix, sidebar visibility) — this helper jumps straight to
    # screen 4 in session_state, bypassing that code path, so it needs
    # setting explicitly to keep simulating a completed-onboarding
    # arrival at screen 4 rather than a mid-onboarding state.
    at.session_state["_sidebar_unlocked"] = True


# ---------------------------------------------------------------------------
# Step 3 - context field defaults to last-used
# ---------------------------------------------------------------------------

def test_context_field_empty_on_first_ever_visit():
    # Nothing used before - must not seed anything that isn't there.
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()
    _seed_screen4(at)
    at.run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == ""


def test_context_field_prefills_with_last_used_context():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()
    _seed_screen4(at)
    # Simulate a previous render in this session having used a context.
    at.session_state["render_context_input"] = "LinkedIn post for a client"
    at.run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == "LinkedIn post for a client"


def test_context_field_remains_editable_after_prefill():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()
    _seed_screen4(at)
    at.session_state["render_context_input"] = "LinkedIn post for a client"
    at.run()
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    context_input.set_value("A different context entirely").run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == "A different context entirely"


# ---------------------------------------------------------------------------
# Step 4 - write_render_history called from _run_render's success path
#
# Patched at render_history.get_supabase_client rather than
# app.write_render_history: AppTest executes app.py through its own
# script-runner mechanism rather than a normal `import app`, so a
# patch on the app module's bound name is never seen by that
# execution path. render_history is imported by the normal Python
# import system as a dependency, so patching its own
# get_supabase_client (the same seam test_render_history.py's unit
# tests already patch) reaches the real call reliably.
# ---------------------------------------------------------------------------

def _mock_supabase_client():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    table.insert.return_value.execute.return_value = MagicMock()
    select_result = MagicMock()
    select_result.data = []
    table.select.return_value.eq.return_value.order.return_value.execute.return_value = select_result
    return client, table


def test_write_render_history_called_on_successful_render():
    client, table = _mock_supabase_client()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch("render_history.get_supabase_client", return_value=client):
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
            at.run()
            _seed_screen4(at)
            at.session_state["render_context_input"] = "A quick client email"
            at.run()
            assert not at.exception

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception, f"App raised during render: {at.exception}"

            table.insert.assert_called_once()
            payload = table.insert.call_args[0][0]
            assert payload["device_id"] == "test-device-1"
            assert payload["context"] == "A quick client email"
            assert payload["mode"] == "preserve"
            assert payload["input_text"] == "Please write a short note about the launch plan."
            assert payload["output_text"] == at.session_state["render_output"]
            # voice_match_tier (human-readable, e.g. "Strong match"),
            # not voice_match_badge (a CSS class name like
            # "badge-green") - caught and fixed 19 Aug 2026, this
            # assertion is what should have caught it the first time.
            assert payload["voice_match"] == at.session_state["voice_report"]["voice_match_tier"]
            assert not payload["voice_match"].startswith("badge-")
            assert isinstance(payload["content_lock_pass"], bool)


def test_write_render_history_content_lock_pass_matches_hard_fail_state():
    # content_lock_pass should be the inverse of content_integrity_hard_fail
    # for the same render, not an independently recomputed value.
    client, table = _mock_supabase_client()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch("render_history.get_supabase_client", return_value=client):
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
            at.run()
            _seed_screen4(at)
            at.run()

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception

            hard_fail = at.session_state["voice_report"]["content_integrity_hard_fail"]
            payload = table.insert.call_args[0][0]
            assert payload["content_lock_pass"] == (not hard_fail)


def test_write_render_history_not_called_when_render_fails():
    # Daily cap reached -> _run_render returns False before generating
    # anything - History must not get a phantom entry for a render
    # that never happened.
    client, table = _mock_supabase_client()
    with patch("render_cap.check_and_reserve_render", return_value=(False, 40, 40)), \
         patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        table.insert.assert_not_called()


# ---------------------------------------------------------------------------
# Step 5 - History screen (screen 6): list view + before/after reopen
# ---------------------------------------------------------------------------

def _mock_supabase_client_with_history(rows):
    """Same shape as _mock_supabase_client, but the select().eq().
    order().limit().execute() chain get_render_history actually calls
    returns the given rows, instead of an empty list."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    select_result = MagicMock()
    select_result.data = rows
    table.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = select_result
    return client, table


def test_history_screen_shows_empty_state_with_no_renders():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception
        # render_alert() draws info/error/success via st.markdown on
        # the app's own palette now, not native st.info/error/success
        # (see app.py's .callout / render_alert), so this checks the
        # rendered callout markup rather than the native element type.
        assert any(
            "No renders yet" in m.value for m in at.markdown
            if 'class="callout callout-info"' in (m.value or "")
        )


def test_history_screen_lists_past_renders():
    rows = [
        {
            "id": "row-1", "created_at": "2026-08-19T10:15:00Z",
            "context": "LinkedIn post", "mode": "preserve",
            "input_text": "Original draft one.", "output_text": "Rewritten draft one.",
            "voice_match": "Strong match", "content_lock_pass": True,
        },
        {
            "id": "row-2", "created_at": "2026-08-18T09:00:00Z",
            "context": "", "mode": "elevate",
            "input_text": "Original draft two.", "output_text": "Rewritten draft two.",
            "voice_match": "Close match", "content_lock_pass": False,
        },
    ]
    client, _ = _mock_supabase_client_with_history(rows)
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception

        expander_labels = [e.label for e in at.expander]
        assert any("LinkedIn post" in label and "Strong match" in label for label in expander_labels)
        assert any("No context set" in label and "Close match" in label for label in expander_labels)


def test_history_screen_reopen_shows_before_and_after_text():
    rows = [{
        "id": "row-1", "created_at": "2026-08-19T10:15:00Z",
        "context": "LinkedIn post", "mode": "preserve",
        "input_text": "Original draft one.", "output_text": "Rewritten draft one.",
        "voice_match": "Strong match", "content_lock_pass": True,
    }]
    client, _ = _mock_supabase_client_with_history(rows)
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception

        before_box = next(t for t in at.text_area if t.key == "history_before_row-1")
        after_box = next(t for t in at.text_area if t.key == "history_after_row-1")
        assert before_box.value == "Original draft one."
        assert after_box.value == "Rewritten draft one."
        assert before_box.disabled and after_box.disabled


def test_history_nav_buttons_present_from_write_and_my_voice():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()
        assert not at.exception
        assert any(b.key == "nav_to_history_from_write" for b in at.sidebar.button)

        at.session_state["screen"] = 5
        at.run()
        assert not at.exception
        assert any(b.key == "nav_to_history_from_my_voice" for b in at.sidebar.button)


def test_history_back_to_write_button_navigates():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)  # sets baseline_fingerprint, needed for the sidebar nav to show
        at.session_state["screen"] = 6
        at.run()
        assert not at.exception

        back_button = next(b for b in at.sidebar.button if b.key == "nav_back_to_write_from_history")
        back_button.click().run()
        assert at.session_state["screen"] == 4


# ---------------------------------------------------------------------------
# Step 4 (subscription half) - paywall UI and checkout-success banner
# ---------------------------------------------------------------------------

def test_paywall_shows_upgrade_buttons_not_try_again():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("lifetime_cap.check_and_reserve_lifetime_render", return_value=(False, 15, 15)), \
         patch("render_history.get_supabase_client", return_value=MagicMock()):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()
        assert not at.exception

        assert any(
            "used all 15 free renders" in m.value for m in at.markdown
            if 'class="callout callout-error"' in (m.value or "")
        )
        button_keys = [b.key for b in at.button]
        assert "upgrade_monthly" in button_keys
        assert "upgrade_annual" in button_keys
        assert "retry_render" not in button_keys


def test_non_paywall_error_still_shows_try_again_not_upgrade():
    # Ordinary daily-cap failure must keep the existing Try again path -
    # the paywall branch must not swallow every render_error.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("render_cap.check_and_reserve_render", return_value=(False, 40, 40)):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()
        assert not at.exception

        button_keys = [b.key for b in at.button]
        assert "retry_render" in button_keys
        assert "upgrade_monthly" not in button_keys


def test_upgrade_monthly_button_calls_checkout_with_correct_plan_and_shows_link():
    # Patches the same low-level seam stripe_subscription's own unit
    # tests patch (stripe.checkout.Session.create), not app.
    # create_subscription_checkout - AppTest runs app.py through its
    # own script-runner rather than a normal `import app`, so a patch
    # on app's bound name (imported via `from stripe_subscription
    # import ...`) is never seen by that execution path, same issue
    # documented above for write_render_history. This also exercises
    # the real create_subscription_checkout end to end, which is a
    # stronger integration test than mocking it away entirely.
    import stripe
    fake_session = MagicMock(url="https://checkout.stripe.com/pay/sess_1")
    with patch.dict(os.environ, {
             "ANTHROPIC_API_KEY": "test-key-not-real",
             "STRIPE_API_KEY": "sk_test_fake", "VOICOVA_APP_URL": "https://app.example.com",
         }), \
         patch("lifetime_cap.check_and_reserve_lifetime_render", return_value=(False, 15, 15)), \
         patch.object(stripe.checkout.Session, "create", return_value=fake_session) as mock_create:
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()
        assert not at.exception

        upgrade_button = next(b for b in at.button if b.key == "upgrade_monthly")
        upgrade_button.click().run()
        assert not at.exception

        kwargs = mock_create.call_args.kwargs
        assert kwargs["metadata"]["device_id"] == "test-device-1"
        assert kwargs["metadata"]["plan"] == "monthly"
        # No dedicated .link_button accessor on this Streamlit version's
        # AppTest - .get("link_button") is the documented generic
        # fallback for element types without one.
        link_urls = [lb.url for lb in at.get("link_button")]
        assert "https://checkout.stripe.com/pay/sess_1" in link_urls


def test_upgrade_annual_button_calls_checkout_with_correct_plan():
    import stripe
    fake_session = MagicMock(url="https://checkout.stripe.com/pay/sess_2")
    with patch.dict(os.environ, {
             "ANTHROPIC_API_KEY": "test-key-not-real",
             "STRIPE_API_KEY": "sk_test_fake", "VOICOVA_APP_URL": "https://app.example.com",
         }), \
         patch("lifetime_cap.check_and_reserve_lifetime_render", return_value=(False, 15, 15)), \
         patch.object(stripe.checkout.Session, "create", return_value=fake_session) as mock_create:
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        upgrade_button = next(b for b in at.button if b.key == "upgrade_annual")
        upgrade_button.click().run()
        assert not at.exception

        kwargs = mock_create.call_args.kwargs
        assert kwargs["metadata"]["plan"] == "annual"


def test_upgrade_button_shows_error_when_checkout_fails():
    # No STRIPE_API_KEY configured -> create_subscription_checkout
    # returns None -> the button's own failure branch fires.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("stripe_subscription._get_secret", return_value=None), \
         patch("lifetime_cap.check_and_reserve_lifetime_render", return_value=(False, 15, 15)):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        upgrade_button = next(b for b in at.button if b.key == "upgrade_monthly")
        upgrade_button.click().run()
        assert not at.exception
        assert any(
            "Couldn't start checkout" in html.unescape(m.value) for m in at.markdown
            if 'class="callout callout-error"' in (m.value or "")
        )


def test_checkout_success_query_param_lands_on_confirmation_screen():
    import stripe
    session = stripe.checkout.Session.construct_from(
        {
            "id": "sess_1", "object": "checkout.session", "status": "complete",
            "customer": "cus_1", "metadata": {"device_id": "test-device-1"},
        },
        "sk_test_fake",
    )
    subscription = stripe.Subscription.construct_from(
        {"id": "sub_1", "object": "subscription", "status": "active"}, "sk_test_fake",
    )
    session["subscription"] = subscription
    client = MagicMock()
    with patch.dict(os.environ, {"STRIPE_API_KEY": "sk_test_fake"}), \
         patch.object(stripe.checkout.Session, "retrieve", return_value=session), \
         patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("persistence.get_or_create_device_id", return_value="test-device-1"):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        # In production this fires from wherever the paywall triggered
        # checkout (typically screen 4) - seeded here the same way,
        # not as a workaround. The handler itself now redirects to the
        # dedicated confirmation screen (9) on success, rather than
        # dropping a banner on top of whatever screen was current.
        at.session_state["screen"] = 4
        at.session_state["baseline_fingerprint"] = {}
        at.query_params["payment"] = "success"
        at.query_params["session_id"] = "sess_1"
        at.run()
        assert not at.exception
        assert at.session_state["screen"] == 9
        assert any(
            "You're subscribed" in html.unescape(m.value) for m in at.markdown
            if 'class="headline"' in (m.value or "")
        )


def test_restore_success_query_param_lands_on_confirmation_screen():
    import stripe
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    active_subscription_result = MagicMock()
    active_subscription_result.data = [
        stripe.Subscription.construct_from(
            {"id": "sub_1", "object": "subscription", "status": "active"}, "sk_test_fake",
        )
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", return_value=active_subscription_result), \
         patch("persistence.get_or_create_device_id", return_value="test-device-1"):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        at.query_params["restore"] = "tok_1"
        at.run()
        assert not at.exception
        assert at.session_state["screen"] == 9
        assert any(
            "You're subscribed" in html.unescape(m.value) for m in at.markdown
            if 'class="headline"' in (m.value or "")
        )


def test_checkout_success_query_param_shows_failure_banner_when_verify_fails():
    import stripe
    with patch.dict(os.environ, {"STRIPE_API_KEY": "sk_test_fake"}), \
         patch.object(stripe.checkout.Session, "retrieve", side_effect=Exception("boom")), \
         patch("persistence.get_or_create_device_id", return_value="test-device-1"):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        at.session_state["screen"] = 4
        at.session_state["baseline_fingerprint"] = {}
        at.query_params["payment"] = "success"
        at.query_params["session_id"] = "sess_1"
        at.run()
        assert not at.exception
        assert any(
            "couldn't confirm that payment" in html.unescape(m.value) for m in at.markdown
            if 'class="callout callout-error"' in (m.value or "")
        )


def test_checkout_cancelled_query_param_shows_no_banner():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()
    at.session_state["screen"] = 4
    at.session_state["baseline_fingerprint"] = {}
    at.query_params["payment"] = "cancelled"
    at.run()
    assert not at.exception
    assert not any("subscribed" in s.value for s in at.success)
    assert not any("confirm that payment" in e.value for e in at.error)


def test_active_subscriber_sees_manage_subscription_not_upgrade():
    """Shell sidebar shows the frictionless cancel path (Stripe
    Customer Portal) for an active subscriber, not the free-tier
    upgrade chip."""
    with patch("lifetime_cap.device_has_active_subscription", return_value=True):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1
        at.run()
        _seed_screen4(at)
        at.run()
        assert not at.exception
        assert any(
            b.key == "shell_manage_subscription_from_4" for b in at.button
        )
        assert not any(
            b.key == "shell_upgrade_from_4" for b in at.button
        )


def test_manage_subscription_button_redirects_to_billing_portal():
    portal_url = "https://billing.stripe.com/session/test_1"
    with patch("lifetime_cap.device_has_active_subscription", return_value=True), \
         patch("stripe_subscription.create_billing_portal_session", return_value=portal_url) as mock_portal:
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1
        at.run()
        _seed_screen4(at)
        at.run()
        manage_btn = next(b for b in at.button if b.key == "shell_manage_subscription_from_4")
        manage_btn.click().run()
        assert not at.exception
        mock_portal.assert_called_once_with("test-device-1")
        assert any(
            "Redirecting to your subscription settings" in html.unescape(m.value)
            for m in at.markdown
        )


def test_manage_subscription_shows_error_when_portal_unavailable():
    with patch("lifetime_cap.device_has_active_subscription", return_value=True), \
         patch("stripe_subscription.create_billing_portal_session", return_value=None):
        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1
        at.run()
        _seed_screen4(at)
        at.run()
        manage_btn = next(b for b in at.button if b.key == "shell_manage_subscription_from_4")
        manage_btn.click().run()
        assert not at.exception
        assert any(
            "Couldn't open subscription settings" in html.unescape(m.value) for m in at.markdown
            if 'class="callout callout-error"' in (m.value or "")
        )


# ---------------------------------------------------------------------------
# Social-format character-length nudge (29 Aug 2026) — a soft, informational
# indicator only shown for platform_format="social", against LinkedIn's
# researched 1,200-1,600 engagement sweet spot and 3,000 hard cap.
# ---------------------------------------------------------------------------

def _seed_output(at: AppTest, output_text: str, platform_format: str | None = "social"):
    at.session_state["screen"] = 4
    at.session_state["baseline_fingerprint"] = {}
    at.session_state["render_output"] = output_text
    at.session_state["voice_report"] = None
    at.session_state["render_id"] = None
    at.session_state["_device_id"] = "test-device-1"
    at.session_state["platform_format_input"] = platform_format


def test_length_nudge_shows_sweet_spot_for_social_format_in_range():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    _seed_output(at, "x" * 1400, platform_format="social")
    at.run()
    assert not at.exception
    assert any(
        "sweet spot for engagement" in html.unescape(m.value) for m in at.markdown
        if "1,400 characters" in html.unescape(m.value)
    )


def test_length_nudge_warns_over_sweet_spot_but_under_cap():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    _seed_output(at, "x" * 2000, platform_format="social")
    at.run()
    assert not at.exception
    assert any(
        "badge-amber" in html.unescape(m.value) and "longer than" in html.unescape(m.value)
        for m in at.markdown
    )


def test_length_nudge_warns_over_hard_cap():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    _seed_output(at, "x" * 3200, platform_format="social")
    at.run()
    assert not at.exception
    assert any(
        "badge-red" in html.unescape(m.value) and "cut off" in html.unescape(m.value)
        for m in at.markdown
    )


def test_length_nudge_hidden_for_non_social_format():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    _seed_output(at, "x" * 2000, platform_format="email")
    at.run()
    assert not at.exception
    assert not any("characters</span>" in html.unescape(m.value) for m in at.markdown)


def test_length_nudge_hidden_when_no_platform_format():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    _seed_output(at, "x" * 2000, platform_format=None)
    at.run()
    assert not at.exception
    assert not any("characters</span>" in html.unescape(m.value) for m in at.markdown)


# ---------------------------------------------------------------------------
# "Learn from my edit" (29 Aug 2026) — an edited render output becomes a new
# fingerprint sample, via the shared _add_writing_sample_to_fingerprint
# helper also used by the deepen-fingerprint panel. Only the user's own
# edited text is ever used, never the raw AI output.
# ---------------------------------------------------------------------------

def _seed_rendered_output(at: AppTest, output_text: str):
    import hashlib
    _seed_screen4(at)
    at.session_state["render_output"] = output_text
    at.session_state["voice_report"] = None
    at.session_state["render_id"] = None
    output_key = "out_" + hashlib.md5(output_text[:50].encode()).hexdigest()[:8]
    return output_key


def test_learn_from_edit_button_hidden_when_output_unedited():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    output_text = "This is the original rendered text, untouched."
    _seed_rendered_output(at, output_text)
    at.run()
    assert not at.exception
    assert not any(b.key and b.key.startswith("learn_from_edit_") for b in at.button)


def test_learn_from_edit_button_shown_and_adds_sample_when_edited():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    output_text = "This is the original rendered text, untouched."
    output_key = _seed_rendered_output(at, output_text)
    at.run()
    assert not at.exception

    edited_text = (
        "This is my own edited version of the rendered text, changed "
        "enough that it reads differently from the original output."
    )
    at.text_area(key=output_key).set_value(edited_text)
    at.run()

    learn_button = next(
        (b for b in at.button if b.key == f"learn_from_edit_{output_key}"), None
    )
    assert learn_button is not None, "Expected the Learn from my edit button once edited"

    before_docs = at.session_state["cumulative_docs"]
    before_samples = len(at.session_state["fingerprint_sample_texts"])

    learn_button.click()
    at.run()
    assert not at.exception
    assert at.session_state["cumulative_docs"] == before_docs + 1
    assert len(at.session_state["fingerprint_sample_texts"]) == before_samples + 1
    assert at.session_state["fingerprint_sample_texts"][-1] == edited_text
    assert any(
        "strengthen your voice" in html.unescape(m.value) for m in at.markdown
        if 'class="callout callout-success"' in (m.value or "")
    )


# ---------------------------------------------------------------------------
# Per-register compounding baseline (30 Aug 2026) — Learn-from-edit, driven
# through the real UI, must feed the per-format baseline when the edited
# render targeted a specific platform_format, and must NOT create one when
# it didn't (regression check on the additive design).
# ---------------------------------------------------------------------------

def test_learn_from_edit_populates_per_format_baseline_when_platform_format_set():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    output_text = "This is the original rendered text, untouched."
    output_key = _seed_rendered_output(at, output_text)
    at.session_state["platform_format_input"] = "email"
    at.run()

    edited_text = (
        "This is my own edited version of the rendered text, changed "
        "enough that it reads differently from the original output."
    )
    at.text_area(key=output_key).set_value(edited_text)
    at.run()

    learn_button = next(
        (b for b in at.button if b.key == f"learn_from_edit_{output_key}"), None
    )
    assert learn_button is not None
    learn_button.click()
    at.run()
    assert not at.exception

    assert "baseline_fingerprints_by_format" in at.session_state
    by_format = at.session_state["baseline_fingerprints_by_format"]
    assert "email" in by_format
    assert by_format["email"]["word_count"] == len(edited_text.split())
    # The existing blended baseline must still reflect the sample too —
    # this feature is additive, not a replacement.
    assert at.session_state["baseline_fingerprint"]["word_count"] >= len(edited_text.split())


def test_learn_from_edit_creates_no_per_format_baseline_when_platform_format_unset():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    output_text = "This is the original rendered text, untouched."
    output_key = _seed_rendered_output(at, output_text)
    at.session_state["platform_format_input"] = None
    at.run()

    edited_text = (
        "This is my own edited version of the rendered text, changed "
        "enough that it reads differently from the original output."
    )
    at.text_area(key=output_key).set_value(edited_text)
    at.run()

    learn_button = next(
        (b for b in at.button if b.key == f"learn_from_edit_{output_key}"), None
    )
    assert learn_button is not None
    learn_button.click()
    at.run()
    assert not at.exception
    assert "baseline_fingerprints_by_format" not in at.session_state


# ---------------------------------------------------------------------------
# Structured correction evidence (30 Aug 2026, voice-review item #1) —
# Learn-from-edit, driven through the real UI, must capture the specific
# predicted-vs-corrected delta, not just add a blended sample.
# ---------------------------------------------------------------------------

def test_learn_from_edit_captures_structured_correction_evidence():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    # Heavily hedged prediction, same shape as the worked example in the
    # original review document.
    output_text = (
        "This could potentially suggest that the approach might work, "
        "though it may need more testing perhaps."
    )
    output_key = _seed_rendered_output(at, output_text)
    at.session_state["platform_format_input"] = "email"
    at.run()

    edited_text = "This suggests the approach works. It needs more testing."
    at.text_area(key=output_key).set_value(edited_text)
    at.run()

    learn_button = next(
        (b for b in at.button if b.key == f"learn_from_edit_{output_key}"), None
    )
    assert learn_button is not None
    learn_button.click()
    at.run()
    assert not at.exception

    assert "correction_evidence" in at.session_state
    history = at.session_state["correction_evidence"]
    assert len(history) == 1
    entry = history[0]
    assert entry["platform_format"] == "email"
    assert "hedge_density" in entry["evidence"]
    assert entry["evidence"]["hedge_density"]["direction"] == "decreased"


def test_learn_from_edit_trivial_change_captures_no_correction_evidence():
    """An edit too small to clear DELTA_BAND_MIN_ABS_DIFF on any
    dimension must not create a correction_evidence entry at all —
    regression check on the floor logic reaching the live UI."""
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    output_text = "I think this is good."
    output_key = _seed_rendered_output(at, output_text)
    at.run()

    edited_text = "I think this is great."
    at.text_area(key=output_key).set_value(edited_text)
    at.run()

    learn_button = next(
        (b for b in at.button if b.key == f"learn_from_edit_{output_key}"), None
    )
    assert learn_button is not None
    learn_button.click()
    at.run()
    assert not at.exception
    assert "correction_evidence" not in at.session_state
