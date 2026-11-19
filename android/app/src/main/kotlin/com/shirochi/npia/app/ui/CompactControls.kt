package com.shirochi.npia.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.shirochi.npia.core.ChapterRangeText
import com.shirochi.npia.core.OutputFormat

// Dense controls for the Add tab, so the desktop download options fit on one screen.

/** A small label in front of a compact control. */
@Composable
fun MiniLabel(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** One dense row of controls. */
@Composable
fun CompactRow(content: @Composable RowScope.() -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        content = content,
    )
}

/**
 * Left-aligned groups of label + control that sit side by side and wrap onto a new line only
 * when the screen is too narrow.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FieldFlow(content: @Composable () -> Unit) {
    FlowRow(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 5.dp),
        horizontalArrangement = Arrangement.spacedBy(20.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

/** A label kept right next to its control(s). */
@Composable
fun FieldGroup(content: @Composable RowScope.() -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp), content = content)
}

/** Toggle chips that wrap onto more lines when needed. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ChipFlow(content: @Composable () -> Unit) {
    FlowRow(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) { content() }
}

@Composable
fun ToggleChip(label: String, selected: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    FilterChip(
        selected = selected,
        onClick = { onChange(!selected) },
        enabled = enabled,
        label = { Text(label, style = MaterialTheme.typography.labelMedium) },
        modifier = Modifier.height(32.dp),
        colors = FilterChipDefaults.filterChipColors(),
    )
}

/** Output formats as chips; at least one stays selected. */
@Composable
fun FormatChips(formats: List<OutputFormat>, onChange: (List<OutputFormat>) -> Unit) {
    ChipFlow {
        OutputFormat.entries.forEach { format ->
            ToggleChip(format.label, format in formats) { onChange(toggleFormat(formats, format)) }
        }
    }
}

/** `[−] value [+]` in a small bordered box. */
@Composable
fun MiniStepper(value: Int, range: IntRange, onChange: (Int) -> Unit) {
    Surface(shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            StepButton("−", value > range.first) { onChange((value - 1).coerceIn(range)) }
            Text("$value", Modifier.width(28.dp), textAlign = TextAlign.Center, style = MaterialTheme.typography.bodyMedium)
            StepButton("+", value < range.last) { onChange((value + 1).coerceIn(range)) }
        }
    }
}

@Composable
private fun StepButton(symbol: String, enabled: Boolean, onClick: () -> Unit) {
    Box(
        Modifier.size(32.dp).clickable(enabled = enabled, onClick = onClick),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            symbol,
            style = MaterialTheme.typography.titleMedium,
            color = if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f),
        )
    }
}

/**
 * One small box for a chapter range, typed like pages in a print dialog: `4` or `4-7`
 * (`ChapterRangeText`). `onChange` gets the range as it is typed; leaving the box tidies the text.
 */
@Composable
fun MiniRangeField(from: Int?, to: Int?, placeholder: String, width: Int = 84, enabled: Boolean = true, onChange: (Int?, Int?) -> Unit) {
    var text by remember { mutableStateOf(ChapterRangeText.text(from, to)) }
    var focused by remember { mutableStateOf(false) }
    // A range set elsewhere (another screen, an import) replaces the text; the user's own typing does not.
    LaunchedEffect(from, to) {
        if (ChapterRangeText.parse(text) != (from to to)) text = ChapterRangeText.text(from, to)
    }
    val focus = LocalFocusManager.current
    val alpha = if (enabled) 1f else 0.38f
    Surface(shape = RoundedCornerShape(8.dp), border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = alpha))) {
        Box(Modifier.width(width.dp).height(32.dp).padding(horizontal = 6.dp), contentAlignment = Alignment.Center) {
            if (text.isEmpty()) Text(placeholder, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = alpha), maxLines = 1)
            BasicTextField(
                value = text,
                onValueChange = { new ->
                    text = new
                    val (low, high) = ChapterRangeText.parse(new)
                    if (low != from || high != to) onChange(low, high)
                },
                enabled = enabled,
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface.copy(alpha = alpha), textAlign = TextAlign.Center),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                // The symbols keyboard has the dash a range needs on every phone.
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
                modifier = Modifier.fillMaxWidth().onFocusChanged { state ->
                    // An untouched box changes nothing (an open-ended range of older settings stays as it is).
                    if (focused && !state.isFocused && text != ChapterRangeText.text(from, to)) {
                        // "7-3" and "3-" keep their first number; show what was understood.
                        val (low, high) = ChapterRangeText.parse(text)
                        if (low != from || high != to) onChange(low, high)
                        text = ChapterRangeText.text(low, high)
                    }
                    focused = state.isFocused
                },
            )
        }
    }
}

/**
 * A small bordered number box. `text` is what the model holds ("" for none); `onChange` gets
 * the parsed value, or null when the box is empty.
 */
@Composable
fun MiniNumberField(value: String, placeholder: String, decimal: Boolean, width: Int = 56, error: Boolean = false, onChange: (String) -> Unit) {
    var text by remember(value) { mutableStateOf(value) }
    var focused by remember { mutableStateOf(false) }
    val focus = LocalFocusManager.current
    val outline = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.outline
    Surface(shape = RoundedCornerShape(8.dp), border = BorderStroke(if (error) 2.dp else 1.dp, outline)) {
        Box(Modifier.width(width.dp).height(32.dp).padding(horizontal = 6.dp), contentAlignment = Alignment.Center) {
            if (text.isEmpty()) Text(placeholder, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            BasicTextField(
                value = text,
                onValueChange = { new -> text = new; onChange(new) },
                singleLine = true,
                textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface, textAlign = TextAlign.Center),
                cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                keyboardOptions = KeyboardOptions(keyboardType = if (decimal) KeyboardType.Decimal else KeyboardType.Number, imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { focus.clearFocus() }),
                // Leaving the field (tapping elsewhere or Done) saves what it shows.
                modifier = Modifier.fillMaxWidth().onFocusChanged { state ->
                    if (focused && !state.isFocused) onChange(text)
                    focused = state.isFocused
                },
            )
        }
    }
}
