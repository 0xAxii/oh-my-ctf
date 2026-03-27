# PWN — Binary Exploitation

## Tools
gdb, pwndbg, pwntools, ROPgadget, one_gadget, checksec, Ghidra headless, angr, capstone, objdump, readelf, strings, file, ldd

## Workflow
1. Recon — checksec, file, strings, readelf, ldd
2. Static — Ghidra headless decompile. Find: gets/strcpy/printf(buf)/free-without-null
3. Dynamic — GDB: cyclic pattern offset, address verification, heap layout
4. Exploit — pwntools: leak → control → payload. Test each phase before next
5. Remote — process() → remote(host, port). Timeout 30s

## Protection Bypass
- NX ON → ROP / ret2libc
- PIE ON → runtime leak required
- PIE OFF → absolute addresses
- Canary ON → format string leak or brute force
- RELRO Full → FSOP (glibc ≥ 2.34)
- RELRO Partial → GOT overwrite
- glibc ≥ 2.34 → no hooks, use FSOP or setcontext ROP
- glibc ≥ 2.32 → tcache safe-linking, heap leak required

## Heap Quick Reference
- tcache (≥ 2.26): tcache poisoning, tcache stashing unlink
- fastbin: fastbin dup, fastbin reverse into tcache
- unsorted bin: unsorted bin attack, house of einherjar
- large bin: large bin attack, house of storm

## Key Rules
- ALL addresses/offsets MUST come from GDB output, not memory
- Test exploit locally with: python3 solve.py | ./binary
- Write findings to findings_raw.md as you discover them
