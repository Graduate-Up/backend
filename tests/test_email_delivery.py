import httpx
import pytest

from app.services import email_delivery


@pytest.fixture
def brevo_transport(monkeypatch):
    monkeypatch.setenv("JOLUP_EMAIL_TRANSPORT", "brevo")
    monkeypatch.setenv("JOLUP_BREVO_API_KEY", "test-key")
    monkeypatch.setenv("JOLUP_BREVO_FROM", "sender@example.com")


def test_brevo_sends_code_over_https(monkeypatch, brevo_transport):
    def post(url, **kwargs):
        assert url == "https://api.brevo.com/v3/smtp/email"
        assert kwargs["headers"]["api-key"] == "test-key"
        assert kwargs["follow_redirects"] is False
        assert kwargs["json"]["sender"]["email"] == "sender@example.com"
        assert kwargs["json"]["to"] == [{"email": "student@knu.ac.kr"}]
        assert "001234" in kwargs["json"]["textContent"]
        return httpx.Response(201)

    monkeypatch.setattr(email_delivery.httpx, "post", post)
    email_delivery.email_sender()("student@knu.ac.kr", "001234")


@pytest.mark.parametrize("status", [301, 401, 429, 500])
def test_provider_errors_do_not_leak_response_body(monkeypatch, brevo_transport, status):
    monkeypatch.setattr(
        email_delivery.httpx, "post", lambda *_args, **_kwargs: httpx.Response(status, text="private provider data"),
    )
    with pytest.raises(email_delivery.EmailUnavailable) as error:
        email_delivery.email_sender()("student@knu.ac.kr", "001234")
    assert "private provider data" not in str(error.value)


def test_missing_brevo_key_and_default_smtp(monkeypatch, brevo_transport):
    monkeypatch.delenv("JOLUP_BREVO_API_KEY")
    with pytest.raises(email_delivery.EmailUnavailable):
        email_delivery.email_sender()
    monkeypatch.delenv("JOLUP_EMAIL_TRANSPORT")
    sentinel = object()
    monkeypatch.setattr(email_delivery, "smtp_sender", lambda: sentinel)
    assert email_delivery.email_sender() is sentinel
