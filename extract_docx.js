const fs = require('fs');
const zlib = require('zlib');

const filePath = 'C:/Users/arpit.c.srivastava/Downloads/S4PC-Catalyst-v1.0/input/FD Test AI Stock Monitoring.docx.md';

// Read the ZIP file
const data = fs.readFileSync(filePath);

// Find the central directory to enumerate files
// Look for End of Central Directory record (PK\x05\x06)
let eocdOffset = -1;
for (let i = data.length - 22; i >= 0; i--) {
    if (data[i] === 0x50 && data[i+1] === 0x4b && data[i+2] === 0x05 && data[i+3] === 0x06) {
        eocdOffset = i;
        break;
    }
}

if (eocdOffset === -1) {
    console.error('Could not find End of Central Directory');
    process.exit(1);
}

const cdOffset = data.readUInt32LE(eocdOffset + 16);
const cdSize = data.readUInt32LE(eocdOffset + 12);
const numEntries = data.readUInt16LE(eocdOffset + 10);

// Parse central directory entries
let pos = cdOffset;
const files = {};

for (let i = 0; i < numEntries; i++) {
    if (data[pos] !== 0x50 || data[pos+1] !== 0x4b || data[pos+2] !== 0x01 || data[pos+3] !== 0x02) break;
    const compMethod = data.readUInt16LE(pos + 10);
    const compSize = data.readUInt32LE(pos + 20);
    const uncompSize = data.readUInt32LE(pos + 24);
    const fnLen = data.readUInt16LE(pos + 28);
    const extraLen = data.readUInt16LE(pos + 30);
    const commentLen = data.readUInt16LE(pos + 32);
    const localOffset = data.readUInt32LE(pos + 42);
    const filename = data.slice(pos + 46, pos + 46 + fnLen).toString('utf8');
    files[filename] = { compMethod, compSize, uncompSize, localOffset };
    pos += 46 + fnLen + extraLen + commentLen;
}

// Find word/document.xml
const entry = files['word/document.xml'];
if (!entry) {
    console.error('word/document.xml not found. Files:', Object.keys(files).join(', '));
    process.exit(1);
}

// Read local file header
const lPos = entry.localOffset;
const lFnLen = data.readUInt16LE(lPos + 26);
const lExtraLen = data.readUInt16LE(lPos + 28);
const dataStart = lPos + 30 + lFnLen + lExtraLen;
const compData = data.slice(dataStart, dataStart + entry.compSize);

// Decompress
let xmlBuf;
if (entry.compMethod === 8) {
    xmlBuf = zlib.inflateRawSync(compData);
} else if (entry.compMethod === 0) {
    xmlBuf = compData;
} else {
    console.error('Unknown compression method:', entry.compMethod);
    process.exit(1);
}

const xml = xmlBuf.toString('utf8');

// Extract text from XML - parse w:p paragraphs and w:t text elements
const paragraphs = [];
const pRegex = /<w:p[ >](.*?)<\/w:p>/gs;
const tRegex = /<w:t[^>]*>(.*?)<\/w:t>/gs;

let pMatch;
while ((pMatch = pRegex.exec(xml)) !== null) {
    const pContent = pMatch[1];
    const texts = [];
    let tMatch;
    const tReg = /<w:t[^>]*>(.*?)<\/w:t>/gs;
    while ((tMatch = tReg.exec(pContent)) !== null) {
        texts.push(tMatch[1]);
    }
    paragraphs.push(texts.join(''));
}

console.log(paragraphs.join('\n'));
