package com.shirochi.npia.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.OutputFormat

val ImageFormat.label: String
    get() = when (this) {
        ImageFormat.WEBP -> "WebP"
        ImageFormat.JPEG -> "JPEG"
        ImageFormat.PNG -> "PNG"
        ImageFormat.ORIGINAL -> "Keep original"
    }

@Composable
fun SectionHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 20.dp, bottom = 4.dp),
    )
}

@Composable
fun Footnote(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
    )
}

@Composable
fun SwitchRow(title: String, checked: Boolean, onChange: (Boolean) -> Unit, subtitle: String? = null, enabled: Boolean = true) {
    Row(
        Modifier.fillMaxWidth().clickable(enabled = enabled) { onChange(!checked) }.padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(title, color = if (enabled) Color.Unspecified else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f))
            subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        Switch(checked = checked, onCheckedChange = onChange, enabled = enabled)
    }
}

/** A labelled integer with minus/plus buttons (SwiftUI `Stepper`). */
@Composable
fun StepperRow(title: String, value: Int, range: IntRange, onChange: (Int) -> Unit, step: Int = 1, enabled: Boolean = true) {
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp, top = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f))
        IconButton(onClick = { onChange((value - step).coerceIn(range)) }, enabled = enabled && value > range.first) { Text("\u2212", style = MaterialTheme.typography.titleLarge) }
        Text("$value", Modifier.width(56.dp), style = MaterialTheme.typography.titleMedium, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        IconButton(onClick = { onChange((value + step).coerceIn(range)) }, enabled = enabled && value < range.last) { Text("+", style = MaterialTheme.typography.titleLarge) }
    }
}

/** Decimal input that keeps the user's text while it is being typed. */
@Composable
fun DecimalField(title: String, value: Double, onChange: (Double) -> Unit, enabled: Boolean = true) {
    var text by remember(value) { mutableStateOf(formatNumber(value)) }
    OutlinedTextField(
        value = text,
        onValueChange = { new ->
            text = new
            new.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 }?.let(onChange)
        },
        label = { Text(title) },
        singleLine = true,
        enabled = enabled,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
    )
}

@Composable
fun IntField(title: String, value: Int, onChange: (Int) -> Unit, enabled: Boolean = true) {
    var text by remember(value) { mutableStateOf(value.toString()) }
    OutlinedTextField(
        value = text,
        onValueChange = { new ->
            text = new
            new.trim().toIntOrNull()?.let(onChange)
        },
        label = { Text(title) },
        singleLine = true,
        enabled = enabled,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
    )
}

fun formatNumber(value: Double): String = if (value == Math.floor(value) && value < 1e9) value.toLong().toString() else value.toString()

/** An optional integer with an on/off switch (chapter ranges). */
@Composable
fun OptionalIntRow(title: String, value: Int?, defaultValue: Int, onChange: (Int?) -> Unit) {
    SwitchRow(title, value != null, { on -> onChange(if (on) value ?: defaultValue else null) })
    if (value != null) IntField(title, value, { onChange(it) })
}

@Composable
fun OptionalDecimalRow(title: String, value: Double?, defaultValue: Double, onChange: (Double?) -> Unit) {
    SwitchRow(title, value != null, { on -> onChange(if (on) value ?: defaultValue else null) })
    if (value != null) DecimalField(title, value, { onChange(it) })
}

/** Multi-select for output formats as wrapping chips; at least one stays selected. */
@Composable
fun FormatPicker(formats: List<OutputFormat>, onChange: (List<OutputFormat>) -> Unit) = FormatChips(formats, onChange)

fun toggleFormat(formats: List<OutputFormat>, format: OutputFormat): List<OutputFormat> = when {
    format !in formats -> OutputFormat.entries.filter { it in formats || it == format }
    formats.size > 1 -> formats - format
    else -> formats
}

/** A labelled dropdown (SwiftUI `Picker`). */
@Composable
fun <T> ChoiceRow(title: String, value: T, options: List<T>, label: (T) -> String, onChange: (T) -> Unit, enabled: Boolean = true) {
    var open by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(title, Modifier.weight(1f))
        Box {
            TextButton(onClick = { open = true }, enabled = enabled) { Text(label(value)) }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                options.forEach { option ->
                    DropdownMenuItem(text = { Text(label(option)) }, onClick = {
                        open = false
                        onChange(option)
                    })
                }
            }
        }
    }
}

/** A row that opens another screen. */
@Composable
fun NavRow(title: String, subtitle: String? = null, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 14.dp)) {
        Text(title)
        subtitle?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
    HorizontalDivider()
}

/** A per-source override: off follows the shared value, on edits a local copy. */
@Composable
fun <T> OverrideRow(title: String, value: T?, shared: T, describe: (T) -> String, onChange: (T?) -> Unit, editor: @Composable (T, (T) -> Unit) -> Unit) {
    SwitchRow(title, value != null, { on -> onChange(if (on) shared else null) }, subtitle = if (value == null) "Shared: ${describe(shared)}" else null)
    if (value != null) {
        Box(Modifier.padding(start = 16.dp)) { editor(value) { onChange(it) } }
    }
}
