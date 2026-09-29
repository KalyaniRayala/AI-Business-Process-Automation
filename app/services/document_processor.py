from pathlib import Path

from docx import Document
import pandas as pd
import pymupdf


# ============================================================
# STEP 3 — PDF DOCUMENT INGESTION
# ============================================================

def load_pdf_document(file_path: str) -> str:
    """
    Extract text from a PDF document using PyMuPDF.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    if path.suffix.lower() != ".pdf":
        raise ValueError(
            "Expected a PDF file."
        )

    pages = []

    try:
        with pymupdf.open(file_path) as pdf:

            for page in pdf:

                text = page.get_text("text")

                if text:
                    pages.append(text)

    except Exception as error:
        raise ValueError(
            f"Could not read PDF: {error}"
        )

    extracted_text = "\n".join(pages)

    if not extracted_text.strip():
        raise ValueError(
            "PDF was found, but no readable text could be extracted."
        )

    return extracted_text


# ============================================================
# STEP 3 — TXT DOCUMENT INGESTION
# ============================================================

def load_text_document(file_path: str) -> str:
    """
    Read text from a TXT document.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    if path.suffix.lower() != ".txt":
        raise ValueError(
            "Expected a TXT file."
        )

    return path.read_text(
        encoding="utf-8"
    )


# ============================================================
# STEP 3 — DOCX DOCUMENT INGESTION
# ============================================================

def load_docx_document(file_path: str) -> str:
    """
    Extract text from a DOCX document.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    if path.suffix.lower() != ".docx":
        raise ValueError(
            "Expected a DOCX file."
        )

    document = Document(file_path)

    paragraphs = []

    for paragraph in document.paragraphs:

        text = paragraph.text.strip()

        if text:
            paragraphs.append(text)

    return "\n".join(paragraphs)


# ============================================================
# STEP 3 — CSV DOCUMENT INGESTION
# ============================================================

def load_csv_document(file_path: str) -> str:
    """
    Read structured data from a CSV file
    and convert it into text.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    if path.suffix.lower() != ".csv":
        raise ValueError(
            "Expected a CSV file."
        )

    dataframe = pd.read_csv(file_path)

    return dataframe.to_csv(
        index=False
    )


# ============================================================
# STEP 3 — UNIFIED DOCUMENT LOADER
# ============================================================

def load_document(file_path: str) -> str:
    """
    Automatically select the correct document
    loader based on file extension.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Document not found: {file_path}"
        )

    extension = path.suffix.lower()

    if extension == ".pdf":
        return load_pdf_document(file_path)

    elif extension == ".txt":
        return load_text_document(file_path)

    elif extension == ".docx":
        return load_docx_document(file_path)

    elif extension == ".csv":
        return load_csv_document(file_path)

    else:
        raise ValueError(
            f"Unsupported file type: {extension}"
        )


# ============================================================
# STEP 4 — DATA CLEANING
# ============================================================

def clean_text(text: str) -> str:
    """
    Normalize extracted document text.

    STEP 4:
    - Remove unnecessary spaces
    - Remove empty lines
    - Normalize whitespace
    """

    lines = []

    for line in text.splitlines():

        cleaned_line = " ".join(
            line.split()
        )

        if cleaned_line:
            lines.append(cleaned_line)

    return "\n".join(lines)


# ============================================================
# STEP 5 — CHUNKING
# ============================================================

def create_chunks(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50
) -> list[str]:
    """
    Split text into overlapping chunks.

    chunk_size:
        Number of words in each chunk.

    overlap:
        Number of words shared between
        consecutive chunks.
    """

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size must be greater than 0."
        )

    if overlap < 0:
        raise ValueError(
            "overlap cannot be negative."
        )

    if overlap >= chunk_size:
        raise ValueError(
            "overlap must be smaller than chunk_size."
        )

    words = text.split()

    chunks = []

    start = 0

    while start < len(words):

        end = start + chunk_size

        chunk = " ".join(
            words[start:end]
        )

        if chunk:
            chunks.append(chunk)

        start += chunk_size - overlap

    return chunks


# ============================================================
# STEP 5 — CHUNK METADATA
# ============================================================

def create_chunk_records(
    chunks: list[str],
    source: str
) -> list[dict]:
    """
    Create metadata records for each chunk.

    These records will later be used by
    the embedding and vector database steps.
    """

    records = []

    for index, chunk in enumerate(chunks):

        records.append(
            {
                "chunk_id": index,
                "text": chunk,
                "source": source
            }
        )

    return records

# ============================================================
# TESTING
# ============================================================

if __name__ == "__main__":

    # ========================================================
    # TEST 1 — TXT
    # ========================================================

    txt_path = "data/expense_process.txt"

    print("\n==============================")
    print("TXT DOCUMENT")
    print("==============================")

    try:

        txt_content = load_document(txt_path)

        print(txt_content)

    except Exception as error:

        print(f"TXT error: {error}")


    # ========================================================
    # TEST 2 — PDF
    # ========================================================

    pdf_path = "data/gitlab_travel_expense_policy_clean.pdf"

    print("\n==============================")
    print("PDF DOCUMENT")
    print("==============================")

    try:

        pdf_content = load_document(pdf_path)

        print(pdf_content[:3000])

    except Exception as error:

        print(f"PDF error: {error}")


    # ========================================================
    # TEST 3 — CLEANING
    # ========================================================

    print("\n==============================")
    print("TEXT CLEANING")
    print("==============================")

    cleaned_content = clean_text(txt_content)

    print(cleaned_content)


    # ========================================================
    # TEST 4 — CHUNKING
    # ========================================================

    print("\n==============================")
    print("CHUNKS")
    print("==============================")

    chunks = create_chunks(
        cleaned_content,
        chunk_size=50,
        overlap=10
    )

    for index, chunk in enumerate(chunks):

        print(f"\n--- CHUNK {index} ---")

        print(chunk)


    # ========================================================
    # TEST 5 — CHUNK RECORDS
    # ========================================================

    print("\n==============================")
    print("CHUNK RECORDS")
    print("==============================")

    records = create_chunk_records(
        chunks,
        source=txt_path
    )

    for record in records:

        print(record)