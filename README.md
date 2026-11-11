# TiDB + mem9 AI Investment Recommendation Demo

An end-to-end investment recommendation demo built with TiDB Cloud (vector and relational data, LangGraph checkpoints), mem9 (long-term memory), and Claude (LangGraph agent). The UI is Streamlit, deployed to Streamlit Community Cloud. All data is synthetic.

The app UI is available in Korean and English. Switch with the "언어 / Language" toggle in the sidebar or on the sign-in screen; the default follows the browser locale, and a shareable link can set it with `?lang=English`.

Design and decisions are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). This document covers only **how to deploy from scratch under your own accounts**.

## Overview

| Step | Task | Time |
|---|---|---|
| 1 | Fork or clone the repository | 1 min |
| 2 | Create a TiDB Cloud Starter cluster | 5 min |
| 3 | Get a mem9 API key and a Claude API key | 5 min |
| 4 | Check connections and load seed data locally | 10 min |
| 5 | Set up Google sign-in (OAuth client) | 10 min |
| 6 | Deploy to Streamlit Community Cloud | 10 min |
| 7 | Verify the deployment | 5 min |

You need: a GitHub account, a Google account, Python 3.12, and a payment method (for Claude API credits; $5 is enough).

---

## 1. Get the repository

Streamlit Community Cloud can deploy only **repositories your GitHub account can access**. **Fork** this repository on GitHub, then clone it.

```bash
git clone https://github.com/<your GitHub account>/tidb-mem9-demo.git
cd tidb-mem9-demo
```

## 2. Create a TiDB Cloud Starter cluster

1. Sign up at https://tidbcloud.com.
2. Under **Create Cluster**, choose **Starter**.
   - Cloud Provider: **AWS** (Auto Embedding, i.e. `EMBED_TEXT` inside SQL, is supported only on AWS)
   - Region: **Tokyo (ap-northeast-1)** recommended
3. On the cluster page, click **Connect**.
   - Connect With: `General`
   - Click **Generate Password** and save the password somewhere. You can't view it again.
   - Note the Host, Port (4000), and User (in the form `xxxxxxxx.root`).
4. In **SQL Editor** on the left, create the database.

   ```sql
   CREATE DATABASE invest_demo;
   ```

   The tables are created by `seed.py` in step 4.

## 3. Get API keys

**mem9**: No sign-up needed. The `id` value in the response to the command below is your API key.

```bash
curl -sX POST https://api.mem9.ai/v1alpha1/mem9s
# Check the key: "active" means it works
curl -s https://api.mem9.ai/v1alpha2/status -H "X-API-Key: <your key>"
```

**Claude**: Sign up at https://platform.claude.com, add credits under **Billing**, then create a key under **API Keys**.

## 4. Local setup (check connections and load seed data)

Cloud doesn't run the seed script, so **the first time, you must create the tables and load the seed data locally.**

Python 3.12 is required (3.10 minimum). The default macOS `python3` is 3.9 and won't work.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in the values below
```

Values to fill in `.env`:

| Key | Value |
|---|---|
| `TIDB_HOST`, `TIDB_PORT`, `TIDB_USER`, `TIDB_PASSWORD` | The values you noted in step 2 |
| `TIDB_DATABASE` | `invest_demo` |
| `MEM9_API_KEY` | The mem9 key from step 3 |
| `ANTHROPIC_API_KEY` | The Claude key from step 3 |
| `LLM_MODEL` | Default `claude-haiku-5-5` (leave as is) |
| `ADMIN_EMAILS` | Google emails that can see the admin screen (comma-separated) |

```bash
python scripts/smoke_test.py   # checks TiDB, Auto Embedding, mem9, and Claude connections. All must PASS
python scripts/seed.py         # creates tables and loads seed data
```

To preview the app locally first, temporarily add `DEMO_AUTH=off` to `.env` and run `streamlit run app.py`. This setting skips Google sign-in and is **for local use only**; never put it in the deployed Secrets.

## 5. Set up Google sign-in

The deployed app opens only after Google sign-in (`st.login`). Without sign-in settings, the app shows an error and doesn't open (to prevent it from being exposed without protection).

1. At https://console.cloud.google.com, create or select a project.
2. **APIs & Services > OAuth consent screen**
   - User type: **External**
   - Scopes: keep only the defaults (openid, email, profile).
   - Change the publishing status to **In production**. If left in "Testing", only registered test users can sign in. With only the default scopes, Google app verification isn't required.
3. **Credentials > Create OAuth client ID > Web application**
   - Authorized redirect URIs: `http://localhost:8501/oauth2callback`
   - Add the deployed URL once it's decided in step 6.
4. Note the issued **Client ID** and **Client secret**.
5. Generate a random string for cookie encryption.

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

## 6. Deploy to Streamlit Community Cloud

1. Sign in to https://share.streamlit.io with your GitHub account and click **Create app**.
2. Choose the repository you forked in step 1, branch `main`, and Main file path `app.py`.
3. Set the address you want under **App URL**. Example: `my-mem9-demo` gives `https://my-mem9-demo.streamlit.app`
4. **Advanced settings**
   - Python version: **3.12**
   - Secrets: copy [.streamlit/secrets.toml.example](.streamlit/secrets.toml.example), fill in the values, and paste it.

   ```toml
   TIDB_HOST = "gateway01.ap-northeast-1.prod.aws.tidbcloud.com"
   TIDB_PORT = "4000"
   TIDB_USER = "xxxxxxxx.root"
   TIDB_PASSWORD = "..."
   TIDB_DATABASE = "invest_demo"

   MEM9_API_URL = "https://api.mem9.ai"
   MEM9_API_KEY = "..."

   ANTHROPIC_API_KEY = "sk-ant-..."
   LLM_MODEL = "claude-haiku-5-5"

   ADMIN_EMAILS = "you@example.com"
   LANGSMITH_TRACING = "false"

   [auth]
   redirect_uri = "https://my-mem9-demo.streamlit.app/oauth2callback"
   cookie_secret = "random string generated in step 5"
   client_id = "....apps.googleusercontent.com"
   client_secret = "..."
   server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
   ```

   - Enter the same values as `.env`, in **TOML format**. Wrap every value in quotes.
   - Sign-in settings must go in the **`[auth]` section** at the very bottom. Only the top-level keys above it are copied into environment variables, so if the order changes, the keys won't be found.
   - Don't include `DEMO_AUTH`.
5. Click **Deploy**. Installing packages takes a few minutes.
6. In the Google Cloud Console OAuth client (step 5, item 3), add the redirect URI `https://<App URL>.streamlit.app/oauth2callback`. It must match `redirect_uri` in Secrets exactly, character for character.

The TLS certificate needed for the TiDB connection is handled by the `certifi` package, so there are no extra files to upload.

## 7. Verify the deployment

1. Open the deployed URL and sign in with Google.
2. On the **Signal analysis** screen, run the AI analysis. This step analyzes the raw signals to create investment opportunities and must be done once at the start (about 15 seconds).
3. Choose a demo user in the sidebar and check that recommendations are generated on the **Personalized recommendations** screen. Each recommendation takes about 15–20 seconds.
4. If your account is in `ADMIN_EMAILS`, the **Access log** screen is visible and should show the sign-in you just made.
5. In **Behind the scenes > LangGraph**, you can see the run time of each node.

## Redeploying after code changes

- Pushing to `main` makes Cloud pull the new code.
- However, the server isn't restarted, so already-loaded `core/` and `ui/` modules keep the old code. This can cause errors such as `ImportError` on screen.
- **If you changed `core/` or `ui/`, restart after pushing with Manage app > ⋮ > Reboot app.** Restarting clears records held in server memory, such as the SQL and mem9 call logs.
- If you changed the table structure (`sql/schema.sql`), run `python scripts/seed.py --recreate` locally.

The pre-merge verification commands are in [CLAUDE.md](CLAUDE.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Missing environment variables: ..." | The key is missing from Secrets or was placed under `[auth]`. Move top-level keys above `[auth]`. |
| "The app can't open because sign-in ([auth]) isn't configured" | Secrets has no `[auth]` section. Check step 6, item 4. |
| `redirect_uri_mismatch` after Google sign-in | The OAuth client's redirect URI differs from `redirect_uri` in Secrets. Match them exactly, including `https://`, the address, and `/oauth2callback`. |
| "Access blocked" on the Google sign-in screen | The OAuth consent screen is in "Testing". Publish it to production. |
| "There are no opportunities yet" | Run the AI analysis on the Signal analysis screen first. |
| SQL error saying a table doesn't exist | The seed data wasn't loaded. Run `python scripts/seed.py` locally. |
| Errors related to `EMBED_TEXT` | The cluster isn't on AWS. Recreate the cluster in an AWS region. |
| `ImportError` after a push | Run Reboot app as described above. |
| Secrets changes don't take effect | After saving Secrets, run Reboot app once. |

## Security notes

- `.env` and `.streamlit/secrets.toml` are not committed to git (they're in `.gitignore`). Keep real values only in local files and the Cloud Secrets screen.
- Turning on `LANGSMITH_TRACING` sends prompts and responses to LangSmith servers. It's off by default.
- If multiple deployments share the same TiDB cluster and mem9 key, their data gets mixed. Create separate ones for each deployment.
