from unittest.mock import MagicMock
import pytest
import email_report


def test_email_attaches_report_and_encrypts_before_login(monkeypatch):
    smtp = MagicMock()
    monkeypatch.setattr(email_report.smtplib, "SMTP", smtp)
    settings = dict(SMTP_HOST="smtp.gmail.com", SMTP_USER="sender@example.com",
                    SMTP_PASSWORD="test-only", SMTP_FROM="sender@example.com")
    email_report.send_report("client@example.com", [("report.pdf", b"pdf", "application/pdf")], settings)
    server = smtp.return_value.__enter__.return_value
    assert [c[0] for c in server.mock_calls] == ["starttls", "login", "send_message"]
    message = server.send_message.call_args.args[0]
    assert message["To"] == "client@example.com"
    assert list(message.iter_attachments())[0].get_payload(decode=True) == b"pdf"


@pytest.mark.parametrize("recipient", ["", "invalid", "a@example.com\nbcc:b@example.com", "a@example.com,b@example.com"])
def test_invalid_recipient_never_connects(monkeypatch, recipient):
    smtp = MagicMock()
    monkeypatch.setattr(email_report.smtplib, "SMTP", smtp)
    with pytest.raises(ValueError):
        email_report.send_report(recipient, [("report.pdf", b"pdf", "application/pdf")], {})
    smtp.assert_not_called()
