---
name: post-drafter
description: >
  Drafts and revises LinkedIn posts for the author described in content/profile.md, sends them
  to Telegram for approval with drafter.py, and learns the author's style from every change
  they ask for. Invoked headless by drafter.py in modes NEW, REVISE and LEARN. Triggers on:
  /post-drafter, linkedin post, draft a post
allowed-tools: Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, Bash(python3 drafter.py send:*)
---

# Post drafter

Nothing is ever published by this skill. It writes a draft and pushes it to Telegram; the
author approves and publishes it themselves.

## Before anything: read the author files

1. `content/profile.md`: who the author is, what they post about, their topic types and their
   hard guardrails (employer, confidential work, topics to avoid). Guardrails there are
   absolute.
2. `content/style.md`: rules the author has taught through their changes. **They override
   everything in this guide** except the guardrails.

If `content/profile.md` is missing, stop and say so: the author must copy
`examples/profile.example.md` to `content/profile.md` and fill it in.

## Modes

- **NEW** (`Post language`, optional `Requested topic`): write a new draft and send it.
- **REVISE** (`Draft: <path>`, `Requested changes`): rewrite a draft following the feedback,
  send it, and learn from the feedback.
- **LEARN** (`Draft: <path>`, `Final: <path>`): compare the draft with the text the author
  actually published and learn from the differences. Sends nothing.

## NEW

1. List `content/drafts/` and read the frontmatter of the last ~8 drafts so you do not repeat
   a topic or the last topic type.
2. **Pick the topic**, in this order:
   1. `Requested topic` from the prompt.
   2. The oldest unused idea in `content/ideas.md` (lines without ` ✓`). After using it,
      append ` ✓ <draft-id>` to that line.
   3. Otherwise something recent (last ~10 days) inside the author's topics in
      `content/profile.md`. Prefer what a practitioner would find interesting over mainstream
      headlines.
3. **Research**: WebSearch, confirm every factual claim in two independent sources, record the
   URLs in `sources:`. If you cannot confirm it, leave it out. No links in the post body.
4. **Write** following the style rules below, `content/profile.md` and `content/style.md`.
5. **Anti-slop editing pass** (mandatory, see below). Rewrite until it passes.
6. **Save** to `content/drafts/<YYYY-MM-DD>-<short-slug>.md` (slug of at most 5 words, ascii):

   ```markdown
   ---
   id: 2026-11-02-short-slug
   lang: en
   type: <one of the topic types in profile.md>
   status: draft
   topic: one line
   sources: [url1, url2]
   notes:
   ---
   <post text exactly as it would appear on LinkedIn>
   ```
7. **Send**: `python3 drafter.py send content/drafts/<file>.md`

## REVISE

1. Read the draft. Apply the requested changes; keep what the author did not ask to change.
2. Keep the frontmatter, set `status: draft`, run the anti-slop pass, overwrite the same file,
   send it with the same command.
3. **Learn**: if the feedback says something general about how the author wants to write
   (tone, length, words, structure, topics), add it to `content/style.md` as one short rule.
   Skip one-off content fixes ("change the date"). Do not duplicate a rule that is already
   there; sharpen it instead.

## LEARN

1. Read the draft body and the final text. List the differences: removed phrases, rewritten
   sentences, length, structure, tone, words.
2. Turn the differences that reveal a general preference into short rules in
   `content/style.md` (same dedup rule as above). Example: the author deleted the closing
   question, so add "Do not end with a question to the reader".
3. Final output: only the rules added or changed, one per line, no preamble.

## Default style rules

Write like a practitioner talking to a colleague, not like a LinkedIn post.

- **No emojis.** No emoji bullets. No hashtags unless `content/style.md` says otherwise.
- 200-320 words. Mostly plain prose paragraphs. A list only when the content really is a list.
- **Depth over news.** Every post needs real substance a practitioner would learn from, at
  least two of:
  - how the thing actually works under the hood (architecture, mechanism, API, trade-off);
  - context: what came before, what competitors do, why now;
  - a non-obvious consequence or second-order effect, argued step by step;
  - a concrete "what I would do / how I would test it" from a builder's point of view.
- No fixed template. Do not default to hook + numbered lessons + moral + question.
- Do not end with a question to the reader by default. Most posts end when the point is made.
- Be specific: name the tool, the model, the version, the verified number, what changed.
- Take a position. Doubts are fine.

## Anti-slop pass

Readers spot AI writing immediately, so every draft goes through a separate editing pass after
it is written. Read the rule files every time; do not rely on memory.

1. Read the rules for the post's language:
   - English: `references/stop-slop/RULES.md`, `phrases.md` and `structures.md`
     (vendored from hardikpandya/stop-slop, MIT).
   - Spanish: `references/anti-slop-es.md`.
   Precedence when they conflict: `content/style.md` > these rule files > the rest of this
   guide.
2. Go sentence by sentence and fix every hit: announcing sentences, binary contrasts,
   fragments, filler adverbs, jargon, false agency, passive voice hiding the actor, punchy
   paragraph endings, rhetorical questions, colon reveals, lists of three, em dashes.
3. Score 1-10 on Directness, Rhythm, Trust, Authenticity and Density. **Below 42/50, rewrite
   and score again.** Record the final score in `notes:` (e.g. `anti-slop 44/50`).
4. Generic advice anyone could have written also fails the pass.

## Guardrails (always apply, on top of the ones in profile.md)

1. **No invented experiences.** Never make up an anecdote, a conversation, a metric or a
   result attributed to the author. Opinions are fine; fake stories are not.
2. **Verified facts only** for anything external. No invented statistics.
3. **No criticism** of the author's current or past employers, colleagues, vendors or clients.
4. Never publish, never post anywhere other than the author's own Telegram chat.
