---
name: echo
description: "Count the words in a block of text and report the number, nothing else."
---

# Echo

## What this is about
Given a piece of text, count its words by splitting on whitespace and report the count.
Nothing is written to, or read from, anywhere other than this module's own state file.

## Steps
1. Split the text on whitespace.
2. Count the resulting pieces.
3. Report `{"words": <count>}`.
