# linkedin-post-drafter

A Telegram bot that sends you LinkedIn post drafts written by [Claude Code](https://claude.com/claude-code), and learns your writing style from every change you ask for.

Twice a week (or whenever you send `/new`), Claude Code runs headless, picks a recent topic in your field, checks every fact against two sources, writes a draft, runs an anti-slop editing pass and sends it to your phone. You approve it, ask for changes or discard it. Nothing is ever published for you.

## Why it is built this way

- **Claude Code does the writing, a small script does the plumbing.** `drafter.py` is ~400 lines of standard-library Python. It polls Telegram, keeps a little state and calls `claude -p` with a skill. All the judgement (topic choice, research, writing, editing, learning) lives in the skill, in plain Markdown you can read and change.
- **It learns from your edits.** Feedback that says something general ("shorter", "never open with a question") becomes a one-line rule in `content/style.md`. If you tweak the text before publishing, paste the final version and Claude diffs it against the draft to learn more rules. Those rules override the default style guide.
- **Anti-slop pass on every draft.** Before sending, Claude rereads the draft against explicit rule files (announcing sentences, binary contrasts, filler adverbs, lists of three, em dashes...), scores it on five axes and rewrites anything below 42/50. English rules are vendored from [hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop) (MIT); Spanish rules are an adaptation.
- **Least privilege.** The unattended Claude run only gets `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebSearch`, `WebFetch` and the single shell command `python3 drafter.py send`. The bot only reacts to your own chat id.

## How it works

```
launchd / cron (every minute)
        |
        v
  drafter.py poll ──── Telegram getUpdates (long poll)
        |                     ^
        |  draft day? /new?   |  buttons: Approve / Edit / Discard / I published it
        v                     |
  claude -p "Use the post-drafter skill in mode NEW|REVISE|LEARN"
        |
        |  reads content/profile.md + content/style.md
        |  researches, writes content/drafts/<date>-<slug>.md
        |  anti-slop pass
        v
  python3 drafter.py send <draft>  ──>  your phone
```

| You do | The bot does |
|---|---|
| Nothing (draft day) | Writes and sends a new draft, rotating through your languages |
| `/new [topic]` | Writes a draft now, optionally on that topic |
| **Approve** | Sends back the clean text, ready to long-press and copy into LinkedIn |
| **Edit**, or reply to a draft | Rewrites it with your feedback and records any general rule in `content/style.md` |
| **I published it** + paste final text | Diffs it against the draft and learns from your changes |
| Any other message | Saves it as an idea in `content/ideas.md` for a future post |
| `/pending` | Lists drafts not published yet |

## Setup

Requirements: Python 3.9+, [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in, a Telegram account. macOS or Linux (it uses `fcntl` file locks).

```bash
git clone https://github.com/cavadasA/linkedin-post-drafter.git
cd linkedin-post-drafter
cp examples/profile.example.md content/profile.md   # who you are, topics, guardrails
cp examples/style.example.md content/style.md
cp .env.example .env                                  # schedule and languages
python3 drafter.py setup                              # bot token from @BotFather + your chat id
```

`setup` asks for the token without echoing it, waits for you to press START in the bot chat, and writes `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` to `.env` (mode 600).

Then run `python3 drafter.py poll` every minute:

- **macOS**: edit the paths in `examples/com.example.post-drafter.plist`, copy it to `~/Library/LaunchAgents/` and run `launchctl load ~/Library/LaunchAgents/com.example.post-drafter.plist`.
- **Linux**: `* * * * * cd /path/to/linkedin-post-drafter && python3 drafter.py poll >> bot.log 2>&1` in `crontab -e`.

The poller's environment must find `claude` on `PATH`.

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `DRAFT_DAYS` | `mon,thu` | Weekdays for automatic drafts |
| `DRAFT_TIME` | `08:30` | Local time from which the day's draft is generated |
| `DRAFT_LANGS` | `en` | Languages to rotate through, e.g. `es,en` |

Your voice lives in `content/profile.md` (topics, topic types, guardrails such as "never mention my employer") and `content/style.md` (rules learned from your edits). Everything under `content/` except `.gitkeep` is git-ignored, so your drafts and profile stay local.

## Tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT. The vendored stop-slop rules keep their own MIT license in `.claude/skills/post-drafter/references/stop-slop/LICENSE`.
