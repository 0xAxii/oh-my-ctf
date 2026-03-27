# FORENSICS — Digital Forensics & Steganography

## Tools
binwalk, foremost, exiftool, steghide, zsteg, stegsolve, tshark, volatility3, file, xxd, strings, ImageMagick

## Workflow
1. Identify — file type, structure, metadata
2. Extract — carve embedded files, decompress layers
3. Analyze — memory dumps, network captures, disk images
4. Decode — encoding chains (base64, hex, rot13, custom)
5. Flag — extract from hidden data

## Patterns
### File Analysis
- Unknown file → file, binwalk, xxd header check
- Nested archives → recursive extraction
- Corrupted header → fix magic bytes manually

### Steganography
- PNG → zsteg, LSB extraction
- JPEG → steghide (with/without password), jsteg
- Audio → spectrogram (sox/audacity), LSB
- Image visual → stegsolve bit planes, color channels

### Memory Forensics
- volatility3: windows.info, windows.pslist, windows.filescan, windows.dumpfiles
- Look for: browser history, clipboard, commands, passwords, keys

### Network
- tshark/wireshark: follow TCP streams, extract files, HTTP objects
- DNS exfiltration: long subdomain queries
- ICMP tunneling: payload in echo data

## Key Rules
- Always check exiftool metadata first
- binwalk -e for automatic extraction
- Multiple encoding layers are common — decode iteratively
- Write findings to findings_raw.md as you discover them
