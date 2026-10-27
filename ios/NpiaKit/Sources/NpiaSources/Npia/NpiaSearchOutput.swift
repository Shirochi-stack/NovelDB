import Foundation

/// How the tag dialog turns its inputs into a search, ported from `gui.py` (`_add_group`,
/// `do_retrieve`).
public enum NpiaSearchPlan {
    /// "a, b,, c" becomes ["a", "b", "c"].
    public static func customTags(_ text: String) -> [String] {
        text.split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }

    /// The ticked grid tags in grid order, then the custom tags: what "Add Selected as Group" collects.
    public static func selection(selected: [String], custom: String) -> [String] {
        let chosen = Set(selected)
        return NpiaSearchTerms.commonTags.map(\.value).filter { chosen.contains($0) } + customTags(custom)
    }

    /// The groups a search runs. With no groups defined, every selected or custom tag becomes a
    /// group of its own, so all of them are AND'd. Empty when there is nothing to search.
    public static func groupsToSearch(selected: [String], custom: String, groups: [[String]]) -> [[String]] {
        if !groups.isEmpty { return groups }
        return selection(selected: selected, custom: custom).map { [$0] }
    }
}

/// File names, file contents, and summaries of a search result, in the desktop's formats.
public enum NpiaSearchOutput {
    public static let top100FileName = "top100_rankings.txt"

    /// The part of `tag_<label>.txt` after `tag_`: "all_novels" for the full catalog, otherwise
    /// the groups joined with "+", tags inside a group with "_OR_", and Korean tags replaced by
    /// their English grid name (spaces become underscores).
    public static func tagLabel(groups: [[String]]?) -> String {
        guard let groups else { return "all_novels" }
        return groups.map { group in
            group.map { tag in
                tag.allSatisfy(\.isASCII)
                    ? tag
                    : (NpiaSearchTerms.commonTag(for: tag)?.english ?? tag).replacingOccurrences(of: " ", with: "_")
            }.joined(separator: "_OR_")
        }.joined(separator: "+")
    }

    /// `tag_<label>.txt`, with characters a file name cannot hold replaced by "-".
    public static func tagFileName(groups: [[String]]?) -> String {
        "tag_" + sanitized(tagLabel(groups: groups)) + ".txt"
    }

    /// One id per line.
    public static func idListText(_ ids: [String]) -> String {
        ids.map { $0 + "\n" }.joined()
    }

    /// Every ranking as `# <Label> Top 100`, its ids, and a blank line.
    public static func top100Text(_ rankings: [NpiaRanking]) -> String {
        var text = ""
        for ranking in rankings {
            text += "# \(ranking.label.capitalized) Top 100\n"
            for id in ranking.ids { text += id + "\n" }
            text += "\n"
        }
        return text
    }

    /// Every id of every ranking once, in first-seen order.
    public static func uniqueIDs(_ rankings: [NpiaRanking]) -> [String] {
        NpiaSearchTerms.unique(rankings.flatMap(\.ids))
    }

    /// "Found N novel(s)." with " (stopped)" when the run was stopped.
    public static func idSummary(count: Int, stopped: Bool) -> String {
        "Found \(count) novel(s)." + (stopped ? " (stopped)" : "")
    }

    /// "Top 100: N unique novels", then one line per audience, then "(stopped)" if stopped.
    public static func top100Summary(_ rankings: [NpiaRanking], stopped: Bool) -> String {
        var lines = ["Top 100: \(uniqueIDs(rankings).count) unique novels"]
        var audiences: [String] = []
        var periods: [String: [String]] = [:]
        for ranking in rankings {
            let parts = ranking.label.split(separator: " ").map(String.init)
            let audience = parts.count > 1 ? parts[parts.count - 1] : "all"
            let period = parts.first ?? ranking.label
            if periods[audience] == nil { audiences.append(audience) }
            periods[audience, default: []].append("\(period): \(ranking.ids.count)")
        }
        for audience in audiences {
            lines.append("  \(audience): \((periods[audience] ?? []).joined(separator: ", "))")
        }
        if stopped { lines.append("(stopped)") }
        return lines.joined(separator: "\n")
    }

    private static func sanitized(_ name: String) -> String {
        String(name.map { "/\:*?\"<>|".contains($0) ? "-" : $0 })
    }
}
