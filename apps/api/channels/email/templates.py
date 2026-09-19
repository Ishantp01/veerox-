"""HTML/plain-text bodies for Veerox transactional emails.

Table layout + inline styles only — email clients (Gmail, Outlook) ignore
``<style>`` blocks and modern CSS. Colours are light-on-white with dark text so
the message stays readable when a client forces dark mode.
"""

from __future__ import annotations

from html import escape

_BRAND = "#4f46e5"
_INK = "#0f172a"
_MUTED = "#64748b"
_LINE = "#e2e8f0"
_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _shell(preheader: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Veerox</title>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;">
<tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;border:1px solid {_LINE};border-radius:14px;">
    <tr><td style="padding:28px 32px 0 32px;font-family:{_FONT};">
      <span style="font-size:20px;font-weight:700;letter-spacing:-0.3px;color:{_BRAND};">Veerox</span>
    </td></tr>
    <tr><td style="padding:20px 32px 32px 32px;font-family:{_FONT};color:{_INK};">
      {body_html}
    </td></tr>
  </table>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;">
    <tr><td align="center" style="padding:20px 16px 0 16px;font-family:{_FONT};font-size:12px;line-height:18px;color:{_MUTED};">
      Sent by Veerox &middot; WorkAssign AI<br>
      This is an automated message, please don&rsquo;t reply.
    </td></tr>
  </table>
</td></tr>
</table>
</body>
</html>"""


def login_token_email(login_token: str, login_url: str) -> tuple[str, str]:
    """Return ``(html, plain_text)`` for the "here is your new login token" email."""
    token = escape(login_token)
    url = escape(login_url, quote=True)
    body = f"""
      <h1 style="margin:0 0 8px 0;font-size:22px;line-height:28px;font-weight:700;color:{_INK};">Your new login token</h1>
      <p style="margin:0 0 20px 0;font-size:15px;line-height:22px;color:{_MUTED};">
        Copy the token below and paste it on the Veerox sign-in page.
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;padding:16px 18px;">
          <div style="font-size:11px;letter-spacing:0.8px;text-transform:uppercase;color:{_MUTED};margin-bottom:6px;">Login token</div>
          <span style="font-family:SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;font-size:16px;line-height:24px;color:{_INK};word-break:break-all;"><b>{token}</b></span>
        </td></tr>
      </table>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 0 0;">
        <tr><td style="border-radius:8px;background:{_BRAND};">
          <a href="{url}" style="display:inline-block;padding:12px 24px;font-family:{_FONT};font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;">Go to sign in</a>
        </td></tr>
      </table>
      <hr style="border:0;border-top:1px solid {_LINE};margin:28px 0 16px 0;">
      <p style="margin:0 0 8px 0;font-size:13px;line-height:20px;color:{_MUTED};">
        <strong style="color:{_INK};">Your previous token no longer works.</strong>
        Keep this one private &mdash; anyone who has it can sign in to your account.
      </p>
      <p style="margin:0;font-size:13px;line-height:20px;color:{_MUTED};">
        Didn&rsquo;t ask for a new token? Contact your administrator right away.
      </p>"""
    html = _shell("Your new Veerox login token is inside.", body)
    text = (
        "Your new Veerox login token\n\n"
        f"{login_token}\n\n"
        f"Sign in: {login_url}\n\n"
        "Your previous token no longer works. Keep this one private.\n"
        "Didn't ask for a new token? Contact your administrator right away.\n"
    )
    return html, text
