package com.shirochi.npia.sources.npia

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NpiaSearchOutputTest {
    /** The tag the site spells with its own name; found by its picker label so no name is written here. */
    private val siteTag = NpiaCommonTags.ALL.first { it.label == "npia" }.tag

    @Test
    fun commonTagsListTheDesktopPicker() {
        assertEquals(44, NpiaCommonTags.ALL.size)
        assertEquals(NpiaCommonTags.ALL.size, NpiaCommonTags.ALL.map { it.tag }.distinct().size)
        assertEquals("TS (Genderbend)", NpiaCommonTags.ALL.first().display)
        assertEquals("약피폐 (Moderate Suffering)", NpiaCommonTags.ALL[1].display)
        // The site's own tag is searched by its real name but shown by its codename.
        assertEquals("npia (Npia)", NpiaCommonTags.ALL.first { it.tag == siteTag }.display)
        assertTrue(siteTag.all { it.code > 127 })
        assertEquals("Npia", NpiaCommonTags.englishName(siteTag))
        assertEquals("Pure Love", NpiaCommonTags.englishName("순애"))
        assertNull(NpiaCommonTags.englishName("unknown tag"))
    }

    @Test
    fun customTagsAreSplitOnCommasAndTrimmed() {
        assertEquals(listOf("a", "b c", "d"), NpiaSearchOutput.customTags(" a, b c ,, d ,"))
        assertEquals(emptyList<String>(), NpiaSearchOutput.customTags("  , "))
        assertEquals(listOf("TS", "x"), NpiaSearchOutput.selectionTags(listOf("TS"), "x, TS"))
    }

    @Test
    fun groupsToSearchFollowTheDesktopRules() {
        // Defined groups are used as they are, whatever is selected.
        val groups = listOf(listOf("TS"), listOf("a", "b"))
        assertEquals(groups, NpiaSearchOutput.groupsToSearch(listOf("백합"), "x", groups))
        // Without groups every selected or custom tag is a group of its own (all AND'd).
        assertEquals(listOf(listOf("백합"), listOf("x"), listOf("y")), NpiaSearchOutput.groupsToSearch(listOf("백합"), "x, y", emptyList()))
        // Nothing selected, nothing to search.
        assertNull(NpiaSearchOutput.groupsToSearch(emptyList(), " ", emptyList()))
    }

    @Test
    fun theAdultTagIsDroppedWhenTheAgeFilterIsSet() {
        val selected = listOf("TS", NpiaCommonTags.ADULT_TAG, "백합")
        assertEquals(selected, NpiaSearchOutput.selectedForAge(selected, ""))
        assertEquals(listOf("TS", "백합"), NpiaSearchOutput.selectedForAge(selected, "15"))
        assertEquals(listOf("TS", "백합"), NpiaSearchOutput.selectedForAge(selected, "19"))
    }

    @Test
    fun fileLabelsNameTheGroupsAndTranslateKnownTags() {
        assertEquals("all_novels", NpiaSearchOutput.tagLabel(null))
        assertEquals("tag_all_novels.txt", NpiaSearchOutput.tagFileName(NpiaSearchOutput.tagLabel(null)))
        // Groups joined with "+", tags in a group with "_OR_"; known Korean tags by English name, unknown ones as they are.
        assertEquals(
            "TS+Moderate_Suffering_OR_피폐",
            NpiaSearchOutput.tagLabel(listOf(listOf("TS"), listOf("약피폐", "피폐"))),
        )
        assertEquals("Slice_of_Life+My Tag", NpiaSearchOutput.tagLabel(listOf(listOf("일상"), listOf("My Tag"))))
        assertEquals("Npia", NpiaSearchOutput.tagLabel(listOf(listOf(siteTag))))
        // An English name with "/" cannot be a file name.
        assertEquals("Hyundai/Modern", NpiaSearchOutput.tagLabel(listOf(listOf("현대"))))
        assertEquals("tag_Hyundai_Modern.txt", NpiaSearchOutput.tagFileName("Hyundai/Modern"))
        assertEquals("tag_a_b_c_d_e_f_g_h.txt", NpiaSearchOutput.tagFileName("a\\b:c*d?e\"f<g>h"))
    }

    @Test
    fun idListsHaveOneIdPerLine() {
        assertEquals("1\n2\n3\n", NpiaSearchOutput.idListText(listOf("1", "2", "3")))
        assertEquals("", NpiaSearchOutput.idListText(emptyList()))
    }

    @Test
    fun top100FileHasATitledSectionPerRanking() {
        val rankings = linkedMapOf<String, List<String>>("daily all" to listOf("5", "3"), "weekly adult" to emptyList(), "monthly teen" to listOf("9"))
        assertEquals(
            "# Daily All Top 100\n5\n3\n\n# Weekly Adult Top 100\n\n# Monthly Teen Top 100\n9\n\n",
            NpiaSearchOutput.top100Text(rankings),
        )
        assertEquals("Daily All", NpiaSearchOutput.titleCase("daily all"))
        assertEquals("It'S Ok", NpiaSearchOutput.titleCase("it's ok"))
        assertEquals("A1B", NpiaSearchOutput.titleCase("a1B"))
        assertEquals("top100_rankings.txt", NpiaSearchOutput.TOP100_FILE_NAME)
    }

    @Test
    fun summariesMatchTheDesktopDialog() {
        assertEquals("Found 12 novel(s).", NpiaSearchOutput.tagSummary(12, stopped = false))
        assertEquals("Found 3 novel(s). (stopped)", NpiaSearchOutput.tagSummary(3, stopped = true))

        val all = (1..100).map { it.toString() }
        val rankings = linkedMapOf<String, List<String>>(
            "daily all" to all, "weekly all" to all, "monthly all" to all.take(50),
            "daily adult" to (90..189).map { it.toString() }, "weekly adult" to emptyList(), "monthly adult" to listOf("1"),
        )
        assertEquals(
            "Top 100: 189 unique novels\n" +
                "  all: daily: 100, weekly: 100, monthly: 50\n" +
                "  adult: daily: 100, weekly: 0, monthly: 1",
            NpiaSearchOutput.top100Summary(rankings, stopped = false),
        )
        assertTrue(NpiaSearchOutput.top100Summary(rankings, stopped = true).endsWith("\n(stopped)"))
        assertEquals("Top 100: 0 unique novels", NpiaSearchOutput.top100Summary(emptyMap(), stopped = false))
    }

    @Test
    fun cartLinksPointAtTheNovelPage() {
        assertEquals("https://${NpiaParser.HOST}/novel/419368", NpiaSearchOutput.novelUrl("419368"))
        assertEquals("419368", NpiaParser.novelId(NpiaSearchOutput.novelUrl("419368")))
    }
}
