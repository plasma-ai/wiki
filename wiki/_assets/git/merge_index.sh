#!/usr/bin/env bash
set -euo pipefail

# Custom git merge driver for _index.md files
# -------------------------------------------
#
# Splits the file at the *** separator. Above ***, the frontmatter is
# merged field-aware: the regenerated keys (name/updated) and the
# link block are normalized to "ours" on all three inputs -- wiki update
# owns them, so their churn must never conflict -- and the remaining
# authored keys (title/desc/created/category/tags/sources) get a normal
# three-way merge that may produce conflicts. title is authored (update
# never invents a value), so it must stay out of REGENERATED_KEYS --
# normalizing it to ours would silently discard theirs' titles; the H1
# rides the take-ours link block, so a merged-in title shows in the H1
# only after the post-merge wiki update. On add/add merges (empty
# base) created joins the regenerated keys: both sides seed it from
# independent wiki update runs, so the stamps are churn, not authorship.
# A side whose frontmatter is undetectable (formatter-mangled or
# unclosed) is treated as unchanged from base, never as a deletion of
# the block; a side missing the *** separator entirely cannot be split
# into regions at all, so it surfaces a whole-file conflict with a
# repair hint. Everything below *** is manually written content, so it
# also gets a normal three-way merge, with an in-situ hint comment
# planted above add/add body conflicts.
#
# Install via .gitattributes:
#   **/_index.md merge=wiki
#
# Configure in .git/config or .gitconfig (wiki init/config do this):
#   [merge "wiki"]
#     name = wiki merge (auto-resolve generated sections)
#     driver = wiki _merge %O %A %B %L %P
#
# The `wiki _merge` command dispatches _index.md pathnames below a
# declared wiki root here with the conflict-marker size as the fourth
# argument (default 7 standalone); an _index.md outside every wiki
# (e.g. a site generator's content page) is not tool-owned and takes
# git's default text merge instead.

OURS="$1"
BASE="$2"
THEIRS="$3"
MARKER_SIZE="${4:-7}"

# keys wiki update owns, normalized to ours before the frontmatter merge
REGENERATED_KEYS=(name updated)

split_at_separator() {
    local FILE="$1"
    local ABOVE="$2"
    local BELOW="$3"
    # skip a leading YAML frontmatter block (--- ... ---) before searching, so a
    # bare *** inside a multi-line block scalar (e.g. a desc) is not mistaken for
    # the links/content separator and split inside the frontmatter; tolerate a
    # UTF-8 BOM on the opening line (the Python parser does, and update
    # preserves it) -- a BOM-blind match would misread the frontmatter region
    local SCAN_FROM=1
    local FIRST
    FIRST="$(head -1 "$FILE")"
    FIRST="${FIRST#$'\xef\xbb\xbf'}"
    if [[ "$FIRST" =~ ^[[:space:]]*---[[:space:]]*$ ]]; then
        local FM_END
        FM_END=$(tail -n +2 "$FILE" | grep -n '^---[[:space:]]*$' \
            | head -1 | cut -d: -f1 || true)
        [[ -n "$FM_END" ]] && SCAN_FROM=$((FM_END + 2))
    fi
    # find the first *** line at/after the frontmatter (separator between links
    # and content; mirrors Python parse_index); user content below may contain
    # additional *** thematic breaks
    local SEP_LINE
    SEP_LINE=$(tail -n +"$SCAN_FROM" "$FILE" | grep -n '^\*\*\*[[:space:]]*$' \
        | head -1 | cut -d: -f1 || true)
    if [[ -z "$SEP_LINE" ]]; then
        # no separator -- treat the whole file as content (below) so it gets a
        # full three-way merge rather than silently taking ours and dropping theirs
        : >"$ABOVE"
        cp "$FILE" "$BELOW"
    else
        # SEP_LINE is relative to SCAN_FROM -- convert to an absolute line number
        local ABS_SEP=$((SCAN_FROM + SEP_LINE - 1))
        head -n "$ABS_SEP" "$FILE" >"$ABOVE"
        tail -n +"$((ABS_SEP + 1))" "$FILE" >"$BELOW"
    fi
}

split_frontmatter() {
    local FILE="$1"
    local FM="$2"
    local LINKS="$3"
    # the frontmatter is a leading --- ... --- block (mirrors Python
    # extract_frontmatter, BOM tolerance included -- a BOM'd side must not
    # read as frontmatter-less and lose its authored keys); the link block
    # (H1 and generated links, up to and including ***) is everything after it
    local FIRST
    FIRST="$(head -1 "$FILE")"
    FIRST="${FIRST#$'\xef\xbb\xbf'}"
    if [[ "$FIRST" =~ ^[[:space:]]*---[[:space:]]*$ ]]; then
        local FM_END
        FM_END=$(tail -n +2 "$FILE" | grep -n '^---[[:space:]]*$' \
            | head -1 | cut -d: -f1 || true)
        if [[ -n "$FM_END" ]]; then
            # rebuild the opener from the BOM-stripped line -- the merged
            # output carries exactly one clean block, no BOM residue
            printf '%s\n' "$FIRST" >"$FM"
            tail -n +2 "$FILE" | head -n "$FM_END" >>"$FM"
            tail -n +"$((FM_END + 2))" "$FILE" >"$LINKS"
            return
        fi
    fi
    # no (closed) frontmatter -- the whole region is link block
    : >"$FM"
    cp "$FILE" "$LINKS"
}

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# ------ split inputs at the *** separator
split_at_separator "$OURS" "$WORK/ours_above" "$WORK/ours_below"
split_at_separator "$BASE" "$WORK/base_above" "$WORK/base_below"
split_at_separator "$THEIRS" "$WORK/theirs_above" "$WORK/theirs_below"

# ------ whole-file conflict on a lost separator
# a side that lost the *** separator itself (formatter damage: mdformat
# rewrites *** to ---) has no region boundary, so its generated bytes and
# authored edits are indistinguishable -- refuse to guess: emit a whole-file
# conflict (merging against an empty base concedes only the lines both sides
# share) with a repair hint planted above the first marker
for SIDE in ours theirs; do
    if [[ -s "$WORK/base_above" && ! -s "$WORK/${SIDE}_above" ]] \
        && [[ -s "$WORK/${SIDE}_below" ]]; then
        git merge-file --marker-size="$MARKER_SIZE" -p -L ours -L base -L theirs \
            "$OURS" /dev/null "$THEIRS" >"$WORK/result_conflict" || true
        HINT='<!-- index *** separator missing on one side: likely'
        HINT+=' formatter damage; restore the *** line (wiki update'
        HINT+=' repairs it), redo the merge, and delete this line when'
        HINT+=' resolving -->'
        MARKER=$(printf '%*s' "$MARKER_SIZE" '' | tr ' ' '<')
        # the hint and marker travel through the environment so awk
        # never mangles them
        HINT="$HINT" MARKER="$MARKER" awk '
            !done && index($0, ENVIRON["MARKER"]) == 1 {
                print ENVIRON["HINT"]
                done = 1
            }
            { print }
        ' "$WORK/result_conflict" >"$OURS"
        exit 1
    fi
done

# ------ split and guard frontmatter
split_frontmatter "$WORK/ours_above" "$WORK/ours_fm" "$WORK/ours_links"
split_frontmatter "$WORK/base_above" "$WORK/base_fm" "$WORK/base_links"
split_frontmatter "$WORK/theirs_above" "$WORK/theirs_fm" "$WORK/theirs_links"

# a side with no detectable frontmatter (formatter-mangled or unclosed) must
# not read as a deletion of the whole block -- treat its whole above-***
# region as unchanged from base, so the other side's frontmatter survives
# and the side's residual frontmatter bytes never leak through its links
for SIDE in ours theirs; do
    if [[ ! -s "$WORK/${SIDE}_fm" && -s "$WORK/base_fm" ]]; then
        cp "$WORK/base_fm" "$WORK/${SIDE}_fm"
        cp "$WORK/base_links" "$WORK/${SIDE}_links"
    fi
done

# ------ normalize regenerated keys
# add/add (empty base file): both sides seed created: from their own wiki
# update runs, so the stamps are churn, not authorship -- normalize it too
[[ ! -s "$BASE" ]] && REGENERATED_KEYS+=(created)

# normalize the regenerated keys to ours' lines on all three inputs, so the
# frontmatter merge below only ever sees authored-field differences (the value
# travels through the environment so awk never mangles a backslash in a name)
for KEY in "${REGENERATED_KEYS[@]}"; do
    OURS_LINE=$(grep -m1 "^${KEY}:" "$WORK/ours_fm" || true)
    for SIDE in base theirs; do
        FM="$WORK/${SIDE}_fm"
        if [[ -n "$OURS_LINE" ]]; then
            OURS_LINE="$OURS_LINE" awk -v key="$KEY" \
                'index($0, key ":") == 1 { print ENVIRON["OURS_LINE"]; next } { print }' \
                "$FM" >"$FM.new"
        else
            # ours dropped the key -- drop it from the other inputs too
            grep -v "^${KEY}:" "$FM" >"$FM.new" || true
        fi
        mv "$FM.new" "$FM"
    done
done

# ------ merge the three regions
# frontmatter: three-way merge of the authored keys (may produce conflicts)
MERGE_EXIT=0
git merge-file --marker-size="$MARKER_SIZE" -p -L ours -L base -L theirs \
    "$WORK/ours_fm" "$WORK/base_fm" "$WORK/theirs_fm" \
    >"$WORK/result_fm" || MERGE_EXIT=$?

# link block: take ours (regenerated by wiki update)
cp "$WORK/ours_links" "$WORK/result_links"

# below ***: three-way merge (may produce conflict markers)
BELOW_EXIT=0
git merge-file --marker-size="$MARKER_SIZE" -p -L ours -L base -L theirs \
    "$WORK/ours_below" "$WORK/base_below" "$WORK/theirs_below" \
    >"$WORK/result_below" || BELOW_EXIT=$?
[[ "$BELOW_EXIT" -ne 0 ]] && MERGE_EXIT=$BELOW_EXIT

# an add/add body conflict (empty base) is sibling branches authoring the
# same new directory's index concurrently: plant the convention hint above
# the markers (removed on hand-resolution; renders invisibly if left behind)
if [[ "$BELOW_EXIT" -ne 0 && ! -s "$BASE" ]]; then
    HINT='<!-- add/add index conflict: sibling branches authored this'
    HINT+=' body concurrently; leave new-directory index bodies empty'
    HINT+=' until after the merge wave, and delete this line when'
    HINT+=' resolving -->'
    MARKER=$(printf '%*s' "$MARKER_SIZE" '' | tr ' ' '<')
    # the hint and marker travel through the environment so awk
    # never mangles them
    HINT="$HINT" MARKER="$MARKER" awk '
        !done && index($0, ENVIRON["MARKER"]) == 1 {
            print ENVIRON["HINT"]
            done = 1
        }
        { print }
    ' "$WORK/result_below" >"$WORK/result_below.new"
    mv "$WORK/result_below.new" "$WORK/result_below"
fi

# ------ recombine
cat "$WORK/result_fm" "$WORK/result_links" "$WORK/result_below" >"$OURS"

exit "$MERGE_EXIT"
