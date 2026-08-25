"""Temporary, user-bound preprocessing for chat file attachments.

The browser uploads a file once and receives an opaque id.  The chat request
then carries only that id: PDF/DOCX page images never make a wasteful round trip
through JavaScript, and no attachment bytes are persisted in chat history.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from config.settings import settings

from .schemas.chat import CapturePayload


class AttachmentError(ValueError):
    """A safe, user-facing attachment refusal."""


@dataclass(frozen=True)
class ResolvedAttachment:
    id: str
    filename: str
    kind: Literal["image", "text", "document"]
    media_type: str
    size: int
    text: str | None
    images: tuple[CapturePayload, ...]


_ROOT = Path(tempfile.gettempdir()) / "tf26-chat-attachments"
_ID = re.compile(r"^[A-Za-z0-9_-]{20,80}$")
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
_TEXT_EXTENSIONS = {".txt", ".md", ".markdown"}
_DOCUMENT_EXTENSIONS = {".pdf", ".docx"}


def _safe_filename(value: str | None) -> str:
    name = Path((value or "attachment").replace("\x00", "")).name.strip()
    return (name or "attachment")[:240]


def _read_bounded(stream, limit: int) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise AttachmentError(
            f"The attachment is larger than {settings.CHAT_ATTACHMENT_MAX_UPLOAD_MB} MB."
        )
    return data


def _image_media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pdf_pages(path: Path) -> int:
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        raise AttachmentError("PDF support is unavailable because Poppler is not installed.")
    try:
        result = subprocess.run(
            [pdfinfo, str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.CHAT_ATTACHMENT_PROCESS_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise AttachmentError("The PDF could not be opened.") from exc
    match = re.search(r"^Pages:\s+(\d+)\s*$", result.stdout, re.MULTILINE)
    if not match:
        raise AttachmentError("The PDF page count could not be read.")
    pages = int(match.group(1))
    if pages < 1:
        raise AttachmentError("The PDF has no pages.")
    if pages > settings.CHAT_ATTACHMENT_MAX_PAGES:
        raise AttachmentError(
            f"The document has {pages} pages; the attachment limit is "
            f"{settings.CHAT_ATTACHMENT_MAX_PAGES} pages."
        )
    return pages


def _render_pdf(path: Path, output: Path) -> list[Path]:
    pages = _pdf_pages(path)
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise AttachmentError("PDF support is unavailable because Poppler is not installed.")
    prefix = output / "page"
    try:
        subprocess.run(
            [
                pdftoppm,
                "-jpeg",
                "-f",
                "1",
                "-l",
                str(pages),
                "-scale-to",
                str(settings.CHAT_ATTACHMENT_RENDER_LONG_EDGE),
                "-jpegopt",
                f"quality={settings.CHAT_ATTACHMENT_JPEG_QUALITY}",
                str(path),
                str(prefix),
            ],
            check=True,
            capture_output=True,
            timeout=settings.CHAT_ATTACHMENT_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AttachmentError("Rendering the document took too long.") from exc
    except subprocess.CalledProcessError as exc:
        raise AttachmentError("The document pages could not be rendered.") from exc
    rendered = sorted(output.glob("page-*.jpg"), key=lambda item: int(item.stem.split("-")[-1]))
    if len(rendered) != pages:
        raise AttachmentError("Not every document page could be rendered.")
    return rendered


def _soffice() -> str:
    configured = settings.CHAT_ATTACHMENT_SOFFICE_PATH.strip()
    candidates = [
        configured,
        shutil.which("soffice") or "",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        "/Applications/LibreOfficeDev.app/Contents/MacOS/soffice",
    ]
    # Codex desktop bundles a headless LibreOffice runtime on this workstation.
    # It is only a fallback; deployments should install LibreOffice or set the
    # explicit setting above.
    candidates.extend(
        str(path)
        for path in (Path.home() / ".cache/codex-runtimes").glob(
            "*/dependencies/bin/override/soffice"
        )
    )
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise AttachmentError("DOCX support is unavailable because LibreOffice is not installed.")


def _docx_to_pdf(source: Path, output: Path) -> Path:
    try:
        with zipfile.ZipFile(source) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise AttachmentError("The DOCX file is invalid.")
    except (zipfile.BadZipFile, OSError) as exc:
        raise AttachmentError("The DOCX file is invalid.") from exc

    profile = output / "libreoffice-profile"
    profile.mkdir()
    try:
        subprocess.run(
            [
                _soffice(),
                "--headless",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(output),
                str(source),
            ],
            check=True,
            capture_output=True,
            timeout=settings.CHAT_ATTACHMENT_PROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AttachmentError("Converting the DOCX file took too long.") from exc
    except subprocess.CalledProcessError as exc:
        raise AttachmentError("The DOCX file could not be converted.") from exc
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = output / f"{source.stem}.pdf"
    if not pdf.is_file():
        raise AttachmentError("The DOCX file did not produce a readable document.")
    return pdf


def _cleanup_expired(now: float | None = None) -> None:
    now = now or time.time()
    _ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    for entry in _ROOT.iterdir():
        if not entry.is_dir():
            continue
        try:
            expired = now - entry.stat().st_mtime > settings.CHAT_ATTACHMENT_TTL_SECONDS
        except OSError:
            continue
        if expired:
            shutil.rmtree(entry, ignore_errors=True)


def prepare_attachment(stream, filename: str | None, user_id: object) -> dict:
    """Validate and preprocess one upload, returning browser-safe metadata."""
    _cleanup_expired()
    name = _safe_filename(filename)
    extension = Path(name).suffix.lower()
    allowed = _IMAGE_EXTENSIONS | _TEXT_EXTENSIONS | _DOCUMENT_EXTENSIONS
    if extension not in allowed:
        raise AttachmentError("Supported attachments are images, TXT, MD, PDF, and DOCX files.")

    limit = settings.CHAT_ATTACHMENT_MAX_UPLOAD_MB * 1024 * 1024
    data = _read_bounded(stream, limit)
    if not data:
        raise AttachmentError("The attachment is empty.")

    attachment_id = secrets.token_urlsafe(24)
    directory = _ROOT / attachment_id
    directory.mkdir(mode=0o700, parents=True)
    kind: Literal["image", "text", "document"]
    media_type: str
    text: str | None = None
    image_files: list[Path] = []
    try:
        if extension in _IMAGE_EXTENSIONS:
            media_type = _image_media_type(data) or ""
            if not media_type:
                raise AttachmentError("The selected image is not a valid JPG, PNG, or WebP file.")
            kind = "image"
            image = directory / f"image{extension if extension != '.jpeg' else '.jpg'}"
            image.write_bytes(data)
            image_files = [image]
        elif extension in _TEXT_EXTENSIONS:
            kind = "text"
            media_type = "text/markdown" if extension in {".md", ".markdown"} else "text/plain"
            if b"\x00" in data:
                raise AttachmentError("The text attachment appears to be a binary file.")
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AttachmentError("Text and Markdown attachments must use UTF-8 encoding.") from exc
            if len(text) > settings.CHAT_ATTACHMENT_MAX_TEXT_CHARS:
                raise AttachmentError(
                    f"The text attachment exceeds {settings.CHAT_ATTACHMENT_MAX_TEXT_CHARS:,} characters."
                )
        else:
            kind = "document"
            media_type = (
                "application/pdf"
                if extension == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            source = directory / ("document.pdf" if extension == ".pdf" else "document.docx")
            source.write_bytes(data)
            if extension == ".pdf":
                if not data.startswith(b"%PDF-"):
                    raise AttachmentError("The selected PDF file is invalid.")
                pdf = source
            else:
                pdf = _docx_to_pdf(source, directory)
            image_files = _render_pdf(pdf, directory)
            source.unlink(missing_ok=True)
            if pdf != source:
                pdf.unlink(missing_ok=True)

        manifest = {
            "id": attachment_id,
            "user_id": str(user_id),
            "filename": name,
            "kind": kind,
            "media_type": media_type,
            "size": len(data),
            "text": text,
            "images": [path.name for path in image_files],
            "created_at": time.time(),
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise

    return {
        "id": attachment_id,
        "filename": name,
        "kind": kind,
        "mediaType": media_type,
        "size": len(data),
        "pageCount": len(image_files) if kind == "document" else None,
    }


def resolve_attachments(ids: list[str], user_id: object) -> list[ResolvedAttachment]:
    """Resolve ids for this caller into model-ready text and image payloads."""
    _cleanup_expired()
    resolved: list[ResolvedAttachment] = []
    total_images = 0
    total_text = 0
    for attachment_id in ids:
        if not _ID.fullmatch(attachment_id):
            raise AttachmentError("An attachment reference is invalid.")
        directory = _ROOT / attachment_id
        manifest_path = directory / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AttachmentError("An attachment expired. Please attach it again.") from exc
        if manifest.get("user_id") != str(user_id):
            # Do not disclose whether another user's opaque id exists.
            raise AttachmentError("An attachment expired. Please attach it again.")

        images: list[CapturePayload] = []
        image_names = list(manifest.get("images") or [])
        total_images += len(image_names)
        if total_images > settings.CHAT_ATTACHMENT_MAX_TOTAL_IMAGES:
            raise AttachmentError(
                f"Attachments may contain at most {settings.CHAT_ATTACHMENT_MAX_TOTAL_IMAGES} images or pages in one message."
            )
        for index, image_name in enumerate(image_names, start=1):
            image_path = directory / Path(str(image_name)).name
            try:
                image_data = image_path.read_bytes()
            except OSError as exc:
                raise AttachmentError("An attachment expired. Please attach it again.") from exc
            media_type = _image_media_type(image_data)
            if not media_type:
                raise AttachmentError("A prepared attachment image is invalid.")
            images.append(
                CapturePayload(
                    id=f"{attachment_id}-{index}",
                    label=f"{manifest['filename']} — page {index} of {len(image_names)}",
                    mediaType=media_type,
                    data=base64.b64encode(image_data).decode("ascii"),
                )
            )
        item_text = manifest.get("text")
        total_text += len(item_text or "")
        if total_text > settings.CHAT_ATTACHMENT_MAX_TOTAL_TEXT_CHARS:
            raise AttachmentError(
                f"Text attachments may contain at most {settings.CHAT_ATTACHMENT_MAX_TOTAL_TEXT_CHARS:,} characters in one message."
            )
        resolved.append(
            ResolvedAttachment(
                id=attachment_id,
                filename=str(manifest["filename"]),
                kind=manifest["kind"],
                media_type=str(manifest["media_type"]),
                size=int(manifest["size"]),
                text=item_text,
                images=tuple(images),
            )
        )
    return resolved
