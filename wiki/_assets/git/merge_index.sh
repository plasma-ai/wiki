#!/usr/bin/env bash
set -euo pipefail

# Custom git merge driver for _index.md files
# -------------------------------------------
#
# Splits the file at the *** separator. Above ***, the frontmatter is
# merged field-aware: the regenerated keys (name/updated) are normalized
# to "ours" on all three inputs -- wiki update owns them, so their churn
# must never conflict -- and the remaining authored keys
# (title/desc/created/category/tags/sources) get a normal three-way
# merge that may produce conflicts. The link block resolves to the union
# of both sides' rows: ours' layout wins and rows present only in theirs
# (with their desc continuations) are appended above the closing ***, so
# a merge never silently drops one side's additions -- the next wiki
# update re-sorts the block and prunes whatever rows went stale. title
# is authored (update never invents a value), so it must stay out of
# REGENERATED_KEYS -- normalizing it to ours would silently discard
# theirs' titles; the H1 rides ours' link-block layout, so a merged-in
# title shows in the H1 only after the post-merge wiki update. When the
# base carries no created: stamp (an add/add merge's empty base, a
# hand-written index) created joins the regenerated keys: both sides
# seed it from independent wiki update runs, so the stamps are churn,
# not authorship. A side whose frontmatter is undetectable
# (formatter-mangled or unclosed) is treated as unchanged from base,
# never as a deletion of the block; a side missing the *** separator
# entirely cannot be split into regions at all, so it surfaces a
# whole-file conflict with a repair hint. Everything below *** is manually
# written content, so it also gets a normal three-way merge, with an
# in-situ hint comment planted above add/add body conflicts.
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

# the driver's grammar is ASCII and the file's bytes are the user's: keep awk,
# grep, and sed byte-oriented, so a byte no UTF-8 decodes merges verbatim
# instead of aborting a multibyte-aware awk
export LC_ALL=C

# a literal carriage return for grep bracket expressions, where \r is the
# two characters backslash and r
CR=$'\r'

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
            # no pipeline: under pipefail, tail | head SIGPIPEs on files
            # larger than the pipe buffer once head exits (exit 141 race)
            sed -n "2,$((FM_END + 1))p" "$FILE" >>"$FM"
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
# a base without a created: stamp (an empty add/add base, a hand-written or
# imported index, a bare or quoted-empty created: key): both sides seed
# created: from their own wiki update runs, so the stamps are churn, not
# authorship -- normalize it too, in the canonical order, so a side gaining
# both stamps takes them as ours lays them out; a stamp is a plain token or a
# non-empty quoted scalar after the colon
grep -Eq "^created[[:blank:]]*:[[:blank:]]+([^[:space:]#\"']|\"[^\"$CR]|'[^'$CR])" \
    "$WORK/base_fm" || REGENERATED_KEYS=(name created updated)

# normalize the regenerated keys to ours' values on all three inputs, so the
# frontmatter merge below only ever sees authored-field differences; a value
# moves as its whole extent so a block-scalar or bare-key value never strands
# its body under the other side's one-liner (the value travels through the
# environment so awk never mangles a backslash in a name)
for KEY in "${REGENERATED_KEYS[@]}"; do
    # the extent is the key line plus the indented lines that continue its
    # value: every indented line under a block-scalar header (`|`/`>`, node
    # properties allowed before it), else the indented non-comment lines of
    # a plain value; a comment (indented or not) is authored, so it merges as
    # an ordinary line, and, like a blank run, belongs to the extent only
    # when a value line follows (a comment between a key and its indented
    # value must not strand the value under the other side's one-liner),
    # otherwise it separates the fields and stays where it is on every side,
    # byte for byte (a CRLF or whitespace-only separator included) -- mirrors
    # Python FIELD_EXTENT minus its trailing blanks, its column-0 sequence
    # items (a regenerated key carries a scalar), and its trailing comments;
    # the key match mirrors the Python `^key[ \t]*:(?:[ \t]|$)` anchor, so
    # `name :` is `name:` while a `name:core` typo is text, and a CRLF bare
    # key still matches; the extent travels through a file, as the
    # environment caps a value's size
    awk -v key="$KEY" '
        BEGIN {
            header = "^" key "[[:blank:]]*:[[:blank:]]*" \
                "([&!][^[:blank:]]*[[:blank:]]+)*[|>]"
        }
        found && /^[[:space:]]*$/ { pending = pending $0 "\n"; next }
        found && !block && /^[[:space:]]*#/ { pending = pending $0 "\n"; next }
        found && /^[[:space:]]/ {
            printf "%s", pending
            pending = ""
            print
            next
        }
        found { exit }
        $0 ~ "^" key "[[:blank:]]*:([[:blank:]\r]|$)" {
            block = ($0 ~ header)
            print
            found = 1
        }
    ' "$WORK/ours_fm" >"$WORK/extent"
    # 0/1 flags for awk -v, whose truth test is numeric -- any non-empty
    # string, "false" included, would read as true
    HAVE_EXTENT=0
    [[ -s "$WORK/extent" ]] && HAVE_EXTENT=1
    for SIDE in base theirs; do
        FM="$WORK/${SIDE}_fm"
        # an empty extent (ours dropped the key) drops it from the other
        # inputs too; the value lines of this side go, with the comments and
        # blank run a value line follows, while those trailing the value
        # stay; a side without the key (a base index written by hand, a side
        # that never ran wiki update) gains ours' extent where wiki update
        # puts the key -- name: under the opening fence, a stamp above the
        # closing one -- so the other side adding the key is no change to
        # merge; the extent path travels through the environment, as awk -v
        # would decode a backslash in it
        HAS_KEY=$(grep -Ec "^${KEY}[[:blank:]]*:([[:blank:]$CR]|$)" "$FM" || true)
        EXTENT="$WORK/extent" awk -v key="$KEY" -v have_extent="$HAVE_EXTENT" \
            -v has_key="$HAS_KEY" '
            BEGIN {
                header = "^" key "[[:blank:]]*:[[:blank:]]*" \
                    "([&!][^[:blank:]]*[[:blank:]]+)*[|>]"
            }
            skipping && /^[[:space:]]*$/ { pending = pending $0 "\n"; next }
            skipping && !block && /^[[:space:]]*#/ { pending = pending $0 "\n"; next }
            skipping && /^[[:space:]]/ {
                pending = ""
                next
            }
            skipping {
                printf "%s", pending
                pending = ""
                skipping = 0
            }
            NR == 1 && key == "name" && have_extent && !has_key {
                print
                while ((getline line < ENVIRON["EXTENT"]) > 0) print line
                close(ENVIRON["EXTENT"])
                next
            }
            NR > 1 && key != "name" && have_extent && !has_key && /^---[[:space:]\r]*$/ {
                while ((getline line < ENVIRON["EXTENT"]) > 0) print line
                close(ENVIRON["EXTENT"])
            }
            $0 ~ "^" key "[[:blank:]]*:([[:blank:]\r]|$)" {
                block = ($0 ~ header)
                while ((getline line < ENVIRON["EXTENT"]) > 0) print line
                close(ENVIRON["EXTENT"])
                skipping = 1
                next
            }
            { print }
            END { printf "%s", pending }
        ' "$FM" >"$FM.new"
        mv "$FM.new" "$FM"
    done
done

# ------ merge the three regions
# frontmatter: three-way merge of the authored keys (may produce conflicts)
MERGE_EXIT=0
git merge-file --marker-size="$MARKER_SIZE" -p -L ours -L base -L theirs \
    "$WORK/ours_fm" "$WORK/base_fm" "$WORK/theirs_fm" \
    >"$WORK/result_fm" || MERGE_EXIT=$?

# link block: union both sides' rows -- ours' layout wins, and each row
# present only in theirs rides over with its desc continuations, so the
# merge never silently drops one side's additions (the next wiki update
# re-sorts the block and prunes whatever rows went stale)
if grep -q '^\*\*\*[[:space:]]*$' "$WORK/ours_links"; then
    # rows key on their [[target| prefix (mirrors Python _LINK_ROW);
    # continuation and blank lines ride with the row that precedes them,
    # and a side's heading/preamble (before its first row) is never
    # collected -- ours' rows stream through in ours' layout; a row both
    # sides carry keeps ours' text unless ours left base's text alone while
    # theirs changed it -- an authored desc edit on a row wiki update does
    # not regenerate (an asset, a child still on the placeholder) -- and
    # then theirs' text lands, as a three-way merge would land it; a row
    # only theirs has rides over, appended directly above ours' closing ***
    # (the region ends at its first ***, so the anchor is unambiguous)
    awk '
        # a row block without its trailing blank lines (CRLF and
        # whitespace-only ones included): the text a side edited, whatever
        # separated the rows on that side
        function body(s) {
            while (s ~ /\r?\n[[:blank:]]*\r?\n$/) sub(/[[:blank:]]*\r?\n$/, "", s)
            return s
        }
        function flush() {
            if (row == "") return
            if (((2, row) in text) && ((1, row) in text) \
                && body(held) == body(text[1, row]) \
                && body(text[2, row]) != body(text[1, row]))
                printf "%s%s", body(text[2, row]), substr(held, length(body(held)) + 1)
            else
                printf "%s", held
            row = ""
            held = ""
        }
        # the parts are the input files in order: an empty base (add/add)
        # yields no record, so the file name, not the record count, says
        # which part a line belongs to
        { part = (FILENAME == ARGV[3]) ? 3 : (FILENAME == ARGV[2]) ? 2 : 1 }
        FNR == 1 { key = "" }
        part < 3 {
            if ($0 ~ /^\*\*\*[[:space:]]*$/) { key = ""; next }
            if ($0 ~ /^\[\[.*\|/) {
                key = $0
                sub(/\|.*$/, "", key)
                if (part == 2 && !((2, key) in text)) order[++count] = key
            }
            if (key != "") text[part, key] = text[part, key] $0 "\n"
            next
        }
        /^\*\*\*[[:space:]]*$/ && !done {
            flush()
            for (i = 1; i <= count; i++)
                if (!(order[i] in ours)) printf "%s", text[2, order[i]]
            done = 1
            print
            next
        }
        done { print; next }
        /^\[\[.*\|/ {
            flush()
            row = $0
            sub(/\|.*$/, "", row)
            ours[row] = 1
            held = $0 "\n"
            next
        }
        row != "" { held = held $0 "\n"; next }
        { print }
        END { flush() }
    ' "$WORK/base_links" "$WORK/theirs_links" "$WORK/ours_links" >"$WORK/result_links"
else
    # ours lost its closing *** (an empty above region): nothing anchors
    # an insertion, so take ours as-is
    cp "$WORK/ours_links" "$WORK/result_links"
fi

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
