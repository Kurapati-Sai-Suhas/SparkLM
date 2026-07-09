"""
groups/utils.py

FIX-01 (CRITICAL): Output normalization for Judge0 test-case comparison.

Root cause: Judge0 returns stdout with a trailing newline ('42\n') while
expected_output stored in the DB does not ('42'). A raw '==' comparison
fails on every correct submission. This was the #1 reported bug
(correct code marked as Wrong Answer / 0 test cases passed).
"""


def normalize_output(raw: str) -> str:
    """
    Normalize program output for comparison.

    - Handles trailing/leading whitespace (Judge0 trailing '\\n').
    - Handles Windows line endings ('\\r\\n' -> '\\n').
    - Strips trailing whitespace per line without collapsing intentional
      internal blank lines.
    """
    if raw is None:
        return ''
    lines = [line.rstrip() for line in raw.strip().splitlines()]
    return '\n'.join(lines)


def extract_text_from_file(file_path):
    """Reads text from PDF, DOCX, or TXT files based on extension.

    (Restored — the FIX-01 rewrite of this module dropped it while
    views.py still imports it for document processing.)
    """
    import os
    import PyPDF2
    import docx

    print(f"🔍 Attempting to read file at: {file_path}")
    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            with open(file_path, 'rb') as file:
                reader = PyPDF2.PdfReader(file)
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        text += extracted + "\n"
        elif ext == '.docx':
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"
        elif ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as file:
                text = file.read()
        else:
            print(f"⚠️ Unsupported file type: {ext}")
            return None

        print(f"✅ Extracted {len(text)} characters from {ext} file.")
        return text

    except Exception as e:
        print(f"❌ File Read Error: {e}")
        return ""


def load_image_for_ai(file_path):
    """Opens an image file and prepares it for Gemini Vision. (Restored.)"""
    import PIL.Image

    print(f"🖼️ Opening Image: {file_path}")
    try:
        img = PIL.Image.open(file_path)
        return img
    except Exception as e:
        print(f"❌ Image Load Error: {e}")
        return None


def all_test_cases_passed(judge0_results: list, hidden_test_cases: list) -> bool:
    """
    FIX-01 (extension): Guard against silent truncation.

    zip() stops at the shorter of the two lists. If Judge0 fails to return
    a result for one or more hidden test cases (crash, timeout, sandbox
    error), a naive `all(zip(...))` would only check the cases that DID
    come back and could report all_passed=True on an incomplete run.
    We explicitly fail the submission if the lengths don't match instead
    of silently comparing a subset.
    """
    if len(judge0_results) != len(hidden_test_cases):
        return False

    return all(
        normalize_output(r.get('stdout', '')) == normalize_output(tc['expected'])
        for r, tc in zip(judge0_results, hidden_test_cases)
    )
