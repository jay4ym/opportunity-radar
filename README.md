# Opportunity Radar

A small Python opportunity monitor that sends matching RSS and webpage listings to Telegram. It runs on GitHub Actions, so your own computer does not need to stay on.

## 1. Configure Telegram

1. In Telegram, message `@BotFather` and send `/newbot`.
2. Follow the prompts and copy the bot token.
3. Open your bot and press **Start**.
4. Visit `https://api.telegram.org/botYOUR_TOKEN/getUpdates` and find `message.chat.id`.
5. In this GitHub repository, open **Settings → Secrets and variables → Actions**.
6. Add repository secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

Never commit your bot token to the repository.

## 2. Upload and enable

1. Create a GitHub repository named `opportunity-radar`.
2. Upload all files in this project, including the hidden `.github` folder.
3. In **Settings → Actions → General → Workflow permissions**, allow read and write permissions if repository policy requires it.
4. Open **Actions → Opportunity Radar → Run workflow** for the first test.
5. Check Telegram and the Actions run log.

The workflow is scheduled every six hours (UTC). GitHub scheduled workflows can be delayed during busy periods and may be disabled in inactive public repositories.

## 3. Customize

Edit `config.json`:

- Add a keyword to `keywords`.
- Add a source object with `type` set to `rss` or `html`.
- For HTML sources, set `url` and optionally a CSS `selector`.
- Set `enabled` to `false` to pause a source.

Example RSS source:

```json
{
  "name": "Example feed",
  "type": "rss",
  "url": "https://example.com/feed/",
  "enabled": true
}
```

Example HTML source:

```json
{
  "name": "Example events",
  "type": "html",
  "url": "https://example.com/events",
  "selector": "article a[href]",
  "enabled": true
}
```

## Important limitations

- Generic HTML anchor discovery is best-effort. Sites may change their markup, use JavaScript rendering, or block automated requests. A selector may need tuning for each site.
- This starter project does not bypass logins, CAPTCHAs, paywalls, or anti-bot controls. Respect each website's terms and robots guidance.
- Deadline extraction uses date patterns in visible text. It can miss deadlines or interpret ambiguous dates incorrectly. Always verify the official page.
- Seven-day reminders are sent only when a deadline date is successfully extracted and stored. A listing first discovered fewer than seven days before its deadline will not receive a one-week reminder.
- GitHub Actions is free within the applicable account usage limits, not an unlimited hosting guarantee.
- The JSON state file is committed to the repository. Keep it private if you prefer not to publish your opportunity history.

## Files

- `main.py`: collection, filtering, Telegram delivery, deadline reminders.
- `config.json`: keywords and monitored sources.
- `sent_items.json`: deduplication and reminder state.
- `.github/workflows/radar.yml`: scheduled execution.
