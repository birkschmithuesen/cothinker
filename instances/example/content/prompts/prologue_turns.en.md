## premises
Your table's setting is settled. Draft five premises for the prologue now, per the method
in `{skill_path}`, section "Writing premises", with the input `{input_path}`. Write them to
`{premises_path}`. Reply afterwards with only: written

## dispatch
Write the prologue now. The method is in `{skill_path}`, the input in `{input_path}`.
Commission {drafts} drafts.{conditions} Reply afterwards with only: started

## pick
The drafts are ready: {draft_paths}. Choose per the method in `{skill_path}` and write the
result to `{scene_path}`. Reply afterwards with only: done

## fallback
No drafts are available. Work per the method in `{skill_path}`, with the input
`{input_path}`, and write to `{scene_path}`. Reply afterwards with only: done

## repair
The prologue in `{scene_path}` violates these rules: {reasons}. Work per the method in
`{skill_path}`. Reply afterwards with only: done

## revise
These changes are recommended for `{scene_path}`: {recommendations}. Work per the method in
`{skill_path}` and have the result written to `{revise_path}`. Reply afterwards with only:
started

## reasons
placeholder: There is a placeholder like P1 in the text. Replace it with the role's short
name.
name: There is a real name in the text. Replace it with the role's given name.
length: The narration does not have 6 to 22 sentences and 150 to 300 words. If it is too
long, cut it to at most 240 words; if it is too short, extend it to at least 150 words.
lines: There are not 4 to 20 dialogue lines from at least two roles, each at most 40 words.
If there are too many, cut to at most 16 lines; if a line is too long, shorten it to at
most 30 words; if there are too few, add lines until there are at least 8.
dialogue_words: The dialogue lines together have more than 200 words. Shorten individual
lines or drop one, until the total of all dialogue lines is at most 200 words.
speaker: A line does not start with a role's speaker label from the input ("given · short",
or the short name).
ending: The last line is not "…".
extra: There is something outside the form in the text (title, heading, parenthetical,
explanation).
language: The text is not written in the table's language.
links: A `link:` line pointing to a place that really is in the material is missing.
setting: The group's place or beginning from the setting is missing from the narration.
verbatim: The characters » « do not match `quote_mode`.
quote: The `quote:` line is not a sentence from the text, or longer than 160 characters.
method: Something from your instructions is in the text. Remove it.
story_copy: The text takes a clause verbatim from a role's self-introduction. The group
just heard that minutes ago -- rephrase it, do not retell the introduction.

## words
someone: someone
condition: Condition for draft {n}
