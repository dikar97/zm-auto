# AGENTS.md

Pure-HTTP account-registration automation (temp mail + 2captcha, no browser). CLI tool, single-purpose scripts.

## Layout gotcha

The git repo and all code live in the **nested** `zm-auto/` directory (e.g. `D:\workspace\zm-auto\zm-auto`), not the workspace root. Run everything from there.

```
register.py          ← main flow + CLI entrypoint (curl_cffi, threaded)
mail_provider.py     ← 7 temp-mail providers (factory + round-robin)
captcha_solver.py    ← 2captcha / anticaptcha (Turnstile + reCAPTCHA v2)
sub2api_importer.py  ← imports API keys into a Sub2API gateway
config.example.json  ← copy to config.json (gitignored, holds secrets)
```

## Setup & run

No manifest exists (no requirements.txt / pyproject). Install deps manually:

```bash
pip install curl_cffi requests urllib3
cp config.example.json config.json   # then edit config.json
python register.py -n 5 -t 2 --proxy http://127.0.0.1:7897
```

- No tests, no linter, no typecheck config, no CI. Verify changes by running `register.py` directly.
- `config.json` and `accounts.json` are gitignored (secrets / output). Never commit real proxies, domains, or keys — README scrubbed these to placeholders, keep it that way.

## Non-obvious behavior

- **Config loads at import time.** `register.py` runs `config = load_config()` at module level, which mutates module globals `TARGET_BASE` / `API_BASE` / `X_API_VERSION` from `config.json`. Editing those constants in source has no effect once `config.json` sets `target_base` / `target_api_version`.
- **`register_proxies` key is read but missing from `config.example.json`.** It lives in `DEFAULT_CONFIG` (`register.py`) and `load_config()` reads it. Each entry `{name, proxy_url, sub2api_proxy_id}` round-robins across workers and ties the registration IP to its Sub2API proxy. Add it to `config.json` if you need IP rotation.
- **API key masking workaround.** `/api_key/create` returns the token masked (`***`); the code refetches from `/api_key/list`. Don't "simplify" this away.
- **reCAPTCHA is always solved**, even when `needVerify=False` — the target returns 423 "not whitelisted" on `api_key/create` otherwise (see comment in `register.register`).
- **Hardcoded captcha site keys** in `captcha_solver.py` (`TURNSTILE_SITE_KEY`, `RECAPTCHA_SITE_KEY`) are target-specific, recovered from protocol analysis. `CAPTCHA_API_KEY` / `CAPTCHA_PROVIDER` env vars are fallbacks.
- **`_FORBIDDEN_CODES = {"177010"}`** in `mail_provider.py` is an OpenAI sentinel kept for upstream (chatgpt2api) parity; harmless, do not treat as the target's code.
- **Two success conventions:** Sub2API uses `data["code"] == 0`; the target site uses `resp["success"] == true`. Don't mix them.
- **HTTP clients differ on purpose:** `curl_cffi` with `impersonate="chrome"` for TLS fingerprinting on the target + Cloudflare/MoEmail; plain `requests` with `trust_env=False` for other mail providers. `verify=False` is intentional throughout.
- **Endpoint quirk:** chat completions is `/api/v1/chat/completions`, NOT `/v1/...`; key-test path is `/api/anthropic/v1/messages`.
- **`zm-N` naming:** on startup `_query_max_zm_number` pages the Sub2API admin API to continue numbering and avoid collisions.

## Conventions

- Logs, comments, and CLI help are in Chinese. Match that.
- Mail providers subclass `BaseMailProvider` and register in `_PROVIDER_CLASSES` (`mail_provider.py`). Each must return a dict with at least `address` and provider-specific token fields.
- Every module starts with the educational-use disclaimer header — preserve it.
