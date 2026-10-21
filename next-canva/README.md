# Events / Canva automation

This is the Vercel web control desk for the Python Canva → Sheets → Drive
worker. It supports:

- Google authorization for Sheets and full Drive access.
- A shared date, business, content type, status, description, and unlimited
  Canva source links.
- Server-side proxying to the Python API so `AUTOMATION_SECRET` never reaches
  the browser.
- Worker health status and live job polling.
- Manual access-token paste for the current browser session, if OAuth is not
  being used yet.

## Google Cloud setup

1. Enable **Google Sheets API** and **Google Drive API** in the Google Cloud
   project.
2. Configure the OAuth consent screen and add the Google account as a test
   user while the app is in Testing mode.
3. Create a **Web application** OAuth client.
4. Add this authorized redirect URI:

   ```text
   https://YOUR-VERCEL-DOMAIN/api/auth/google/callback
   ```

   For local development, also add:

   ```text
   http://localhost:3000/api/auth/google/callback
   ```

5. Configure the same account with edit access to the target Sheet and Drive
   root folder.

## Environment

Copy `.env.example` to `.env.local`:

```text
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
AUTOMATION_BACKEND_URL=https://events.mudasarmajeed5.tech
AUTOMATION_SECRET=the_same_value_as_server/.env
NEXT_PUBLIC_SHEET_URL=https://docs.google.com/spreadsheets/d/.../edit
```

The OAuth callback displays the short-lived Google access token in the page so
it can be copied into the current session. It is not stored in a database or
sent to logs. For a production multi-user deployment, replace this manual
token handoff with a server-side session/token store such as Better Auth plus
Neon, as described in `05_nextjs_web_app.md`.

## Run

```bash
pnpm install
pnpm dev
```

Open <http://localhost:3000>, choose **Continue with Google**, approve the
Sheets and Drive scopes, and submit a batch of Canva links.

## Validate

```bash
pnpm lint
pnpm build
```
