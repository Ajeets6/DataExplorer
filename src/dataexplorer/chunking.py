import hashlib
import re

from dataexplorer.models import StoredChunk, StoredDocument


def chunk_document(
    document: StoredDocument,
    *,
    maximum_characters: int = 1_200,
    overlap_characters: int = 120,
) -> list[StoredChunk]:
    """Split on paragraph/sentence boundaries while preserving source lineage."""

    if overlap_characters >= maximum_characters:
        raise ValueError("chunk overlap must be smaller than chunk size")
    normalized = document.text.replace("\r\n", "\n").strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    pieces: list[str] = []
    current = ""
    for paragraph in paragraphs or [normalized]:
        for candidate in _split_oversized(paragraph, maximum_characters):
            merged = f"{current}\n\n{candidate}".strip() if current else candidate
            if current and len(merged) > maximum_characters:
                pieces.append(current)
                prefix = current[-overlap_characters:].lstrip() if overlap_characters else ""
                current = f"{prefix}\n{candidate}".strip()
            else:
                current = merged
    if current:
        pieces.append(current)

    checksum = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return [
        StoredChunk(
            chunk_id=f"{document.document_id}:{position}",
            document_id=document.document_id,
            tenant_id=document.tenant_id,
            title=document.title,
            text=text,
            position=position,
            allowed_groups=document.allowed_groups,
            classification=document.classification,
            trust_tier=document.trust_tier,
            version=document.version,
            valid_until=document.valid_until,
            content_sha256=checksum,
            metadata=document.metadata,
        )
        for position, text in enumerate(pieces)
    ]


def _split_oversized(text: str, maximum_characters: int) -> list[str]:
    if len(text) <= maximum_characters:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > maximum_characters:
            if current:
                pieces.append(current)
                current = ""
            pieces.extend(
                sentence[start : start + maximum_characters]
                for start in range(0, len(sentence), maximum_characters)
            )
            continue
        merged = f"{current} {sentence}".strip()
        if current and len(merged) > maximum_characters:
            pieces.append(current)
            current = sentence
        else:
            current = merged
    if current:
        pieces.append(current)
    return pieces
