"""
Fragmentation with XOR parity, so a backup survives one storage provider
being unavailable.

With n providers the protected data is split into n-1 equal-size data
fragments plus one parity fragment (the XOR of all data fragments). Any
n-1 of the n fragments are enough to rebuild the original: a missing data
fragment is the XOR of the parity fragment and the remaining data
fragments. n=2 degenerates to a full copy on each provider, since the
parity of a single fragment is that fragment itself.

Operates on already-encrypted bytes, so every fragment (parity included)
is ciphertext-derived. Nothing here touches keys or plaintext.
"""


class InsufficientFragmentsError(Exception):
    """Raised when too many fragments are missing to rebuild the original."""


def _xor(a: bytes, b: bytes) -> bytes:
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big")


def split_with_parity(data: bytes, n: int) -> tuple[list[bytes], dict]:
    """
    Split data into n fragments: n-1 data fragments followed by one parity
    fragment. Returns (fragments, metadata); the metadata is needed later
    to reconstruct and should be stored with the file version.
    """
    if n < 2:
        raise ValueError("Need at least 2 fragments to tolerate a failure.")

    data_count = n - 1
    fragment_size = max(1, -(-len(data) // data_count))
    padded = data.ljust(fragment_size * data_count, b"\x00")
    data_fragments = [
        padded[i * fragment_size : (i + 1) * fragment_size] for i in range(data_count)
    ]

    parity = data_fragments[0]
    for fragment in data_fragments[1:]:
        parity = _xor(parity, fragment)

    metadata = {
        "scheme": "xor-parity",
        "fragment_count": n,
        "fragment_size": fragment_size,
        "original_length": len(data),
    }
    return data_fragments + [parity], metadata


def reconstruct(fragments: dict[int, bytes], metadata: dict) -> bytes:
    """
    Rebuild the original data from the fragments that are available, keyed
    by fragment index (data fragments first, parity last). At most one of
    the n fragments may be missing.
    """
    n = metadata["fragment_count"]
    size = metadata["fragment_size"]
    parity_index = n - 1

    for index, fragment in fragments.items():
        if not 0 <= index < n:
            raise ValueError(f"Fragment index {index} is out of range for {n} fragments.")
        if len(fragment) != size:
            raise ValueError(f"Fragment {index} is {len(fragment)} bytes, expected {size}.")

    missing = [i for i in range(n) if i not in fragments]
    if len(missing) > 1:
        raise InsufficientFragmentsError(
            f"Need at least {n - 1} of {n} fragments, have {n - len(missing)}."
        )

    data_fragments = {i: fragments[i] for i in range(parity_index) if i in fragments}
    if missing and missing[0] != parity_index:
        recovered = fragments[parity_index]
        for fragment in data_fragments.values():
            recovered = _xor(recovered, fragment)
        data_fragments[missing[0]] = recovered

    joined = b"".join(data_fragments[i] for i in range(parity_index))
    return joined[: metadata["original_length"]]
