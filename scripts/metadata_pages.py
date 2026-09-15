"""Bounded catalog prefetch; consumers alone mutate durable checkpoints."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED


def catalog_pages(adapter, client, partitions, cursors, args, log):
    # Imported lazily to keep direct-script and package entrypoints equivalent.
    try:
        from .metadata_common import BudgetExceeded, CatalogPage
    except ImportError:
        from metadata_common import BudgetExceeded, CatalogPage
    jobs = deque()
    used = {}
    for partition in partitions:
        key = partition['key']
        cursor = cursors.setdefault(key, {'next_page': partition['start_page'], 'complete': False})
        mode = partition.get('pagination')
        if mode and cursor.get('pagination') != mode:
            cursor.clear()
            cursor.update(next_page=partition['start_page'], complete=False, pagination=mode, cursor_point='')
        if cursor.get('complete'):
            continue
        overlap_for = cursor['next_page']
        overlap = not mode and args.resume and cursor.get('overlap_checked_for') != overlap_for
        page = max(partition['start_page'], overlap_for - int(bool(overlap)))
        jobs.append({'partition': partition, 'cursor': cursor, 'next': page, 'expected': page,
                     'overlap': overlap, 'overlap_for': overlap_for, 'signatures': set(), 'pending': {}, 'stopped': False})

    def fetch(partition, page):
        try:
            return adapter.fetch_page(client, partition, page)
        except BudgetExceeded as error:
            return error
        except Exception as error:
            return CatalogPage([], None, False, type(error).__name__ + ': catalog request failed')

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while jobs:
            # Reserve budgets before submission; completed-but-unconsumed pages
            # also occupy slots, keeping the prefetch/reorder buffer bounded.
            occupied = sum(len(j['pending']) for j in jobs)
            stalled = 0
            while occupied < args.workers and stalled < len(jobs):
                job = jobs[0]
                jobs.rotate(-1)
                part = job['partition']
                tier = part['tier']
                if (job['stopped'] or (part.get('pagination') and job['pending'])
                        or (args.max_pages is not None and used.get(tier, 0) >= args.max_pages)):
                    stalled += 1
                    continue
                request = dict(part)
                if part.get('pagination'):
                    request['cursor_point'] = job['cursor'].get('cursor_point', '')
                page = job['next']
                job['pending'][page] = pool.submit(fetch, request, page)
                job['next'] += 1
                used[tier] = used.get(tier, 0) + 1
                occupied += 1
                stalled = 0
            ready = [j for j in jobs if j['expected'] in j['pending'] and j['pending'][j['expected']].done()]
            if not ready:
                active = [f for j in jobs for f in j['pending'].values() if not f.done()]
                if not active:
                    break
                log(f"Catalog: {len(active)} requests in flight; {occupied}/{args.workers} prefetch slots")
                wait(active, timeout=10, return_when=FIRST_COMPLETED)
                continue
            for job in ready:
                page = job['expected']
                result = job['pending'].pop(page).result()
                if isinstance(result, BudgetExceeded):
                    raise result
                if not isinstance(result, CatalogPage):
                    result = CatalogPage([], None, False, 'Malformed catalog page')
                yield job, page, result
                cursor = job['cursor']
                if cursor.get('complete') or cursor.get('error'):
                    job['stopped'] = True
                next_page = cursor['next_page']
                if job['stopped'] or next_page != page + 1:
                    for future in job['pending'].values():
                        future.cancel()
                    # Drain already-started requests before reusing their slots.
                    wait(list(job['pending'].values())) if job['pending'] else None
                    job['pending'].clear()
                    job['next'] = next_page
                job['expected'] = next_page
            jobs = deque(j for j in jobs if not j['stopped'])
