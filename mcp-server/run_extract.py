#!/usr/bin/env python3
"""
Standalone docx extractor — saves full text to mcp-server/docx_extracted.txt
Run as: python mcp-server/run_extract.py
from the project root.
"""
import zipfile
import struct
import zlib
import re
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
file_path = os.path.join(BASE, "..", "input", "FD Test AI Stock Monitoring.docx.md")
file_path = os.path.abspath(file_path)
out_path = os.path.join(BASE, "docx_extracted.txt")

def extract_xml_text(file_path):
    # Try standard ZIP first
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            with z.open("word/document.xml") as f:
                xml = f.read().decode("utf-8")
        return xml, "standard_zip"
    except Exception as e1:
        pass

    # Manual local-header scan
    with open(file_path, "rb") as fh:
        raw = fh.read()

    pos = 0
    files_found = []
    while True:
        idx = raw.find(b"\x50\x4b\x03\x04", pos)
        if idx == -1:
            break
        try:
            comp_method = struct.unpack_from("<H", raw, idx + 8)[0]
            comp_size   = struct.unpack_from("<I", raw, idx + 18)[0]
            uncomp_size = struct.unpack_from("<I", raw, idx + 22)[0]
            fname_len   = struct.unpack_from("<H", raw, idx + 26)[0]
            extra_len   = struct.unpack_from("<H", raw, idx + 28)[0]
            if 0 < fname_len < 512:
                fname = raw[idx + 30: idx + 30 + fname_len].decode("ascii", errors="replace")
                data_start = idx + 30 + fname_len + extra_len
                files_found.append((idx, fname, comp_method, comp_size, uncomp_size, data_start))
        except Exception:
            pass
        pos = idx + 4

    print(f"[extract] found {len(files_found)} local file entries")
    for (idx, fname, cm, cs, us, ds) in files_found:
        print(f"  @ {idx:8d}  method={cm}  cs={cs:8d}  us={us:8d}  {fname}")

    for (idx, fname, cm, cs, us, ds) in files_found:
        if fname == "word/document.xml":
            data = raw[ds: ds + cs]
            if cm == 8:  # deflate
                data = zlib.decompress(data, -15)
            elif cm == 0:  # stored
                pass
            return data.decode("utf-8", errors="replace"), "recovered"

    return None, "not_found"


xml_text, mode = extract_xml_text(file_path)
if xml_text is None:
    print("[extract] ERROR: word/document.xml not found in file")
    sys.exit(1)

# Strip XML tags
text = re.sub(r"<[^>]+>", " ", xml_text)
text = re.sub(r"[ \t]+", " ", text)
text = re.sub(r"\n\s*\n", "\n\n", text)
text = text.strip()

print(f"[extract] mode={mode}, chars={len(text)}, writing to {out_path}")
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)

print("[extract] DONE")
print()
print(text[:500])
