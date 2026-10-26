package com.shirochi.npia.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.pipeline.JobPhase

class MainActivity : ComponentActivity() {
    private val model: AppViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val shared = intent?.takeIf { it.action == Intent.ACTION_SEND }?.getStringExtra(Intent.EXTRA_TEXT)
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                RootScreen(model, initialUrl = shared.orEmpty())
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun RootScreen(model: AppViewModel, initialUrl: String) {
    val jobs by model.jobs.collectAsStateWithLifecycle()
    var urlText by rememberSaveable { mutableStateOf(initialUrl) }

    Scaffold(topBar = { TopAppBar(title = { Text("Npia Mobile") }) }) { padding ->
        Column(Modifier.padding(padding).padding(16.dp).fillMaxSize(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedTextField(
                value = urlText,
                onValueChange = { urlText = it },
                label = { Text("Novel URL") },
                singleLine = true,
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                modifier = Modifier.fillMaxWidth(),
            )
            Button(
                onClick = { model.enqueue(urlText); urlText = "" },
                enabled = model.canDownload(urlText),
                modifier = Modifier.fillMaxWidth(),
            ) { Text("Download") }

            if (jobs.isEmpty()) {
                Text("No downloads yet", style = MaterialTheme.typography.bodyMedium)
            }
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(jobs, key = { it.id }) { job -> JobCard(job, onCancel = { model.cancel(job.id) }) }
            }
        }
    }
}

@Composable
private fun JobCard(job: JobItem, onCancel: () -> Unit) {
    val context = LocalContext.current
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(job.title ?: job.url, maxLines = 1, style = MaterialTheme.typography.titleSmall)
            Text(status(job), style = MaterialTheme.typography.bodySmall)
            job.links.forEach { link ->
                TextButton(onClick = {
                    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, link)
                    context.startActivity(Intent.createChooser(send, null))
                }) { Text(link, maxLines = 1) }
            }
            val active = job.phase !is JobPhase.Finished && job.phase !is JobPhase.Failed
            if (active) {
                Row { TextButton(onClick = onCancel) { Text("Cancel") } }
            }
        }
    }
}

private fun status(job: JobItem): String = when (val phase = job.phase) {
    JobPhase.Queued -> "Queued"
    JobPhase.FetchingBook -> "Reading book info"
    is JobPhase.FetchingChapters -> "Chapters ${phase.done}/${phase.total}"
    is JobPhase.DownloadingImages -> "Images ${phase.done}/${phase.total}"
    is JobPhase.Writing -> "Writing ${phase.format.raw.uppercase()}"
    is JobPhase.Uploading -> "Uploading ${phase.fileName}"
    JobPhase.Finished -> "Done: ${job.files.joinToString()}"
    is JobPhase.Failed -> phase.message
}
