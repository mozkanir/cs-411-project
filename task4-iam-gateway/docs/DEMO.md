# Presentation demo: IAM & login (about 3 minutes)

The whole presentation is 12 minutes, so the demo is short and scripted.
Everything below runs on one laptop with Ollama and `qwen2.5:0.5b`.

## Before the presentation (5 minutes, do not skip)

Run in the `task4-iam-gateway` folder with the venv active:

```powershell
python scripts/demo_setup.py          # fresh demo.db, users, keys, request files
. .\demo_env.ps1                      # loads $GW, $BOB, $VERA and IAM_DB_URL
ollama run qwen2.5:0.5b "hi"          # load the model now, so the demo answer is instant
python -m ollama_iam_gateway.cli serve
```

Then prepare the screen:

1. **Browser tab 1:** `http://127.0.0.1:8080/iam/login` (nobody signed in).
2. **Browser tab 2 (private window):** also the login page, for bob later.
3. **Terminal 2:** run `. .\demo_env.ps1` here too, font size large.
4. Make sure Ollama is **not** started with `OLLAMA_HOST=0.0.0.0`.

Demo passwords: `admin / admin-demo-1`, `bob / bob-demo-1`, `vera / vera-demo-1`.

## Script

These steps really are a sequence: each one builds on the one before.

### 1. The problem (30 s)

Terminal 2:
```powershell
curl.exe http://127.0.0.1:11434/api/tags
```
> "This is plain Ollama. No key, no login, and it answers. Its only login, `ollama signin`,
> links the machine to ollama.com. It does not protect this server. If the port is opened
> to the network, anyone can chat, pull 70 GB models or delete them."

### 2. The new login page (40 s)

Tab 1:
- Sign in as `admin` with a **wrong password** → *"Invalid username or password."*
  > "Same message for a wrong user or a wrong password, so attackers cannot find usernames.
  > 5 failures lock the account for 15 minutes."
- Sign in correctly → Account page. Point at the ✓/✕ list.
- Create a key named `demo` → it is shown **once**.
  > "We store only a hash of it. If the database leaks, the keys are useless."

### 3. Roles at work (50 s)

Terminal 2:
```powershell
curl.exe $GW/api/tags                                                     # 401: no key
curl.exe -H "Authorization: Bearer $VERA" $GW/api/tags                    # 200: viewer can list
curl.exe -H "Authorization: Bearer $VERA" $GW/api/chat --data-binary "@body.json"   # 403
curl.exe -N -H "Authorization: Bearer $BOB" $GW/api/chat --data-binary "@body.json" # streams
curl.exe -X DELETE -H "Authorization: Bearer $BOB" $GW/api/delete --data-binary "@delete.json"   # 403
```
> "The gateway checks the key, asks the policy engine, and only then forwards to Ollama.
> The answer still streams token by token. The 403 says *why*: role 'user' lacks models:manage."

### 4. Instant revocation (30 s)

Tab 1 → **Users** → **Disable** bob. Then terminal 2:
```powershell
curl.exe -H "Authorization: Bearer $BOB" $GW/api/tags     # 401 immediately
```
> "Server-side sessions and key checks on every request, so disabling works at once.
> With JWTs this would only work after the token expires."

### 5. Audit log (30 s)

Tab 1 → **Audit log**. Point at the red rows (401, 403, failed login).
Filter by user `bob`.
> "Every login, admin action and Ollama request: who, what, which model, result."

## If something fails

- **Ollama not answering:** the gateway returns 502 *"Ollama is not reachable"*.
  Start Ollama and continue.
- **Anything else:** run the self-contained backup, which uses its own port and database:
  `python scripts/demo_step4.py`

## Likely questions (short answers)

| Question | Answer |
|---|---|
| Why a gateway and not a change in Ollama? | Ollama is Go and changes fast. A gateway adds IAM without forking it, and it works with every Ollama version. The cost is one extra local hop (a few ms). |
| What stops people from bypassing the gateway? | Ollama listens on 127.0.0.1 only (deployment view). The gateway is the single way in. |
| Why argon2 for passwords but sha256 for keys? | Passwords are guessable, so the hash must be slow. Keys are 256-bit random values: nothing to guess, so a fast hash is enough and keeps requests fast. |
| Why sessions in the DB and not JWT? | Logout and disabling a user take effect immediately. JWTs cannot be revoked before they expire. |
| Why can't the browser cookie call the Ollama API? | So a malicious web page cannot use an admin's login to call `/api/delete` (CSRF). The API accepts only API keys. |
| A new Ollama version adds an endpoint. What happens? | Default deny: it is blocked for everyone until it is added to the policy table. |
| Can someone trick the policy with `/api/chat/../delete`? | No. Unusual paths are refused, never "fixed", so the checked path is the path Ollama routes. |
| Does the `ollama` CLI work through the gateway? | Not in the MVP: the CLI cannot send an API key (known limitation). Verifying its `OLLAMA_AUTH` signature is a planned extension. |
| Single point of failure? | Yes, like Ollama itself on one server. It is stateless apart from SQLite, so it can be restarted at once. |
| How do you add a new role? | Add it to `Role` and give it permissions in `ROLE_PERMISSIONS` (`iam/policy.py`). No other code changes. |
