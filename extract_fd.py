import zipfile, xml.etree.ElementTree as ET, sys

path = "input/FD Test AI Stock Monitoring.docx.md"
with zipfile.ZipFile(path, 'r') as z:
    with z.open('word/document.xml') as f:
        tree = ET.parse(f)
        root = tree.getroot()
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        texts = []
        for para in root.iter(f'{{{ns}}}p'):
            runs = para.findall(f'.//{{{ns}}}t')
            line = ''.join(r.text or '' for r in runs)
            if line.strip():
                texts.append(line)
        print('\n'.join(texts))
