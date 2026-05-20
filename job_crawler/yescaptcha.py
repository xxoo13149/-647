from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .utils import clean_text, emit_task_log


class YesCaptchaError(RuntimeError):
    """Raised when the YesCaptcha API returns an explicit error."""


class YesCaptchaClient:
    """Small async client for the YesCaptcha create/getResult workflow."""

    API_URL = "https://api.yescaptcha.com/createTask"
    RESULT_URL = "https://api.yescaptcha.com/getTaskResult"

    def __init__(self, client_key: str, timeout: int = 120):
        self.client_key = client_key
        self.timeout = timeout
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "YesCaptchaClient":
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout + 30),
            headers={"Content-Type": "application/json"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self.session:
            await self.session.close()

    async def create_task(self, task_type: str, **kwargs) -> str:
        if not self.session:
            raise RuntimeError("YesCaptcha session is not initialized.")

        payload = {
            "clientKey": self.client_key,
            "task": {
                "type": task_type,
                **kwargs,
            },
        }

        async with self.session.post(self.API_URL, json=payload) as response:
            result = await response.json(content_type=None)
            if response.status != 200:
                raise YesCaptchaError(f"HTTP {response.status}: {result}")
            if result.get("errorId") != 0:
                code = result.get("errorCode", "UNKNOWN")
                desc = result.get("errorDescription", "")
                raise YesCaptchaError(f"{code}: {desc}".strip())
            task_id = result.get("taskId")
            if not task_id:
                raise YesCaptchaError(f"Missing taskId in response: {result}")
            return str(task_id)

    async def poll_solution(self, task_id: str) -> dict[str, Any]:
        if not self.session:
            raise RuntimeError("YesCaptcha session is not initialized.")

        payload = {"clientKey": self.client_key, "taskId": task_id}
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            async with self.session.post(self.RESULT_URL, json=payload) as response:
                result = await response.json(content_type=None)
                if response.status != 200:
                    raise YesCaptchaError(f"HTTP {response.status}: {result}")
                if result.get("errorId") != 0:
                    code = result.get("errorCode", "UNKNOWN")
                    desc = result.get("errorDescription", "")
                    raise YesCaptchaError(f"{code}: {desc}".strip())
                status = clean_text(str(result.get("status", ""))).lower()
                if status == "ready":
                    return result.get("solution") or {}
            await asyncio.sleep(2)

        raise YesCaptchaError("Timed out while waiting for captcha solution.")

    async def solve(self, task_type: str, **kwargs) -> dict[str, Any]:
        task_id = await self.create_task(task_type, **kwargs)
        return await self.poll_solution(task_id)


def is_yescaptcha_configured(settings: dict[str, Any]) -> bool:
    api_key = settings.get("yescaptcha_api_key", "").strip()
    return bool(api_key)


def _mask_key(api_key: str) -> str:
    key = clean_text(api_key)
    if len(key) <= 10:
        return "*" * len(key)
    return f"{key[:6]}...{key[-4:]}"


def _extract_first(patterns: list[str], html: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            return clean_text(match.group(1))
    return ""


def detect_captcha_context(html: str, page_url: str = "") -> dict[str, Any]:
    lowered = html.lower()
    context: dict[str, Any] = {
        "kind": "unknown",
        "site_key": "",
        "gt": "",
        "challenge": "",
        "captcha_url": "",
        "appid": "",
        "notes": [],
    }

    gt = _extract_first(
        [
            r"""gt\s*[:=]\s*['"]([^'"]+)['"]""",
            r"""["']gt["']\s*:\s*["']([^"']+)["']""",
        ],
        html,
    )
    challenge = _extract_first(
        [
            r"""challenge\s*[:=]\s*['"]([^'"]+)['"]""",
            r"""["']challenge["']\s*:\s*["']([^"']+)["']""",
        ],
        html,
    )
    if gt:
        context["kind"] = "geetest"
        context["gt"] = gt
        context["challenge"] = challenge
        if not challenge:
            context["notes"].append("GeeTest markers found but challenge was empty.")
        return context

    if "cf-turnstile" in lowered or "turnstile" in lowered:
        context["kind"] = "turnstile"
        context["site_key"] = _extract_first(
            [
                r"""data-sitekey\s*=\s*['"]([^'"]+)['"]""",
                r"""["']sitekey["']\s*:\s*["']([^"']+)["']""",
            ],
            html,
        )
        return context

    if "g-recaptcha" in lowered or "recaptcha" in lowered:
        context["kind"] = "recaptcha_v2"
        context["site_key"] = _extract_first(
            [
                r"""data-sitekey\s*=\s*['"]([^'"]+)['"]""",
                r"""sitekey\s*[:=]\s*['"]([^'"]+)['"]""",
            ],
            html,
        )
        return context

    if "hcaptcha" in lowered:
        context["kind"] = "hcaptcha"
        context["site_key"] = _extract_first([r"""data-sitekey\s*=\s*['"]([^'"]+)['"]"""], html)
        return context

    captcha_url = _extract_first(
        [
            r"""(https://captcha\.eo\.qq\.com/[^"' )]+)""",
            r"""(https://captcha\.eo\.gtimg\.com/[^"' )]+)""",
            r"""(//captcha\.eo\.qq\.com/[^"' )]+)""",
            r"""(//captcha\.eo\.gtimg\.com/[^"' )]+)""",
        ],
        html,
    )
    if captcha_url:
        if captcha_url.startswith("//"):
            captcha_url = f"https:{captcha_url}"
        context["captcha_url"] = captcha_url
        context["appid"] = parse_qs(urlparse(captcha_url).query).get("appid", [""])[0]
        context["kind"] = "tencent"
        context["notes"].append("Detected Tencent EdgeOne captcha script/iframe.")
        return context

    if any(token in lowered for token in ["captcha.eo.qq.com", "captcha.eo.gtimg.com", "tdc.js"]):
        context["kind"] = "tencent"
        context["notes"].append("Detected Tencent captcha markers without a parseable task payload.")
        return context

    if any(token in lowered for token in ["cloudflare", "cf-challenge", "__cf_chl_"]):
        context["kind"] = "cloudflare"
        context["notes"].append("Detected Cloudflare markers but not a Turnstile widget.")
        return context

    if page_url and "captcha" in page_url.lower():
        context["notes"].append(f"Captcha-like URL: {page_url}")

    return context


def describe_captcha_context(context: dict[str, Any]) -> str:
    parts = [f"kind={context.get('kind', 'unknown')}"]
    if context.get("site_key"):
        parts.append(f"site_key={context['site_key'][:10]}...")
    if context.get("gt"):
        parts.append(f"gt={context['gt'][:10]}...")
    if context.get("challenge"):
        parts.append(f"challenge={context['challenge'][:10]}...")
    if context.get("appid"):
        parts.append(f"appid={context['appid']}")
    if context.get("captcha_url"):
        parts.append(f"url={context['captcha_url'][:80]}")
    notes = context.get("notes") or []
    if notes:
        parts.append(f"notes={'; '.join(str(note) for note in notes)}")
    return ", ".join(parts)


async def _inject_recaptcha_token(page, token: str) -> bool:
    return bool(
        await page.evaluate(
            """
            (token) => {
              const applied = [];
              for (const selector of [
                'textarea[name="g-recaptcha-response"]',
                'textarea#g-recaptcha-response',
                'input[name="g-recaptcha-response"]'
              ]) {
                const node = document.querySelector(selector);
                if (node) {
                  node.value = token;
                  node.innerHTML = token;
                  node.dispatchEvent(new Event('input', { bubbles: true }));
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                  applied.push(selector);
                }
              }
              return applied.length > 0;
            }
            """,
            token,
        )
    )


async def _inject_hcaptcha_token(page, token: str) -> bool:
    return bool(
        await page.evaluate(
            """
            (token) => {
              const applied = [];
              for (const selector of [
                'textarea[name="h-captcha-response"]',
                'textarea[name="g-recaptcha-response"]',
                'input[name="h-captcha-response"]'
              ]) {
                const node = document.querySelector(selector);
                if (node) {
                  node.value = token;
                  node.innerHTML = token;
                  node.dispatchEvent(new Event('input', { bubbles: true }));
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                  applied.push(selector);
                }
              }
              return applied.length > 0;
            }
            """,
            token,
        )
    )


async def _inject_turnstile_token(page, token: str) -> bool:
    return bool(
        await page.evaluate(
            """
            (token) => {
              const selectors = [
                'input[name="cf-turnstile-response"]',
                'textarea[name="cf-turnstile-response"]',
                'input[name="g-recaptcha-response"]',
                'textarea[name="g-recaptcha-response"]'
              ];
              let applied = 0;
              for (const selector of selectors) {
                document.querySelectorAll(selector).forEach((node) => {
                  node.value = token;
                  node.innerHTML = token;
                  node.dispatchEvent(new Event('input', { bubbles: true }));
                  node.dispatchEvent(new Event('change', { bubbles: true }));
                  applied += 1;
                });
              }
              if (window.turnstile && typeof window.turnstile.render === 'function') {
                window.__codexTurnstileToken = token;
              }
              return applied > 0;
            }
            """,
            token,
        )
    )


async def _inject_geetest_result(page, gt: str, challenge: str, result: dict[str, Any]) -> bool:
    validate = clean_text(str(result.get("validate", "")))
    seccode = clean_text(str(result.get("seccode", "")))
    if not validate or not seccode:
        return False

    await page.evaluate(
        """
        (payload) => {
          window.gt = payload.gt;
          window.challenge = payload.challenge;
          window.validate = payload.validate;
          window.seccode = payload.seccode;
          window.__codexGeetestPayload = payload;
          const inputs = {
            geetest_challenge: payload.challenge,
            geetest_validate: payload.validate,
            geetest_seccode: payload.seccode,
          };
          for (const [name, value] of Object.entries(inputs)) {
            const node = document.querySelector(`input[name="${name}"]`);
            if (node) {
              node.value = value;
              node.dispatchEvent(new Event('input', { bubbles: true }));
              node.dispatchEvent(new Event('change', { bubbles: true }));
            }
          }
        }
        """,
        {
            "gt": gt,
            "challenge": challenge,
            "validate": validate,
            "seccode": seccode,
        },
    )
    return True


async def solve_zhaopin_captcha(page, settings: dict[str, Any]) -> bool:
    if not is_yescaptcha_configured(settings):
        emit_task_log(settings, "YesCaptcha is not configured; skipping automatic solving.")
        return False

    api_key = clean_text(str(settings.get("yescaptcha_api_key", "")))
    proxy = clean_text(str(settings.get("yescaptcha_proxy", "")))
    page_url = page.url
    html = await page.content()
    context = detect_captcha_context(html, page_url)
    emit_task_log(
        settings,
        f"YesCaptcha diagnostics: api_key={_mask_key(api_key)}, {describe_captcha_context(context)}",
    )

    kind = context.get("kind", "unknown")
    if kind in {"unknown", "tencent"}:
        emit_task_log(
            settings,
            "YesCaptcha did not find a directly supported token workflow for the current Zhaopin captcha page.",
        )
        return False

    if kind == "cloudflare":
        emit_task_log(
            settings,
            "Detected a Cloudflare-style challenge without a Turnstile site key; this project does not yet have the extra request metadata needed to submit CloudFlareTaskS2 safely.",
        )
        return False

    try:
        async with YesCaptchaClient(api_key) as client:
            if kind == "geetest":
                gt = clean_text(str(context.get("gt", "")))
                challenge = clean_text(str(context.get("challenge", "")))
                if not gt or not challenge:
                    emit_task_log(settings, "GeeTest markers were incomplete; cannot create a solving task.")
                    return False
                emit_task_log(settings, "YesCaptcha recognized GeeTest and is creating a solving task.")
                solution = await client.solve(
                    "GeeTestTaskProxyless",
                    websiteURL=page_url,
                    gt=gt,
                    challenge=challenge,
                )
                injected = await _inject_geetest_result(page, gt, challenge, solution)
                emit_task_log(
                    settings,
                    "YesCaptcha received a GeeTest solution." if injected else "YesCaptcha returned a GeeTest solution, but injection did not bind to the page.",
                )
                await asyncio.sleep(2)
                return injected

            if kind == "turnstile":
                site_key = clean_text(str(context.get("site_key", "")))
                if not site_key:
                    emit_task_log(settings, "Turnstile markers were found, but no site key was detected.")
                    return False
                emit_task_log(settings, "YesCaptcha recognized Turnstile and is creating a solving task.")
                solution = await client.solve(
                    "TurnstileTaskProxyless",
                    websiteURL=page_url,
                    websiteKey=site_key,
                )
                token = clean_text(
                    str(
                        solution.get("token")
                        or solution.get("gRecaptchaResponse")
                        or solution.get("cfTurnstileResponse")
                        or ""
                    )
                )
                if not token:
                    emit_task_log(settings, f"Turnstile task returned an unexpected payload: {solution}")
                    return False
                injected = await _inject_turnstile_token(page, token)
                emit_task_log(
                    settings,
                    "YesCaptcha received a Turnstile token." if injected else "YesCaptcha returned a Turnstile token, but injection did not bind to the page.",
                )
                await asyncio.sleep(2)
                return injected

            if kind == "recaptcha_v2":
                site_key = clean_text(str(context.get("site_key", "")))
                if not site_key:
                    emit_task_log(settings, "reCAPTCHA markers were found, but no site key was detected.")
                    return False
                emit_task_log(settings, "YesCaptcha recognized reCAPTCHA v2 and is creating a solving task.")
                solution = await client.solve(
                    "ReCaptchaV2TaskProxyless",
                    websiteURL=page_url,
                    websiteKey=site_key,
                )
                token = clean_text(str(solution.get("gRecaptchaResponse", "")))
                if not token:
                    emit_task_log(settings, f"reCAPTCHA task returned an unexpected payload: {solution}")
                    return False
                injected = await _inject_recaptcha_token(page, token)
                emit_task_log(
                    settings,
                    "YesCaptcha received a reCAPTCHA token." if injected else "YesCaptcha returned a reCAPTCHA token, but injection did not bind to the page.",
                )
                await asyncio.sleep(2)
                return injected

            if kind == "hcaptcha":
                site_key = clean_text(str(context.get("site_key", "")))
                if not site_key:
                    emit_task_log(settings, "hCaptcha markers were found, but no site key was detected.")
                    return False
                emit_task_log(settings, "YesCaptcha recognized hCaptcha and is creating a solving task.")
                solution = await client.solve(
                    "HCaptchaTaskProxyless",
                    websiteURL=page_url,
                    websiteKey=site_key,
                )
                token = clean_text(str(solution.get("gRecaptchaResponse", "")))
                if not token:
                    emit_task_log(settings, f"hCaptcha task returned an unexpected payload: {solution}")
                    return False
                injected = await _inject_hcaptcha_token(page, token)
                emit_task_log(
                    settings,
                    "YesCaptcha received an hCaptcha token." if injected else "YesCaptcha returned an hCaptcha token, but injection did not bind to the page.",
                )
                await asyncio.sleep(2)
                return injected

            if kind == "tencent" and proxy:
                emit_task_log(
                    settings,
                    "Tencent captcha was detected, but this project still lacks a verified YesCaptcha task mapping for that challenge type.",
                )
                return False
    except YesCaptchaError as exc:
        emit_task_log(settings, f"YesCaptcha API error: {exc}")
        return False
    except Exception as exc:
        emit_task_log(settings, f"YesCaptcha runtime error: {exc}")
        return False

    emit_task_log(settings, f"YesCaptcha does not handle captcha kind={kind} in the current build.")
    return False
