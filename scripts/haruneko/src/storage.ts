// HaruNeko's StorageController keeps settings, title lists and bookmarks in IndexedDB. NovelDB reads
// one book at a time, so an in-memory store is enough (and leaves the site's own storage alone).
import type { StorageController, Store } from '@hn/engine/StorageController';

export class MemoryStorage implements StorageController {
    private readonly persistent = new Map<string, unknown>();
    private readonly temporary = new Map<string, unknown>();
    private next = 0;

    private static key(store: Store, key?: string) {
        return `${store}::${key ?? ''}`;
    }

    async SavePersistent<T>(value: T, store: Store, key?: string): Promise<void> {
        this.persistent.set(MemoryStorage.key(store, key), value);
    }

    async LoadPersistent<T>(store: Store, key?: string): Promise<T> {
        return this.persistent.get(MemoryStorage.key(store, key)) as T;
    }

    async RemovePersistent(store: Store, ...keys: string[]): Promise<void> {
        for (const key of keys) this.persistent.delete(MemoryStorage.key(store, key));
    }

    async SaveTemporary<T>(value: T): Promise<string> {
        const key = `tmp-${++this.next}`;
        this.temporary.set(key, value);
        return key;
    }

    async LoadTemporary<T>(key: string): Promise<T> {
        return this.temporary.get(key) as T;
    }

    async RemoveTemporary(...keys: string[]): Promise<void> {
        for (const key of keys) this.temporary.delete(key);
    }
}
