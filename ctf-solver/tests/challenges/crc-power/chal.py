from fastcrc import crc64

import os
import random

PREFIX = os.urandom(random.randint(100, 1000))
SUFFIX = os.urandom(random.randint(100, 1000))
p = 0x273e0ccdd855bd09f6e7c6c03d5f966d0f
BITS = p.bit_length()

def PoW():
    import hashlib
    DIFFICULTY = 7
    message = os.urandom(16).hex()
    res = hashlib.sha256(message.encode()).hexdigest()

    print(f"SHA-256({message[:-DIFFICULTY]} + xxxxxx) == {res}")
    inp = input("Solve PoW: ")
    assert len(inp) == DIFFICULTY

    if hashlib.sha256( (message[:-DIFFICULTY] + inp).encode() ).hexdigest() == res:
        return
    
    print("Wrong PoW")
    exit(1)

def read_flag():
    with open('./flag', 'r') as f:
        print(f"Here is the flag: {f.read().strip()}")

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


if __name__ == "__main__":
    PoW()
    main()