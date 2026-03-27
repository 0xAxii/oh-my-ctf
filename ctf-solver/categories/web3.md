# WEB3 — Smart Contract Security

## Tools
Slither, Mythril, Foundry (forge/cast/anvil), Semgrep, solc, ethers.js

## Workflow
1. Source Analysis — read contracts, identify state variables, access control
2. Static Analysis — Slither detectors + Semgrep rules
3. Vulnerability Identification — reentrancy, flash loan, oracle manipulation, etc
4. PoC Development — Foundry test (forge test -vvvv)
5. Exploit Execution — cast send / forge script against target

## Vulnerability Patterns
### Access Control
- Missing onlyOwner / auth checks
- tx.origin vs msg.sender confusion
- Uninitialized proxy / storage collision

### Reentrancy
- External call before state update
- Cross-function reentrancy
- Read-only reentrancy (view function)

### Flash Loan / Oracle
- Price oracle manipulation via flash loan
- Sandwich attack vectors
- TWAP oracle bypass

### Logic
- Integer overflow/underflow (pre-0.8.0)
- Rounding errors in token math
- Front-running / MEV

## Key Rules
- Always use Anvil fork for local testing: anvil --fork-url <rpc>
- forge test -vvvv for full trace
- Check storage layout for proxy contracts
- Write findings to findings_raw.md as you discover them
