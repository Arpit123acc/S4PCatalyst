import zipfile
import xml.etree.ElementTree as ET

path = 'C:/Users/arpit.c.srivastava/Downloads/S4PC-Catalyst-v1.0/input/FD Test AI Stock Monitoring.docx.md'

with zipfile.ZipFile(path) as z:
    xml_data = z.read('word/document.xml')

ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
root = ET.fromstring(xml_data)

out = []
for p in root.iter('{%s}p' % ns):
    line = ''.join(t.text for t in p.iter('{%s}t' % ns) if t.text)
    out.append(line)

print('\n'.join(out))
