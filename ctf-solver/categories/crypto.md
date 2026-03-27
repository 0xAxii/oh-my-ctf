# CRYPTO — Cryptography

## Tools
SageMath, z3, gmpy2, pycryptodome, RsaCtfTool, cado-nfs, hashcat, john, openssl

## Workflow
1. Identify — cryptographic primitives, parameters, weakness
2. Attack — SageMath/z3 attack script
3. Execute — run script → extract flag
4. Verify — flag format check

## Attack Patterns
### RSA
- Small e → Coppersmith (small_roots)
- Common p between keys → GCD
- Weak key → factordb, RsaCtfTool, yafu
- Wiener (small d) → continued fractions
- Hastad broadcast → CRT + nth root
- Bleichenbacher → padding oracle

### AES
- ECB → block substitution/reordering
- CBC → bit flipping, padding oracle
- CTR → nonce reuse = XOR plaintext

### Elliptic Curve
- Small order → Pohlig-Hellman
- Smart's attack → anomalous curve
- MOV attack → Weil pairing

### Classical
- XOR → frequency analysis, known plaintext
- Substitution → frequency + crib dragging
- Vigenere → Kasiski, index of coincidence

## Key Rules
- NEVER do math in your head. Use SageMath/z3 for all computation
- Check if the key/flag space is small enough for brute force first
- Write findings to findings_raw.md as you discover them
