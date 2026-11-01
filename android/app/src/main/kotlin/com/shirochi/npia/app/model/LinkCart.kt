package com.shirochi.npia.app.model

import com.shirochi.npia.sources.SourceCatalog
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import java.io.File

/** A book picked from the catalog website, waiting on the Add tab. */
@Serializable
data class CartItem(val url: String, val title: String = "", val source: String = "")

/** Links collected from the Catalog tab, like a shopping cart; the Add tab downloads them. */
class LinkCart(file: File) {
    private val store = JsonFileStore(file, ListSerializer(CartItem.serializer()))
    private val _items = MutableStateFlow(store.load().orEmpty())
    val items: StateFlow<List<CartItem>> = _items.asStateFlow()

    fun add(item: CartItem) = change { list ->
        val book = canonical(item)
        if (list.any { it.url == book.url }) list else list + book
    }

    /** Adds several books with one save, skipping links already in the cart (and repeats within `items`). */
    fun addAll(items: List<CartItem>) = change { list ->
        val known = list.mapTo(HashSet()) { it.url }
        list + items.map(::canonical).filter { known.add(it.url) }
    }

    /** A phone-site link and its desktop twin are one book. */
    private fun canonical(item: CartItem) = item.copy(url = SourceCatalog.canonicalUrl(item.url))

    /** Removes `url`, also when the cart keeps it in its canonical form. */
    fun remove(url: String) = change { list ->
        val book = SourceCatalog.canonicalUrl(url)
        list.filterNot { it.url == url || it.url == book }
    }

    /** True when `url` is in the cart, as given or in its canonical form. */
    fun contains(items: List<CartItem>, url: String): Boolean {
        val book = SourceCatalog.canonicalUrl(url)
        return items.any { it.url == url || it.url == book }
    }

    fun removeAll(urls: Collection<String>) = change { list -> list.filterNot { it.url in urls } }

    fun clear() = change { emptyList() }

    private fun change(transform: (List<CartItem>) -> List<CartItem>) {
        _items.update(transform)
        store.save(_items.value)
    }
}
