import json
import os
import sys
import uuid

# logfire must be configured before app imports so ingestion spans are captured.
import logfire
from app.config import settings

_logfire_base_url = settings.LOGFIRE_BASE_URL
if not _logfire_base_url and settings.LOGFIRE_TOKEN:
    if settings.LOGFIRE_TOKEN.startswith("pylf_v2_eu_"):
        _logfire_base_url = "https://logfire-eu.pydantic.dev"

if settings.LOGFIRE_TOKEN:
    logfire.configure(
        token=settings.LOGFIRE_TOKEN,
        service_name="enterprise-ingestion",
        advanced=logfire.AdvancedOptions(base_url=_logfire_base_url) if _logfire_base_url else None,
    )

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.ingestion.chunking.splitter import chunk_text
from app.ingestion.loaders.html import parse_html
from app.ingestion.loaders.pdf import parse_pdf
from app.ingestion.loaders.text import parse_text
from app.services.retrieval.embedding import embed_texts, get_embedding_dim

# Parsed chunks are saved here as JSON before being embedded and indexed.
PROCESSED_DATA_DIR = "processed_data"

from app.services.retrieval.qdrant_service import client as qdrant_client


def _get_source_type(name: str) -> str:
    """
    Infer a source type label from a folder or file name.

    Directories containing 'true' map to 'true', those with 'noisy' to 'noisy',
    and everything else uses the name as-is.
    """
    lower = name.lower()
    if "true" in lower:
        return "true"
    if "noisy" in lower:
        return "noisy"
    return name


def save_processed_chunk(data: dict, source_type: str, filename: str) -> str:
    """Write parsed chunk metadata as a JSON file under processed_data/<source_type>/."""
    folder = os.path.join(PROCESSED_DATA_DIR, source_type)
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"{filename}.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return dest


def process_file(file_path: str, filename: str, source_type: str):
    """
    Full ingestion pipeline for a single document file.

    Steps:
      1. Parse the raw file into plain text (PDF, HTML, TXT, DOCX, PPTX).
      2. Split the text into overlapping chunks (≤ 1500 chars each).
      3. Save chunk metadata as JSON to processed_data/.
      4. Generate embeddings via the Jina API (or local fallback).
      5. Upsert the embedded points into Qdrant.
    """
    with logfire.span("Ingest file", file=filename, source=source_type):
        try:
            ext = filename.lower().rsplit(".", 1)[-1]
            if ext == "pdf":
                text = parse_pdf(file_path)
            elif ext in ("html", "htm"):
                text = parse_html(file_path)
            elif ext == "txt":
                text = parse_text(file_path)
            elif ext in ("docx", "pptx"):
                from app.ingestion.loaders.office import parse_office
                text = parse_office(file_path)
            else:
                logfire.warning(f"Skipping unsupported file type: {filename}")
                return

            if not text or not text.strip():
                logfire.warning(f"No text extracted from {filename} — skipping.")
                return

            chunks = chunk_text(text)
            if not chunks:
                return

            local_path = save_processed_chunk(
                {"filename": filename, "source_type": source_type, "chunks": chunks},
                source_type,
                filename,
            )
            logfire.info(f"Saved processed chunks to {local_path}")

            with logfire.span("Vectorize and index"):
                embeddings = embed_texts(chunks)
                points = [
                    models.PointStruct(
                        id=str(uuid.uuid4()),
                        vector=vector,
                        payload={"text": chunk, "source": filename, "source_type": source_type},
                    )
                    for chunk, vector in zip(chunks, embeddings)
                ]
                qdrant_client.upsert(collection_name=settings.QDRANT_COLLECTION, points=points)
                logfire.info(f"Indexed {len(points)} vectors from {filename}.")

        except Exception as exc:
            logfire.error(f"Failed to process {filename}: {exc}")


def process_directory(dir_path: str, source_type: str):
    """Iterate over every file in a directory and run process_file on each."""
    with logfire.span("Scan directory", path=dir_path, source=source_type):
        files = [f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))]
        logfire.info(f"Found {len(files)} files in {dir_path}.")
        for filename in files:
            process_file(os.path.join(dir_path, filename), filename, source_type)


def run_ingestion(base_dir: str, explicit_source_type: str = None, wipe: bool = False):
    """
    Entry point for ingesting an entire document collection into Qdrant.

    When wipe=True, the existing collection is deleted before a fresh run.
    If base_dir contains sub-folders, each sub-folder is treated as a separate
    source type. Otherwise the whole directory is processed as one source.
    """
    with logfire.span("Universal ingestion", base_directory=base_dir):
        if wipe and qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
            qdrant_client.delete_collection(settings.QDRANT_COLLECTION)
            logfire.info(f"Dropped collection '{settings.QDRANT_COLLECTION}'.")

        if not qdrant_client.collection_exists(settings.QDRANT_COLLECTION):
            dim = get_embedding_dim()
            qdrant_client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            logfire.info(f"Created collection '{settings.QDRANT_COLLECTION}' ({dim}-dim, Cosine).")

        subdirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        if not subdirs:
            source_type = explicit_source_type or _get_source_type(os.path.basename(base_dir))
            logfire.info(f"No sub-folders — processing '{base_dir}' as source type '{source_type}'.")
            process_directory(base_dir, source_type)
        else:
            for subdir in subdirs:
                process_directory(os.path.join(base_dir, subdir), _get_source_type(subdir))


if __name__ == "__main__":
    # Usage:
    #   python -m app.ingestion.processor DATA --wipe
    #   python -m app.ingestion.processor DATA/true_data true
    wipe_requested = "--wipe" in sys.argv
    clean_args = [a for a in sys.argv if a != "--wipe"]

    target_dir = clean_args[1] if len(clean_args) > 1 else "DATA"
    explicit_type = clean_args[2] if len(clean_args) > 2 else None

    if not os.path.exists(target_dir):
        print(f"Error: path '{target_dir}' does not exist.")
        sys.exit(1)

    run_ingestion(target_dir, explicit_source_type=explicit_type, wipe=wipe_requested)
    logfire.info("Ingestion complete.")
