# Authorization

The script supports **five credential methods**. By default (`auto`) it uses the first one that is configured, in this order; force one with `--auth <method>` on any command or `GSHEET_OPS_AUTH=<method>`. `auth-check` shows `method_in_use` and which methods are available (names only, never values).

| # | Method | How to provide it | Typical use | Verified how |
|---|---|---|---|---|
| 1 | `token` | env `GSHEET_OPS_TOKEN` (access token) or env `GSHEET_OPS_TOKEN_FILE` (path of a file containing it) | a short-lived token handed over by the user or another tool | real Google (env and file) |
| 2 | `refresh` | env `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` + `GOOGLE_REFRESH_TOKEN` | long-running or unattended jobs with an OAuth client | mock OAuth server only |
| 3 | `service-account` | env `GOOGLE_APPLICATION_CREDENTIALS` = path of the key JSON; optional `GSHEET_OPS_IMPERSONATE` = user email (domain-wide delegation) | automation without a human login | mock OAuth server only; the RS256 signature was verified with the public key |
| 4 | `skynet` | nothing to configure: the `skynet-base` login (key `GOOGLE_TOKEN`) | default for Digital Engineer / Skynet users | real Google |
| 5 | `gcloud` | `gcloud` installed and logged in (`gcloud auth print-access-token`) | users who already work with gcloud | stub binary only |

"mock/stub only" means the whole code path ran against a local fake OAuth server or a fake `gcloud`, so the protocol, parameters and error handling are tested, but no real Google credential of that kind was available to try. Treat these three as likely to work and report any real-world failure.

## Method details

**1 token.** The token is used as is. Access tokens last about an hour. On HTTP 401 the script stops with "token rejected (expired, revoked or lacking scope)" because it cannot renew a token it does not own. Never ask a user to paste a token into the conversation; use a file or environment variable they set themselves.

**2 refresh.** The script exchanges the refresh token at Google's token endpoint (`grant_type=refresh_token`), caches the access token in memory until a minute before it expires, and re-exchanges after a 401. The client secret and refresh token are scrubbed from every error message.

**3 service-account.** The script builds a signed JWT (RS256) for scopes `spreadsheets` and `drive`, signs it with the system `openssl` (the private key goes to openssl over stdin; only the public JWT body touches a temporary directory in the working directory, which is removed immediately), and exchanges it (`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`). Requirements: `openssl` installed; the **spreadsheet must be shared with the service account's e-mail** (Editor), or domain-wide delegation must be set up and `GSHEET_OPS_IMPERSONATE` given. Creating service accounts may be restricted by your organization.

**4 skynet.** The script reads the Google access token the Skynet login already holds (`skynet-base key get GOOGLE_TOKEN --raw`) into memory, re-reads it every five minutes, and never prints it. On HTTP 401 it runs one harmless read through the official CLI (`skynet-base gsheet list-sheets <url>`), which renews or re-authorizes, then retries once. If that still fails, re-authorize through the CLI (`skynet-base key remove GOOGLE_REFRESH_TOKEN`, then run any `skynet-base gsheet` command and finish the browser step). In cloud runs that cannot open a browser, stop and ask the user to authorize elsewhere.

**5 gcloud.** Runs `gcloud auth print-access-token` (cached five minutes, re-run after a 401). Needs a prior `gcloud auth login`.

## What the credential allows

`auth-check` asks Google's token-info endpoint. In the session where this skill was built, the stored Skynet token had these scopes: `spreadsheets` (full read/write incl. formatting and structure), `drive` (used for `export` and `copy-spreadsheet`), plus documents, presentations, calendar, gmail.compose, gmail.readonly, meetings.space.readonly, and identity scopes; it had about 3,500 seconds of lifetime left. Service-account and refresh-token methods request only `spreadsheets` and `drive` (the refresh method gets whatever scopes the refresh token was issued with). Every method acts as its identity: a user token can only edit what that user can edit; a service account only what is shared with it.

## Other environment variables

| Variable | Purpose |
|---|---|
| `GSHEET_OPS_AUTH` | force a method: `token`, `refresh`, `service-account`, `skynet`, `gcloud` (or `auto`) |
| `GSHEET_OPS_TOKEN_URL` | override Google's OAuth token endpoint (testing or private gateways); never point it at a host you do not control, because refresh tokens and signed assertions are sent there |

## What not to do

- Do not print, echo, log, or write any token, refresh token, client secret or private key, including in debugging output.
- Do not run `skynet-base key list` / `key get` interactively just to look at tokens.
- Do not read the CLI's local credential files or copy a key file into the repository; point `GOOGLE_APPLICATION_CREDENTIALS` at the file where it already lives.
- Do not ask a user to paste a credential into the conversation.
