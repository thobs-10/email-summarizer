from email.message import EmailMessage

from email_summarizer.tools.email_ingestor import EmailIngestor


def _make_ingestor(monkeypatch):
    monkeypatch.setenv("USER_ID", "user-1")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("USER_NAME", "Test User")
    monkeypatch.setenv("EMAIL_PASSWORD", "app-password")
    return EmailIngestor()


def test_map_raw_messages_prefers_plain_text(monkeypatch):
    ingestor = _make_ingestor(monkeypatch)

    msg = EmailMessage()
    msg["Subject"] = "Weekly Update"
    msg["From"] = "newsletter@example.com"
    msg["Date"] = "Thu, 11 Jul 2024 09:00:00 +0000"
    msg.set_content("Plain body content")
    msg.add_alternative("<p>HTML body content</p>", subtype="html")

    models = ingestor.map_raw_messages_to_email_model([msg.as_bytes()])

    assert len(models) == 1
    assert models[0].subject == "Weekly Update"
    assert models[0].sender == "newsletter@example.com"
    assert models[0].date == "Thu, 11 Jul 2024 09:00:00 +0000"
    assert models[0].body == "Plain body content"


def test_map_raw_messages_falls_back_to_html_only(monkeypatch):
    ingestor = _make_ingestor(monkeypatch)

    msg = EmailMessage()
    msg["Subject"] = "HTML Newsletter"
    msg["From"] = "news@example.com"
    msg.set_content("<h1>Hello HTML</h1>", subtype="html")

    models = ingestor.map_raw_messages_to_email_model([msg.as_bytes()])

    assert len(models) == 1
    assert models[0].subject == "HTML Newsletter"
    assert models[0].sender == "news@example.com"
    assert "Hello HTML" in models[0].body


def test_map_raw_messages_handles_charset_variant(monkeypatch):
    ingestor = _make_ingestor(monkeypatch)

    body_text = "Cafe creme and pinata"
    msg = EmailMessage()
    msg["Subject"] = "Charset Test"
    msg["From"] = "charset@example.com"
    msg.set_content(body_text, charset="iso-8859-1")

    models = ingestor.map_raw_messages_to_email_model([msg.as_bytes()])

    assert len(models) == 1
    assert models[0].body == body_text
