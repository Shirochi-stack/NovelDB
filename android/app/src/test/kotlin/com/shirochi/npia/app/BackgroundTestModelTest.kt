package com.shirochi.npia.app

import com.shirochi.npia.app.model.BackgroundTestMeter
import com.shirochi.npia.app.model.BackgroundTestResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackgroundTestModelTest {
    private fun run(totalMs: Long, ticks: List<Pair<Long, Boolean>>, checks: List<Boolean> = emptyList()): BackgroundTestResult {
        val meter = BackgroundTestMeter(totalMs, 0)
        ticks.forEach { (now, background) -> meter.tick(now, background) }
        checks.forEach(meter::check)
        return meter.result()
    }

    @Test
    fun steadyTicksInTheBackgroundPass() {
        val result = run(120_000, (1..120).map { it * 1000L to true }, listOf(true, true, true))
        assertTrue(result.text, result.passed)
        assertEquals("Passed: NovelDB kept running for 2 min in the background (longest pause 1 s, 3 of 3 internet checks answered).", result.text)
    }

    @Test
    fun aLongGapBetweenTicksIsAPause() {
        // Frozen from 0:30 to 1:45: the next tick arrives 75 seconds late.
        val ticks = (1..30).map { it * 1000L to true } + (105_000L to true) + (106..120).map { it * 1000L to true }
        val result = run(120_000, ticks)
        assertFalse(result.passed)
        assertTrue(result.text, result.text.startsWith("Failed: Android paused NovelDB for 1 min 15 s"))
    }

    @Test
    fun aTestWatchedOnScreenTestsNothing() {
        val result = run(120_000, (1..120).map { it * 1000L to (it > 100) })
        assertFalse(result.passed)
        assertTrue(result.text, result.text.startsWith("Nothing tested"))
    }

    @Test
    fun timeOnScreenIsNotCountedAsAPause() {
        // On screen for the first 20 seconds with one slow frame there; the rest is in the background.
        val ticks = listOf(15_000L to false, 20_000L to false) + (21..120).map { it * 1000L to true }
        val result = run(120_000, ticks)
        assertTrue(result.text, result.passed)
        assertTrue(result.text, result.text.contains("1 min 40 s in the background"))
    }

    @Test
    fun unansweredInternetChecksFail() {
        val result = run(120_000, (1..120).map { it * 1000L to true }, listOf(true, false, false))
        assertFalse(result.passed)
        assertTrue(result.text, result.text.contains("2 of 3 internet checks got no answer"))
    }

    @Test
    fun aKilledTestSaysHowFarItGot() {
        val result = BackgroundTestResult.stopped(42_000, 900_000)
        assertFalse(result.passed)
        assertTrue(result.text, result.text.startsWith("Failed: Android stopped NovelDB 42 s into a 15 min test."))
    }
}
