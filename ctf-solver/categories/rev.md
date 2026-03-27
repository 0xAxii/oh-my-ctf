# REV — Reverse Engineering

## Tools
Ghidra headless, gdb, angr, z3, Frida, capstone, objdump, readelf, strings, file, strace, ltrace

## Workflow
1. Recon — file, strings, checksec
2. Decompile — Ghidra headless → main + key functions
3. Algorithm Analysis — trace logic, identify transformations, constraints
4. Constraint Solving — z3/angr script generation + execution
5. Verification — python3 solve.py → flag output

## Patterns
- Simple XOR/Caesar → brute force or frequency analysis
- Custom cipher → reverse the algorithm, z3 constraints
- VM/bytecode → trace dispatch loop, extract opcodes, write disassembler
- Anti-debug (ptrace) → patch or Frida hook
- Packed/obfuscated → unpack first (upx, manual), then analyze
- Self-modifying → dynamic trace with GDB, dump modified code

## Key Rules
- Decompiler output is a starting point, verify with dynamic analysis
- For z3: model ALL constraints from binary. Missing one = wrong answer
- angr for path exploration when manual analysis is impractical
- Write findings to findings_raw.md as you discover them
