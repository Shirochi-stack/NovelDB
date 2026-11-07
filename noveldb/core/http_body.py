"""Read a response incrementally when the caller can pause or cancel."""


def read_body(response, checkpoint):
    received = bytearray()
    with response:
        chunks = response.iter_content(chunk_size=128 * 1024)
        while checkpoint():
            chunk = next(chunks, None)
            if chunk is None:
                return bytes(received)
            received.extend(chunk)
    return None
