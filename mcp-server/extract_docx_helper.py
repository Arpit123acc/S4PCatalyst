#!/usr/bin/env python3
"""One-shot helper: extract plain text from a .docx (ZIP+XML) file."""
import zipfile
import re
import sys
import os

file_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "input",
    "FD Test AI Stock Monitoring.docx.md"
)

try:
    with zipfile.ZipFile(file_path, 'r') as z:
        with z.open('word/document.xml') as f:
            xml_content = f.read().decode('utf-8')

    # Remove XML tags
    text = re.sub(r'<[^>]+>', ' ', xml_content)
    # Collapse horizontal whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    # Collapse multiple blank lines
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = text.strip()

    print(text)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
