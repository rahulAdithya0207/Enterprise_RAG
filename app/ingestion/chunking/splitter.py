from typing import List

import logfire


def chunk_text(text: str, chunk_size: int = 1500) -> List[str]:
    """
    Split raw document text into chunks that fit within chunk_size characters.

    The splitter treats double newlines as paragraph boundaries, accumulating
    paragraphs into a growing chunk until the next paragraph would exceed the
    size limit, at which point the current chunk is flushed and a new one begins.
    This approach keeps semantically related sentences together rather than
    cutting mid-paragraph.
    """
    with logfire.span("Chunk text", text_length=len(text)):
        if not text.strip():
            return []

        paragraphs = text.split("\n\n")
        chunks: List[str] = []
        current = ""

        for paragraph in paragraphs:
            if len(current) + len(paragraph) < chunk_size:
                current += paragraph + "\n\n"
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = paragraph + "\n\n"

        if current.strip():
            chunks.append(current.strip())

        valid = [c for c in chunks if c.strip()]
        logfire.info(f"Generated {len(valid)} chunks from {len(text)} characters.")
        return valid
