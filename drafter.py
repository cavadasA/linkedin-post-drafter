#!/usr/bin/env python3
"""Telegram bot that sends you LinkedIn post drafts written by Claude Code.

Writing and revising posts is done by Claude Code, run headless (`claude -p`)
with the `post-drafter` skill. This script only moves drafts between the
`content/` folder and your Telegram chat, and decides when to ask for one.

Flow:
  - On the configured weekdays the poller asks Claude for a new draft. The
    skill researches a topic, writes the draft, runs an anti-slop editing
    pass and calls `send` to push it to Telegram with buttons.
  - Approve: the bot returns the clean text, ready to paste into LinkedIn.
  - Edit: your next message is feedback; Claude rewrites the draft and turns
    general feedback into a rule in content/style.md.
  - Replying to a draft message also counts as feedback for that draft.
  - After "I published it", paste the final text and Claude diffs it against
    the draft to learn more rules (mode LEARN).
  - Any other text is saved as a post idea in content/ideas.md.

Nothing is ever published automatically. Only messages from TELEGRAM_CHAT_ID
are processed; everything else is ignored.

Usage:
  python3 drafter.py setup            # store bot token and chat id in .env
  python3 drafter.py poll             # one polling cycle (run it every minute)
  python3 drafter.py send DRAFT.md    # push one draft (called by the skill)
  python3 drafter.py notify TEXT      # plain message to your chat
"""

import argparse
import datetime as dt
import fcntl
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parent
ENV_FILE = REPO / ".env"
BASE = REPO / "content"
DRAFTS = BASE / "drafts"
PUBLISHED = BASE / "published"
IDEAS = BASE / "ideas.md"
STATE = BASE / ".state.json"
LOCK = BASE / ".state.lock"

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
LONG_POLL_SECONDS = 50
CLAUDE_TIMEOUT = 15 * 60
# Claude Code runs unattended, so it only gets the tools the skill needs.
ALLOWED_TOOLS = ",".join([
    "Skill", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
    "Bash(python3 drafter.py send:*)",
])

HELP = (
    "Hi, I am your LinkedIn drafting bot.\n\n"
    "- On your schedule I send you a draft.\n"
    "- Approve: I send back the clean text to paste into LinkedIn.\n"
    "- Edit: tell me what to change and I send a new version. I learn from every change.\n"
    "- Replying to a draft also counts as a change.\n"
    "- If you tweak the text before publishing, paste the final version and I learn from it.\n"
    "- Any other message is saved as an idea for a future post.\n"
    "- /new [topic]: a draft right now.\n"
    "- /pending: drafts not published yet."
)


def log(msg):
    print(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# --- .env -----------------------------------------------------------------

def read_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def write_env(updates):
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in updates:
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, 0o600)


def config():
    env = read_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env; run `setup` first.")
    return token, int(chat)


def schedule(env=None):
    """Weekdays, time and languages for automatic drafts, from .env."""
    env = read_env() if env is None else env
    days = {WEEKDAYS[d.strip().lower()[:3]]
            for d in env.get("DRAFT_DAYS", "mon,thu").split(",") if d.strip()}
    hour, minute = (int(x) for x in env.get("DRAFT_TIME", "08:30").split(":"))
    langs = [x.strip() for x in env.get("DRAFT_LANGS", "en").split(",") if x.strip()]
    return days, dt.time(hour, minute), langs or ["en"]


def next_lang(langs, last):
    """Rotate through the configured languages."""
    if last not in langs:
        return langs[0]
    return langs[(langs.index(last) + 1) % len(langs)]


# --- state ----------------------------------------------------------------

@contextmanager
def state():
    """Load-modify-save under a file lock. Never hold it across `claude -p`:
    the child calls `send`, which writes state too."""
    BASE.mkdir(exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(STATE.read_text()) if STATE.exists() else {}
        data.setdefault("offset", 0)
        data.setdefault("messages", {})  # telegram message_id -> draft path
        yield data
        STATE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# --- telegram -------------------------------------------------------------

def api(token, method, http_timeout=30, **params):
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as e:
        body = json.loads(e.read() or b"{}")
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method}: {body.get('description', body)}")
    return body["result"]


def say(token, chat, text, **extra):
    return api(token, "sendMessage", chat_id=chat, text=text[:4096], **extra)


# --- drafts ---------------------------------------------------------------

def parse_draft(path):
    """Split a draft into its frontmatter (dict) and body (str)."""
    text = Path(path).read_text()
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = m.group(2)
    return meta, body.strip()


def set_status(path, status):
    path = Path(path)
    text = path.read_text()
    if re.search(r"^status:.*$", text, re.M):
        text = re.sub(r"^status:.*$", f"status: {status}", text, count=1, flags=re.M)
    path.write_text(text)


def rel(path):
    return str(Path(path).resolve().relative_to(REPO))


# --- claude ---------------------------------------------------------------

def run_claude(token, chat, prompt):
    claude = shutil.which("claude")
    if not claude:
        say(token, chat, "Claude Code (`claude`) is not on PATH for the poller.")
        return None
    log(f"claude -p: {prompt[:120]!r}")
    try:
        out = subprocess.run(
            [claude, "-p", prompt, "--allowedTools", ALLOWED_TOOLS],
            cwd=REPO, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
        log(f"claude exit {out.returncode}: {out.stdout[-1500:]}{out.stderr[-1500:]}")
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"claude failed: {e}")
    say(token, chat, "I could not finish that. Details in bot.log.")
    return None


def generate(token, chat, topic=None):
    _, _, langs = schedule()
    with state() as s:
        lang = next_lang(langs, s.get("last_lang"))
        s["last_lang"] = lang
    say(token, chat, f"Writing a new draft ({lang.upper()})...")
    prompt = (
        "Use the post-drafter skill in mode NEW. "
        f"Post language: {lang}. "
        + (f"Requested topic: «{topic}». " if topic else "")
        + "When the draft is saved, send it with `python3 drafter.py send <path>`."
    )
    run_claude(token, chat, prompt)


def revise(token, chat, draft, feedback):
    say(token, chat, "Rewriting with your changes...")
    prompt = (
        "Use the post-drafter skill in mode REVISE. "
        f"Draft: {draft}. "
        f"Requested changes: «{feedback}». "
        "Save the new version in the same file, send it with "
        "`python3 drafter.py send <path>` and record in content/style.md "
        "what you learn from this change."
    )
    run_claude(token, chat, prompt)


def learn(token, chat, draft, final_text):
    PUBLISHED.mkdir(exist_ok=True)
    final = PUBLISHED / Path(draft).name
    final.write_text(final_text + "\n")
    prompt = (
        "Use the post-drafter skill in mode LEARN. "
        f"Draft: {draft}. Final: {rel(final)}."
    )
    rules = run_claude(token, chat, prompt)
    if rules:
        say(token, chat, f"Learned:\n{rules}")


# --- commands -------------------------------------------------------------

def cmd_send(args):
    token, chat = config()
    meta, body = parse_draft(args.draft)
    kind = meta.get("type", "")
    head = f"DRAFT ({meta.get('lang', '?').upper()})" + (f" - {kind}" if kind else "")
    keyboard = {"inline_keyboard": [[
        {"text": "Approve", "callback_data": "approve"},
        {"text": "Edit", "callback_data": "edit"},
        {"text": "Discard", "callback_data": "discard"},
    ]]}
    msg = say(token, chat, f"{head}\n\n{body}", reply_markup=keyboard)
    with state() as s:
        s["messages"][str(msg["message_id"])] = rel(args.draft)
    log(f"sent {rel(args.draft)} as message {msg['message_id']}")


def cmd_notify(args):
    token, chat = config()
    say(token, chat, args.text)


def handle_callback(token, chat, cq):
    msg_id = str(cq["message"]["message_id"])
    with state() as s:
        draft = s["messages"].get(msg_id)
    if not draft or not (REPO / draft).exists():
        api(token, "answerCallbackQuery", callback_query_id=cq["id"], text="That draft no longer exists")
        return None
    action = cq["data"]
    api(token, "answerCallbackQuery", callback_query_id=cq["id"])
    api(token, "editMessageReplyMarkup", chat_id=chat, message_id=int(msg_id))
    if action == "published":
        set_status(REPO / draft, "published")
        with state() as s:
            s["awaiting_final"] = draft
        say(token, chat, "Marked as published. If you changed anything before publishing, "
                         "paste the final text here and I will learn from it. Otherwise ignore this.")
    elif action == "approve":
        set_status(REPO / draft, "approved")
        _, body = parse_draft(REPO / draft)
        say(token, chat, "Approved. Long-press the next message to copy it into LinkedIn:")
        copy = say(token, chat, body, reply_markup={"inline_keyboard": [[
            {"text": "I published it", "callback_data": "published"}]]})
        with state() as s:
            s["messages"][str(copy["message_id"])] = draft
    elif action == "discard":
        set_status(REPO / draft, "discarded")
        say(token, chat, "Discarded.")
    elif action == "edit":
        with state() as s:
            s["awaiting_feedback"] = draft
        say(token, chat, "What should I change? Write it in your next message.")
    return None


def handle_message(token, chat, msg):
    """Route one incoming text. Returns a job tuple for slow Claude work, or None."""
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    if text in ("/start", "/help"):
        say(token, chat, HELP)
        return None
    if text.startswith("/new"):
        return ("generate", text[len("/new"):].strip() or None)
    if text.startswith("/pending"):
        rows = []
        for p in sorted(DRAFTS.glob("*.md")):
            meta, _ = parse_draft(p)
            if meta.get("status") in ("draft", "approved"):
                rows.append(f"• {p.stem} — {meta['status']}")
        say(token, chat, "\n".join(rows) or "No pending drafts.")
        return None
    reply_to = str((msg.get("reply_to_message") or {}).get("message_id", ""))
    with state() as s:
        draft = s["messages"].get(reply_to) or s.pop("awaiting_feedback", None)
        final_for = s.pop("awaiting_final", None)
    if draft:
        return ("revise", draft, text)
    if final_for and len(text) >= 200:  # a pasted post, not a stray idea
        return ("learn", final_for, text)
    with open(IDEAS, "a") as f:
        f.write(f"- [{dt.date.today()}] {text}\n")
    say(token, chat, "Idea saved. I will use it in an upcoming draft (or ask with /new).")
    return None


def cmd_poll(args):
    token, chat = config()
    with state() as s:
        offset = s["offset"]
    updates = api(token, "getUpdates", http_timeout=LONG_POLL_SECONDS + 10, offset=offset,
                  timeout=LONG_POLL_SECONDS, allowed_updates=["message", "callback_query"])
    jobs = []
    for u in updates:
        with state() as s:
            s["offset"] = u["update_id"] + 1
        try:
            if "callback_query" in u and u["callback_query"]["message"]["chat"]["id"] == chat:
                handle_callback(token, chat, u["callback_query"])
            elif "message" in u and u["message"]["chat"]["id"] == chat:
                job = handle_message(token, chat, u["message"])
                if job:
                    jobs.append(job)
        except Exception as e:  # one bad update must not wedge the queue
            log(f"update {u['update_id']} failed: {e}")
    for job in jobs:  # after offsets are saved, so a slow claude run never replays updates
        if job[0] == "generate":
            generate(token, chat, job[1])
        elif job[0] == "learn":
            learn(token, chat, job[1], job[2])
        else:
            revise(token, chat, job[1], job[2])

    days, at, _ = schedule()
    now = dt.datetime.now()
    today = now.date().isoformat()
    with state() as s:
        due = now.weekday() in days and now.time() >= at and s.get("last_generated") != today
        if due:
            s["last_generated"] = today
    if due:
        generate(token, chat)


def cmd_setup(args):
    print("Paste the token from @BotFather (input is hidden):")
    token = getpass.getpass("Token: ").strip()
    me = api(token, "getMe")
    write_env({"TELEGRAM_BOT_TOKEN": token})
    print(f"\nToken is valid for @{me['username']}.")
    print(f"Now open https://t.me/{me['username']} on your phone and press START (or send /start).")
    deadline = time.time() + 300
    chat_id, offset = None, 0
    while time.time() < deadline and chat_id is None:
        for u in api(token, "getUpdates", http_timeout=40, offset=offset, timeout=30):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if m.get("chat", {}).get("type") == "private":
                chat_id = m["chat"]["id"]
                print(f"Got a message from {m['from'].get('first_name', '?')}.")
    if chat_id is None:
        sys.exit("No message arrived within 5 minutes. Run setup again.")
    write_env({"TELEGRAM_CHAT_ID": str(chat_id)})
    with state() as s:
        s["offset"] = offset
    api(token, "setMyCommands", commands=[
        {"command": "new", "description": "New draft now (optional: topic)"},
        {"command": "pending", "description": "Drafts not published yet"},
        {"command": "help", "description": "How I work"},
    ])
    say(token, chat_id, HELP)
    print("Done: I sent you a welcome message on Telegram.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    sub.add_parser("poll")
    sp = sub.add_parser("send")
    sp.add_argument("draft")
    sn = sub.add_parser("notify")
    sn.add_argument("text")
    args = p.parse_args()
    DRAFTS.mkdir(parents=True, exist_ok=True)
    {"setup": cmd_setup, "poll": cmd_poll, "send": cmd_send, "notify": cmd_notify}[args.cmd](args)


if __name__ == "__main__":
    main()
