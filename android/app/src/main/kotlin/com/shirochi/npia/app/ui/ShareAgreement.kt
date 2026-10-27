package com.shirochi.npia.app.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

const val SHARE_AGREEMENT_TITLE = "Before you share"

const val SHARE_AGREEMENT_BODY =
    "Upload creates a public link that anyone who has it can open. Only upload files you own or are authorised to share \u2014 " +
        "for example works you wrote, files you have permission to distribute, or public-domain works. " +
        "Sharing copyrighted books without permission may break the law and the source site's terms, " +
        "and you are responsible for what you upload."

const val SHARE_AGREEMENT_CONFIRM = "I confirm I'm authorised to share the files I upload."

/** Shown once, before the first upload; accepting is remembered and never asked again. */
@Composable
fun ShareAgreementDialog(onAgree: () -> Unit, onCancel: () -> Unit) {
    var confirmed by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text(SHARE_AGREEMENT_TITLE) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState())) {
                Text(SHARE_AGREEMENT_BODY)
                Row(
                    Modifier.fillMaxWidth().clickable { confirmed = !confirmed }.padding(top = 12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = confirmed, onCheckedChange = { confirmed = it })
                    Text(SHARE_AGREEMENT_CONFIRM, Modifier.padding(start = 4.dp))
                }
            }
        },
        confirmButton = { TextButton(onClick = onAgree, enabled = confirmed) { Text("Agree and upload") } },
        dismissButton = { TextButton(onClick = onCancel) { Text("Cancel") } },
    )
}
