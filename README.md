# whistle-sns-bot

Scheduled Instagram posts and comment auto-replies for Whistle PRO, run on GitHub Actions every 5 minutes.

- `story-queue.json` — what to post and when (KST)
- `post_story.py due` — posts items whose time has come
- `ig_comment_dm.py run` — replies to event comments
- `state/` — what has been posted / replied (usernames are hashed)

Files here are synced from the main (private) repository; do not edit them directly.
