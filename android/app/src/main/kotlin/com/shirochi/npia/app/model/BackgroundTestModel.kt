package com.shirochi.npia.app.model

/** What a background test found, in words for the Settings screen and its notification. */
data class BackgroundTestResult(val passed: Boolean, val text: String) {
    companion object {
        /** The process ended while a test ran: found at the next start from the test's saved heartbeat. */
        fun stopped(ranMs: Long, totalMs: Long): BackgroundTestResult = BackgroundTestResult(
            false,
            "Failed: Android stopped NovelDB ${BackgroundTestMeter.span(ranMs)} into a ${BackgroundTestMeter.span(totalMs)} test. " +
                "Turn off battery optimization for NovelDB, then test again.",
        )
    }
}

/**
 * Measures one background test. `tick` is called about once a second with a clock that keeps
 * counting while the phone sleeps; a long time between two ticks means Android paused the app.
 * Time and internet checks count only while the app is not on screen.
 */
class BackgroundTestMeter(private val totalMs: Long, start: Long) {
    private var last = start
    var backgroundMs = 0L
        private set
    var longestPauseMs = 0L
        private set
    var checks = 0
        private set
    var failedChecks = 0
        private set

    fun tick(now: Long, inBackground: Boolean) {
        val gap = (now - last).coerceAtLeast(0)
        last = now
        if (!inBackground) return
        backgroundMs += gap
        if (gap > longestPauseMs) longestPauseMs = gap
    }

    /** One internet check made while the app was in the background. */
    fun check(answered: Boolean) {
        checks += 1
        if (!answered) failedChecks += 1
    }

    fun result(): BackgroundTestResult {
        val checked = if (checks == 0) "" else ", ${checks - failedChecks} of $checks internet checks answered"
        return when {
            backgroundMs < minOf(totalMs / 2, MIN_BACKGROUND_MS) -> BackgroundTestResult(
                false,
                "Nothing tested: NovelDB was on screen for most of the test. Start it again, then leave the app or lock the screen until it ends.",
            )
            longestPauseMs > PAUSE_MS -> BackgroundTestResult(
                false,
                "Failed: Android paused NovelDB for ${span(longestPauseMs)} while it was in the background. " +
                    "Turn off battery optimization for NovelDB, then test again.",
            )
            failedChecks > 0 -> BackgroundTestResult(
                false,
                "Failed: NovelDB kept running for ${span(backgroundMs)} in the background, but $failedChecks of $checks internet checks got no answer. " +
                    "Android may be cutting its connection (battery or data saver), or the connection dropped.",
            )
            else -> BackgroundTestResult(
                true,
                "Passed: NovelDB kept running for ${span(backgroundMs)} in the background (longest pause ${span(longestPauseMs)}$checked).",
            )
        }
    }

    companion object {
        /** A gap between ticks longer than this is a pause; ticks are a second apart. */
        const val PAUSE_MS = 10_000L
        private const val MIN_BACKGROUND_MS = 60_000L

        /** "45 s", "2 min", "2 min 5 s". */
        fun span(ms: Long): String {
            val seconds = (ms + 500) / 1000
            val minutes = seconds / 60
            return when {
                minutes == 0L -> "$seconds s"
                seconds % 60 == 0L -> "$minutes min"
                else -> "$minutes min ${seconds % 60} s"
            }
        }
    }
}
