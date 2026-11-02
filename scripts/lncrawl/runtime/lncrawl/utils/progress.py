"""A silent stand-in for tqdm: the crawler core's progress bars have no terminal here."""


class tqdm:
    def __init__(self, iterable=None, total=None, disable=True, desc="", unit="item", **_kwargs):
        self.iterable = iterable
        self.total = total
        self.disable = True
        self.n = 0

    def __iter__(self):
        for item in self.iterable or ():
            self.n += 1
            yield item

    def update(self, n=1):
        self.n += n

    def set_description(self, *_args, **_kwargs):
        pass

    def set_postfix(self, *_args, **_kwargs):
        pass

    def refresh(self, *_args, **_kwargs):
        pass

    def clear(self, *_args, **_kwargs):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
