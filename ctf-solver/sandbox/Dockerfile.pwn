FROM ctf-base:latest

RUN apt-get update && apt-get install -y \
    gdb gdb-multiarch \
    checksec patchelf \
    ltrace strace \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    pwntools \
    angr \
    capstone \
    unicorn \
    ropgadget \
    pyghidra \
    z3-solver

# pwndbg
RUN git clone --depth=1 https://github.com/pwndbg/pwndbg /opt/pwndbg \
    && cd /opt/pwndbg && ./setup.sh

# ROPgadget is installed via pip (ropgadget)
# one_gadget
RUN gem install one_gadget
