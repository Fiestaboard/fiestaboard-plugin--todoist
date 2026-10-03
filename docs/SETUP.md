# Todoist Setup Guide

Connect your Todoist account so your board shows tasks due today, overdue tasks and the next thing to do.

## Overview

The Todoist plugin reads the tasks in your Todoist account that are due today or overdue, counts them, and picks the next one to do. It only reads: it cannot add, change, complete or delete anything.

**Prerequisites:**

- FiestaBoard 9.8.0 or later
- A Todoist account (free or paid)

There are two ways to connect. Use one:

- **Sign in with Todoist** (recommended). Nothing to create or copy. FiestaBoard asks Todoist for read-only access (`data:read`) and keeps the sign-in fresh.
- **Personal API token.** Paste the token from your Todoist settings. Use this if you cannot sign in from a browser. The token gives full access to your account, so keep it private.

If both are set up, the sign-in is used.

## Quick Setup

### 1. Enable the Plugin

1. Open your FiestaBoard web UI and go to **Integrations**.
2. Find **Todoist** and install it if needed.
3. Open its settings and turn on **Enable Todoist**.

### 2. Connect your account

**Sign in with Todoist:**

1. In the plugin's settings, find **Account connection**.
2. Press **Sign in with Todoist**.
3. Todoist asks whether to allow FiestaBoard to read your data. Press **Agree**.
4. You come back to FiestaBoard and the panel shows that you are connected.

The first time you sign in from a browser, a FiestaBoard page asks you to confirm your board's address. That page only ever sends you back to an address on your own network.

**Or paste a personal API token:**

1. In Todoist, open **Settings > Integrations > Developer**.
2. Copy the **API token**.
3. Paste it into **Personal API Token (optional)** in the plugin's settings and save.

### 3. Check the time zone

The plugin decides what counts as "today" in the board's time zone. If your Todoist account uses a different time zone (Todoist **Settings > General > Time zone**), enter that one in **Time Zone (optional)**, for example `Europe/Berlin`.

### 4. Create a Board Template

1. Go to **Pages** and create a page.
2. Add a template that uses the Todoist variables, for example:

```
{{todoist.status_tile}} TODOIST

{{todoist.summary}}

NEXT UP
{{todoist.next_task}}
```

### 5. View on Your Board

Save the page and show it on your board. The counts update every 5 minutes by default.

## Template Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `{{todoist.due_today}}` | Tasks due today (not counting overdue ones) | `3` |
| `{{todoist.overdue}}` | Tasks due before today | `1` |
| `{{todoist.due_total}}` | Due today plus overdue | `4` |
| `{{todoist.summary}}` | Counts on one line, or `ALL CLEAR` | `3 TODAY 1 OVERDUE` |
| `{{todoist.status_tile}}` | Red: something overdue. Yellow: tasks due today. Green: clear | `{63}` |
| `{{todoist.all_clear}}` | Nothing due today and nothing overdue | `false` |
| `{{todoist.next_task}}` | The oldest overdue task, then today's tasks by time and priority | `CALL THE PLUMBER` |
| `{{todoist.next_task_due}}` | `OVERDUE`, `TODAY`, or a time like `3:30 PM` | `OVERDUE` |
| `{{todoist.next_task_priority}}` | `P1` (urgent) to `P4` | `P1` |
| `{{todoist.tasks.N.content}}` | Task N of up to 10 (N from 0) | `WATER THE FERNS` |
| `{{todoist.tasks.N.due}}` | When task N is due | `8:30 AM` |
| `{{todoist.tasks.N.priority}}` | Priority of task N | `P4` |

## Configuration Reference

| Setting | Type | Description | Default |
|---------|------|-------------|---------|
| Enable Todoist (`enabled`) | boolean | Turn the plugin on | `false` |
| Personal API Token (`api_key`) | password | Optional. Used only when you are not signed in | empty |
| Time Zone (`timezone`) | string | IANA time zone that decides what counts as today. Empty uses the board's | empty |
| Refresh Interval (`refresh_seconds`) | integer | How often to ask Todoist, 60 to 3600 seconds | `300` |

This plugin uses no environment variables.

## Troubleshooting

**"Not connected to Todoist"**
Nobody has signed in and no API token is saved. Press **Sign in with Todoist** in Account connection, or paste a token.

**"Todoist rejected the sign-in (401)"**
The sign-in was revoked or expired, for example after removing FiestaBoard under Todoist **Settings > Integrations > Connected**. Press **Reconnect**.

**"Todoist rejected the personal API token (401)"**
The token was reset or mistyped. Copy it again from **Settings > Integrations > Developer**.

**"Todoist refused access to your tasks (403)"**
The sign-in does not allow reading tasks. Press **Reconnect** and approve the request.

**"Todoist rate limit reached"**
Too many requests in a short time, often from other apps using the same account. The plugin waits as long as Todoist asks, then tries again. Raise the refresh interval if it keeps happening.

**Counts are off by a day around midnight**
The board and your Todoist account are in different time zones. Set **Time Zone** to the one in your Todoist settings.

**A task I expected is missing**
The plugin shows the same tasks as the Todoist filter `today | overdue`. Tasks with no date, or due later, are not shown.
