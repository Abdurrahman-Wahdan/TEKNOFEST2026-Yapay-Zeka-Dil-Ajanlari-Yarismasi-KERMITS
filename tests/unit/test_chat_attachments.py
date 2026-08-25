import base64
import io

import pytest

from api import chat_attachments
from api.chat_attachments import AttachmentError, prepare_attachment, resolve_attachments


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def test_markdown_is_resolved_as_utf8_text_for_its_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "_ROOT", tmp_path)
    prepared = prepare_attachment(io.BytesIO("# Başlık\nKAR-17.25".encode()), "oran.md", "u1")

    resolved = resolve_attachments([prepared["id"]], "u1")

    assert prepared["kind"] == "text"
    assert prepared["mediaType"] == "text/markdown"
    assert resolved[0].text == "# Başlık\nKAR-17.25"
    assert resolved[0].images == ()


def test_image_bytes_are_model_ready_but_never_returned_to_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "_ROOT", tmp_path)
    prepared = prepare_attachment(io.BytesIO(PNG_1X1), "pixel.png", "u1")

    resolved = resolve_attachments([prepared["id"]], "u1")

    assert "data" not in prepared
    assert resolved[0].images[0].media_type == "image/png"
    assert base64.b64decode(resolved[0].images[0].data) == PNG_1X1


def test_attachment_id_cannot_be_resolved_by_another_user(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "_ROOT", tmp_path)
    prepared = prepare_attachment(io.BytesIO(b"private"), "note.txt", "owner")

    with pytest.raises(AttachmentError, match="expired"):
        resolve_attachments([prepared["id"]], "someone-else")


def test_binary_disguised_as_text_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_attachments, "_ROOT", tmp_path)

    with pytest.raises(AttachmentError, match="binary"):
        prepare_attachment(io.BytesIO(b"hello\x00world"), "note.txt", "u1")
