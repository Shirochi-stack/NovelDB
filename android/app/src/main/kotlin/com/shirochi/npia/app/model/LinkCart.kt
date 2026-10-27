package com.shirochi.npia.app.model

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

    fun add(item: CartItem) = change { list -> if (list.any { it.url == item.url }) list else list + item }

    /** Adds several books with one save, skipping links already in the cart (and repeats within `items`). */
    fun addAll(items: List<CartItem>) = change { list ->
        val known = list.mapTo(HashSet()) { it.url }
        list + items.filter { known.add(it.url) }
    }

    fun remove(url: String) = change { list -> list.filterNot { it.url == url } }

    fun removeAll(urls: Collection<String>) = change { list -> list.filterNot { it.url in urls } }

    fun clear() = change { emptyList() }

    private fun change(transform: (List<CartItem>) -> List<CartItem>) {
        _items.update(transform)
        store.save(_items.value)
    }
}
