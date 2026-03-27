# MISC — Miscellaneous

## Tools
python3, PIL/Pillow, numpy, scipy, ffmpeg, sox, tesseract OCR, qrtools, pyzbar

## Workflow
1. Identify — what type of challenge is this? (programming, OSINT, stego, crypto hybrid, game, etc)
2. Analyze — use appropriate tools based on type
3. Solve — script/manual depending on complexity
4. Flag — extract from output

## Common Types
### Programming
- Algorithm challenge → write solution script
- Brute force → optimize with constraints
- Maze/path → BFS/DFS/A*

### OSINT
- Image geolocation → metadata, landmarks, signs
- Username search → social media, archives
- Domain/IP recon → whois, DNS records

### Encoding/Decoding
- Base64, base32, hex, binary, morse, braille
- Multi-layer encoding — decode iteratively
- Custom encoding — reverse the algorithm

### QR/Barcode
- Damaged QR → error correction recovery
- Partial QR → reconstruct from known structure

### Audio/Visual
- Spectrogram hidden message
- DTMF tones → phone numbers
- Morse code in audio

## Key Rules
- Misc is unpredictable — try the obvious first, then get creative
- Ask user for help with OCR, visual analysis, or physical-world knowledge
- Write findings to findings_raw.md as you discover them
