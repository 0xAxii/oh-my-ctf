# CRC Power Writeup

## Challenge Overview

The challenge service in [`chal.py`](./chal.py) does three important things:

1. It generates a random hidden `PREFIX` and `SUFFIX` once per process.
2. It picks a random base `a mod p`.
3. For every attacker-chosen hex input `inp`, it computes:

```python
inp = bytes.fromhex(input('> ').strip())
crc_res = crc64.xz(PREFIX + inp + SUFFIX)
pow_res = pow(a, crc_res, p)
```

If `pow_res == 1`, it prints the flag. Otherwise it prints `hex(pow_res)` and lets us try again.

The relevant code is:

```python
PREFIX = os.urandom(random.randint(100, 1000))
SUFFIX = os.urandom(random.randint(100, 1000))
p = 0x273e0ccdd855bd09f6e7c6c03d5f966d0f

def main():
    a = int.from_bytes(os.urandom((BITS + 7) // 8), 'little') % p
    assert a not in [0, 1, p - 1]

    while True:
        inp = bytes.fromhex(input('> ').strip())
        crc_res = crc64.xz(PREFIX + inp + SUFFIX)
        pow_res = pow(a, crc_res, p)

        if pow_res == 1:
            read_flag()
            break

        print(hex(pow_res))
```

There is also a 7-hex-character SHA-256 proof of work before the main loop.

## Vulnerability Analysis

### 1. The group order is fully smooth

The modulus `p` is prime, and:

```text
p - 1 = 2 * 107 * 109^6 * 127^4 * 137^3 * 139^4 * 149
```

That makes the multiplicative group `F_p^*` smooth. As a result, discrete logs are easy with Pohlig-Hellman. In practice, `sympy.ntheory.discrete_log` solved them fast enough.

This matters because the service leaks `pow(a, crc_res, p)` for unlimited chosen inputs. Once discrete logs are cheap, every printed group element becomes an algebraic equation in the hidden CRC value.

### 2. The checksum is only 64 bits

The challenge uses `fastcrc.crc64.xz`, so the exponent is always a 64-bit integer.

For a random `a`, its order modulo `p` is almost surely much larger than `2^64`, because `p-1` is 134 bits and the missing-large-order probability is negligible. That means the only practical way to force:

```text
a^crc_res ≡ 1 mod p
```

is to make:

```text
crc_res = 0
```

So the problem becomes:

```text
Find inp such that crc64.xz(PREFIX || inp || SUFFIX) = 0.
```

### 3. CRC64 gives an affine 64-bit permutation on an 8-byte block

For a fixed suffix length and a chosen 8-byte input block, the map:

```text
block -> crc64.xz(PREFIX || block || SUFFIX)
```

is an affine map over `GF(2)`, and empirically it has full rank 64. So for each fixed hidden `PREFIX` and `SUFFIX`, there is exactly one 8-byte block that makes the final CRC equal to zero.

Let:

```text
c = crc64.xz(PREFIX || 0^8 || SUFFIX)
```

Then for any 8-byte block `x`:

```text
crc(PREFIX || x || SUFFIX) = Mx xor c
```

for some invertible 64x64 binary matrix `M` that depends only on the suffix length, not on the actual random suffix bytes.

So if we can recover `c`, we can compute:

```text
x = M^-1 c
```

and that gives the unique winning 8-byte input.

### 4. The printed powers linearize the hidden CRC bits

Let `g` be a primitive root mod `p`. Since the group is smooth, we can compute:

```text
D(x) = log_g(pow(a, crc(PREFIX || x || SUFFIX), p))
     = alpha * crc(PREFIX || x || SUFFIX) mod (p-1)
```

where `alpha = log_g(a)`.

Now write the hidden CRC offset `c` in bits:

```text
c = sum c_j 2^j
```

If we query 64 carefully chosen 8-byte blocks, the CRC differences become linear combinations of:

```text
u_j = alpha * (1 - 2c_j) * 2^j mod ((p-1)/2)
```

So the service output gives a linear system modulo `((p-1)/2)`. Solving it reveals the set `{+alpha, -alpha}` after dividing out the powers of two, and the sign directly gives each bit `c_j`.

That is the core bug:

- CRC64 is linear.
- The service exposes a smooth-group exponentiation oracle.
- The unknown prefix/suffix only add a fixed affine offset.
- Unlimited chosen-input queries let us recover that offset exactly.

## Solution Approach

### Step 1. Read the challenge and identify the win condition

From [`chal.py`](./chal.py):

- `PREFIX` and `SUFFIX` are random but fixed for the session.
- The attacker fully controls `inp`.
- The flag is printed only when `pow(a, crc_res, p) == 1`.

That gives the initial target:

```text
ord_p(a) | crc_res
```

### Step 2. Study the arithmetic modulo `p`

I confirmed:

- `p` is prime.
- `p-1` factors completely into small prime powers.
- A primitive root exists and can be found quickly; in this instance `g = 0xe`.

This made full discrete logs practical.

### Step 3. Reduce the goal to forcing CRC = 0

Because the exponent is only 64 bits and `ord(a)` is overwhelmingly likely to exceed `2^64`, the only usable exponent multiple is zero:

```text
crc64.xz(PREFIX || inp || SUFFIX) = 0
```

### Step 4. Model the 8-byte correction block

I used an 8-byte controlled block because the CRC map on 64 input bits is full rank. For each possible suffix length `L` in `[100, 1000]`, I precomputed the 64x64 binary matrix describing:

```text
x -> crc64.xz(x || 0^L)
```

This matrix depends only on `L`, so we can brute-force the suffix length offline over just 901 possibilities.

### Step 5. Convert the service outputs into discrete logs

The exploit first sends one zero block and then 64 fixed 8-byte probes. For each response:

```text
pow_res = a^crc mod p
```

it computes:

```text
log_g(pow_res) = alpha * crc mod (p-1)
```

Then it subtracts the zero-block value to remove the unknown affine offset.

### Step 6. Solve the linear system modulo `(p-1)/2`

For each guessed suffix length:

1. Build the corresponding 64x64 CRC matrix.
2. Solve for the hidden coefficients `u_j`.
3. Divide each `u_j` by `2^j` modulo `(p-1)/2`.
4. Check whether the 64 normalized values collapse to exactly two opposites: `alpha` and `-alpha`.

Only the correct suffix length survives this test.

Once `alpha` is known modulo `(p-1)/2`, recover:

```text
c = D(0) * alpha^-1 mod ((p-1)/2)
```

Since `c` is a 64-bit number, this gives the exact CRC offset.

### Step 7. Recover the winning 8-byte block

Now solve:

```text
Mx = c
```

over `GF(2)` using the inverse of the CRC matrix. The resulting 8-byte block is the unique input that makes:

```text
crc64.xz(PREFIX || x || SUFFIX) = 0
```

Submitting that block makes:

```text
pow(a, 0, p) = 1
```

and the service prints the flag.

### Step 8. Solve the PoW

The service prints:

```text
SHA-256(prefix25 + xxxxxx) == digest
```

where the hidden suffix is actually 7 lowercase hex characters from the original `.hex()` string. I wrote a small OpenMP + OpenSSL helper in `pow_solver.c` to brute-force the `16^7` candidates quickly.

## Key Exploit Code

This is the exploit I used, from [`exploit.py`](./exploit.py):

```python
#!/usr/bin/env python3
import math
import os
import re
import socket
import subprocess
import sys
from collections import Counter

from fastcrc import crc64
from sympy.ntheory import discrete_log, primitive_root

HOST = "host8.dreamhack.games"
PORT = 13561

p = 0x273E0CCDD855BD09F6E7C6C03D5F966D0F
N = p - 1
MOD = N // 2
g = primitive_root(p)
QUERY_BASIS = [
    0x629F6FBED82C07CD, 0xE3E70682C2094CAC, 0x0A5D2F346BAA9455, 0xF728B4FA42485E3A,
    0x7C65C1E582E2E662, 0xEB1167B367A9C378, 0xD4713D60C8A70639, 0xF7C1BD874DA5E709,
    0x5BA91FAF7A024204, 0xE443DF789558867F, 0x37EBDCD9E87A1613, 0x23A7711A81332876,
    0x23C6612F48268673, 0x1846D424C17C6279, 0xCCA5A5A19E4D6E3C, 0xFCBD04C340212EF7,
    0x88561712E8E5216A, 0xB4862B21FB97D435, 0x9A164106CF6A659E, 0x259F4329E6F4590B,
    0x19488DEC4F65D4D9, 0x12E0C8B2BAD640FB, 0xD9B8A714E61A441C, 0x5487CE1EAF19922A,
    0x8F4FF31E78DE5857, 0x5A92118719C78DF4, 0x50F244556F25E2A2, 0xA3F2C9BF9C6316B9,
    0x3458A748E9BB17BC, 0x8D723104F77383C1, 0x71545A137A1D5006, 0x85776E9ADD84F39E,
    0x0FF18E0242AF9FC3, 0xEB2083E6CE164DBA, 0xEA7E9D498C778EA6, 0x17E0AA3C03983CA8,
    0xD71037D1B83E90EC, 0xB5D32B1666194CB1, 0xC8F8E3D0D3290A4C, 0xA0116BE5AB0C1681,
    0x9CA5499D004AE545, 0xD3FBF47A7E5B1E7F, 0x55485822DE1B372A, 0xBAF3897A3E70F16A,
    0xB421EAEB534097CA, 0x101FBCCCDED733E8, 0xEAC1C14F30E9C5CC, 0x38C1962E9148624F,
    0xCDA8056C3D15EEF7, 0x247A8333F7B0B7D2, 0x8B0163C1CD9D2B7D, 0x1759EDC372AE2244,
    0xFE43C49E149818D1, 0xE005B86051EF1922, 0xFF7B118E820865D6, 0x7D41E602EECE328B,
    0x4D2B9DEB1BEB3711, 0x4A84EB038D1FD9B7, 0x1FF39849B4E1357D, 0x552F233A8C25166A,
    0xEC188EFBD080E66E, 0x3405095C8A5006C1, 0xCCA74147F6BE1F72, 0x3DFABC08935DDD72,
]


def recv_until(sock: socket.socket, marker: bytes) -> bytes:
    data = b""
    while marker not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise EOFError(data.decode(errors="replace"))
        data += chunk
    return data


def send_line(sock: socket.socket, s: str) -> None:
    sock.sendall(s.encode() + b"\n")


def ensure_pow_solver() -> str:
    exe = os.path.join(os.path.dirname(__file__), "pow_solver")
    src = os.path.join(os.path.dirname(__file__), "pow_solver.c")
    if os.path.exists(exe):
        return exe
    cmd = ["gcc", "-O3", "-fopenmp", src, "-lcrypto", "-o", exe]
    subprocess.run(cmd, check=True)
    return exe


def solve_pow(prefix25: str, digest_hex: str) -> str:
    exe = ensure_pow_solver()
    res = subprocess.run([exe, prefix25, digest_hex], check=True, capture_output=True, text=True)
    return res.stdout.strip()


def solve_linear_mod(cols, rhs, mod):
    mat = [[(cols[i] >> j) & 1 for j in range(64)] + [rhs[i] % mod] for i in range(64)]
    row = 0
    for col in range(64):
        pivot = None
        for r in range(row, 64):
            if mat[r][col] % mod and math.gcd(mat[r][col], mod) == 1:
                pivot = r
                break
        if pivot is None:
            raise ValueError(f"no invertible pivot at column {col}")
        mat[row], mat[pivot] = mat[pivot], mat[row]
        inv = pow(mat[row][col], -1, mod)
        for j in range(col, 65):
            mat[row][j] = (mat[row][j] * inv) % mod
        for r in range(64):
            if r == row:
                continue
            factor = mat[r][col] % mod
            if factor:
                for j in range(col, 65):
                    mat[r][j] = (mat[r][j] - factor * mat[row][j]) % mod
        row += 1
    return [mat[i][64] % mod for i in range(64)]


def gf2_inv_cols(cols):
    rows = [0] * 64
    for col, v in enumerate(cols):
        for r in range(64):
            if (v >> r) & 1:
                rows[r] |= 1 << col
    aug = [[rows[r], 1 << r] for r in range(64)]
    rank = 0
    for c in range(64):
        pivot = None
        for r in range(rank, 64):
            if (aug[r][0] >> c) & 1:
                pivot = r
                break
        if pivot is None:
            raise ValueError("singular matrix")
        aug[rank], aug[pivot] = aug[pivot], aug[rank]
        for r in range(64):
            if r != rank and ((aug[r][0] >> c) & 1):
                aug[r][0] ^= aug[rank][0]
                aug[r][1] ^= aug[rank][1]
        rank += 1
    inv_cols = [0] * 64
    for row in range(64):
        for col in range(64):
            if (aug[row][1] >> col) & 1:
                inv_cols[col] |= 1 << row
    return inv_cols


def mat_vec_mul_cols(cols, vec):
    out = 0
    bit = 0
    x = vec
    while x:
        if x & 1:
            out ^= cols[bit]
        x >>= 1
        bit += 1
    return out


def precompute_tables():
    tables = {}
    for guess in range(100, 1001):
        zero_suffix = b"\0" * guess
        base = crc64.xz(b"\0" * 8 + zero_suffix)
        orig_cols = [crc64.xz((1 << i).to_bytes(8, "little") + zero_suffix) ^ base for i in range(64)]
        query_cols = [mat_vec_mul_cols(orig_cols, q) for q in QUERY_BASIS]
        tables[guess] = (query_cols, gf2_inv_cols(orig_cols))
    return tables


def recover_block(d0_mod, deltas, tables):
    for guess, (query_cols, inv_cols) in tables.items():
        try:
            u = solve_linear_mod(query_cols, deltas, MOD)
        except ValueError:
            continue
        vals = [(u[i] * pow(1 << i, -1, MOD)) % MOD for i in range(64)]
        cnt = Counter(vals)
        if len(cnt) != 2:
            continue
        (a1, _), (a2, _) = cnt.most_common(2)
        if (a1 + a2) % MOD != 0:
            continue
        for alpha in (a1, a2):
            if math.gcd(alpha, MOD) != 1:
                continue
            c = (d0_mod * pow(alpha, -1, MOD)) % MOD
            if c >= (1 << 64):
                continue
            if not all(v == ((-alpha) % MOD if ((c >> i) & 1) else alpha) for i, v in enumerate(vals)):
                continue
            block = mat_vec_mul_cols(inv_cols, c).to_bytes(8, "little")
            return guess, alpha, c, block
    raise RuntimeError("no valid suffix length / alpha found")


def query_pow(sock: socket.socket, payload_hex: str) -> int:
    send_line(sock, payload_hex)
    blob = recv_until(sock, b"> ").decode(errors="replace")
    line = blob.splitlines()[0].strip()
    if "flag" in blob.lower():
        print(blob, end="")
        sys.exit(0)
    if not line.startswith("0x"):
        raise RuntimeError(f"unexpected response: {line!r}")
    return int(line, 16)


def main():
    tables = precompute_tables()
    with socket.create_connection((HOST, PORT)) as sock:
        banner = recv_until(sock, b"Solve PoW: ").decode()
        first = banner.splitlines()[0].strip()
        m = re.fullmatch(r"SHA-256\(([0-9a-f]{25}) \+ xxxxxx\) == ([0-9a-f]{64})", first)
        if not m:
            raise RuntimeError(f"unexpected PoW line: {first!r}")
        prefix25, digest_hex = m.groups()
        solution = solve_pow(prefix25, digest_hex)
        send_line(sock, solution)
        recv_until(sock, b"> ")

        zero_hex = "00" * 8
        y0 = query_pow(sock, zero_hex)
        d0 = discrete_log(p, y0, g)

        deltas = []
        for q in QUERY_BASIS:
            payload = q.to_bytes(8, "little").hex()
            yi = query_pow(sock, payload)
            di = discrete_log(p, yi, g)
            deltas.append((di - d0) % MOD)

        guess, alpha, c, block = recover_block(d0 % MOD, deltas, tables)
        print(f"recovered suffix_length={guess} alpha_mod={(alpha % MOD)} c={hex(c)} block={block.hex()}", file=sys.stderr)
        send_line(sock, block.hex())
        tail = sock.recv(4096).decode(errors="replace")
        print(tail, end="")


if __name__ == "__main__":
    main()
```

## Remote Result

The final remote session recovered:

- Suffix length: `347`
- Hidden CRC offset: `0xbd4329faf549cc78`
- Winning 8-byte input: `f403dac8bb06c71f`

Submitting that block forced the CRC to zero and the service responded with:

```text
Here is the flag: DH{crcs_are_so_fun:4gZPbBgkBLLe5BiPRd4zBQ==}
```

## Flag

`DH{crcs_are_so_fun:4gZPbBgkBLLe5BiPRd4zBQ==}`
