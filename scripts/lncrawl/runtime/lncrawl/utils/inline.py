"""An executor that runs each task as it is submitted, on the caller's thread.

The crawler core queues requests on a thread pool. NovelDB's transport can fall back to the browser,
which only answers on the thread that drives it, so the core's pool runs its tasks in place instead
(build-time swap in scripts/update_lncrawl.py). The app's own download settings decide concurrency.
"""

from concurrent.futures import Executor, Future


class InlineExecutor(Executor):
    def __init__(self, max_workers=None, thread_name_prefix="", *args, **kwargs):
        self._max_workers = max_workers or 1
        self._shutdown = False

    def submit(self, fn, /, *args, **kwargs):
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        future = Future()
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(fn(*args, **kwargs))
            except BaseException as e:
                future.set_exception(e)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        self._shutdown = True
