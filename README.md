# ci-feedback-relay

A GitHub App that intercepts CI failures and enriches them with LangGraph/Ollama
root-cause analysis, then delivers structured payloads to a Claude Code session via MCP.

---

## GitHub App Setup

Setting up the GitHub App requires manual steps in the GitHub web UI. Complete these
steps once before running the application.

### Step 1 — Create the App

Go to [github.com/settings/apps](https://github.com/settings/apps) and click
**"New GitHub App"**.

### Step 2 — Name the App

- **App name:** `ci-feedback-relay` (use `ci-feedback-relay-dev` for a test instance)

### Step 3 — Homepage URL

- **Homepage URL:** `http://localhost:8080`

### Step 4 — Webhook URL

- **Webhook URL:** your smee.io proxy URL (create a channel at
  [smee.io](https://smee.io) — see Phase 0b setup for details)
- **Webhook active:** ✅ checked

### Step 5 — Webhook Secret

Generate a secret and save it to `.env`:

```bash
openssl rand -hex 20
```

Copy the output and add it to `.env`:

```
GITHUB_WEBHOOK_SECRET=<generated-value>
```

### Step 6 — Repository Permissions (read-only)

Under **"Repository permissions"**, set the following — **no write permissions**:

| Permission      | Access level |
|-----------------|--------------|
| Contents        | Read-only    |
| Pull requests   | Read-only    |
| Checks          | Read-only    |
| Metadata        | Read-only *(required base permission)* |

### Step 7 — Event Subscriptions

Under **"Subscribe to events"**, enable:

| Event                         | Purpose                                  |
|-------------------------------|------------------------------------------|
| `check_run`                   | Detect CI failures to enrich             |
| `push`                        | Index commits for diff context           |
| `pull_request_review_comment` | Capture inline review feedback           |
| `pull_request_review`         | Capture review approval / request-changes |

### Step 8 — Private Key

After creating the app, scroll to **"Private keys"** and click **"Generate a private
key"**. A `.pem` file will download automatically.

Move it into the `.keys/` directory:

```bash
mkdir -p .keys
mv ~/Downloads/*.pem .keys/app.pem
```

Set the path in `.env`:

```
GITHUB_PRIVATE_KEY_PATH=.keys/app.pem
```

### Step 9 — App ID

The **App ID** is shown at the top of the app's settings page (e.g.
`github.com/settings/apps/ci-feedback-relay`).

Add it to `.env`:

```
GITHUB_APP_ID=<your-app-id>
```

### Step 10 — Install the App and Note the Installation ID

1. In the app settings, click **"Install App"** in the left sidebar.
2. Install it on the `angelcantugr/ci-feedback-relay` repository.
3. After installation, your browser will redirect to a URL like:
   ```
   https://github.com/settings/installations/12345678
   ```
4. Note the numeric ID at the end of that URL — that is your Installation ID.

Add it to `.env`:

```
GITHUB_INSTALLATION_ID=<installation-id>
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
```

| Variable                 | Description                                               |
|--------------------------|-----------------------------------------------------------|
| `GITHUB_APP_ID`          | Numeric App ID from step 9                                |
| `GITHUB_PRIVATE_KEY_PATH`| Path to the downloaded `.pem` file (default `.keys/app.pem`) |
| `GITHUB_WEBHOOK_SECRET`  | Hex secret generated in step 5                            |
| `GITHUB_INSTALLATION_ID` | Numeric Installation ID from step 10                      |
| `OLLAMA_BASE_URL`        | Ollama endpoint (default `http://localhost:11434`)         |
| `SMEE_URL`               | smee.io channel URL from step 4                           |

---

## Development

### Smee Setup (one-time)

The development server uses [smee.io](https://smee.io) to forward GitHub webhook events to
your local machine.

```bash
# Install smee once
npm install -g smee-client

# Create a channel at https://smee.io/new → copy URL → add to .env as SMEE_URL
```

Add the channel URL to `.env`:

```
SMEE_URL=https://smee.io/your-channel-id
```

### Start Development

```bash
./scripts/start_dev.sh
```

This starts both the smee proxy and uvicorn together. Press Ctrl-C to stop both processes.