"""Job completion email reports via Google SMTP (app password).

Sends a styled HTML report (with a plain-text alternative) so nested Drive
paths read as breadcrumbs instead of one long line. Never raises.
"""

from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from config import get_settings

log = logging.getLogger(__name__)

# Palette mirrors the web app (globals.css).
_INK = "#17211f"
_MUTED = "#6d7771"
_PAPER = "#f3f2ed"
_CARD = "#fbfbf8"
_LINE = "#d9ddd6"
_MINT = "#b9e8ce"
_GREEN = "#246b52"
_CORAL = "#e9765b"
_FAIL_BG = "#f8d9cf"
_FAIL_INK = "#9b3c27"

_KIND_STYLE = {
    "uploaded": ("UPLOADED", _GREEN, _MINT),
    "replaced": ("REPLACED", _GREEN, "#e9eee8"),
    "failed": ("FAILED", _FAIL_INK, _FAIL_BG),
}


def send_upload_report(
    to: str,
    subject: str,
    entries: list[dict],
    *,
    date: str = "",
    final_state: str = "",
) -> bool:
    """Send an HTML upload report (plain-text fallback included).

    Each entry: ``kind`` (uploaded/replaced/failed/…), ``file``, ``path``,
    ``url``, optional ``detail``, ``account``, ``task_type``.
    Never raises — returns False on failure.
    """
    settings = get_settings()
    recipient = (to or "").strip() or settings.email_fallback_to
    if not recipient:
        log.warning("no email recipient configured; skipping upload report")
        return False
    if not settings.smtp_app_password:
        log.warning("SMTP_APP_PASSWORD not set; skipping upload report")
        return False

    items = [e for e in entries if isinstance(e, dict)]
    plain = _render_plain(items)
    body_html = _render_html(items, date=date, final_state=final_state)

    message = EmailMessage()
    message["From"] = settings.smtp_from or settings.smtp_user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(body_html, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_app_password)
            server.send_message(message)
        log.info(
            "upload report emailed to %s (%d entr%s)",
            recipient,
            len(items),
            "y" if len(items) == 1 else "ies",
        )
        return True
    except Exception:
        log.exception("failed to send upload report to %s", recipient)
        return False


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _kind_of(entry: dict) -> str:
    return str(entry.get("kind") or "").strip().lower() or "info"


def _breadcrumbs(path: str) -> str:
    parts = [p.strip() for p in (path or "").split("/") if p.strip()]
    if not parts:
        return f'<span style="color:{_MUTED};">No folder path</span>'
    rendered = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        color = _GREEN if last else _MUTED
        weight = "700" if last else "400"
        rendered.append(
            f'<span style="color:{color};font-weight:{weight};">{_esc(part)}</span>'
        )
    return '<span style="color:#b0b7b2;padding:0 6px;">/</span>'.join(rendered)


def _stats(items: list[dict]) -> dict[str, int]:
    counts = {"uploaded": 0, "replaced": 0, "failed": 0, "other": 0}
    for entry in items:
        kind = _kind_of(entry)
        if kind in ("uploaded", "replaced", "failed"):
            counts[kind] += 1
        else:
            counts["other"] += 1
    return counts


def _render_plain(items: list[dict]) -> str:
    if not items:
        return "No files were processed."
    lines = []
    for entry in items:
        kind = _kind_of(entry).upper()
        file_name = entry.get("file") or entry.get("account") or "file"
        path = (entry.get("path") or "").strip()
        url = (entry.get("url") or "").strip()
        detail = (entry.get("detail") or "").strip()
        if kind == "FAILED":
            where = f"{entry.get('account', '')}/{entry.get('task_type', '')}"
            lines.append(f"FAILED: {where} — {detail or 'unknown error'}")
        elif path and url:
            lines.append(f"{kind}: {file_name} → {path} ({url})")
        elif path:
            lines.append(f"{kind}: {file_name} → {path}")
        else:
            lines.append(f"{kind}: {file_name}{f' — {detail}' if detail else ''}")
    return "\n".join(f"• {line}" for line in lines)


def _render_html(items: list[dict], *, date: str, final_state: str) -> str:
    counts = _stats(items)
    total = len(items)
    state_label = {
        "done": "All done",
        "done_with_errors": "Finished with errors",
        "failed": "Failed",
    }.get(final_state, "Finished")
    accent = _CORAL if final_state in ("done_with_errors", "failed") else _MINT

    stat_cells = []
    for label, value, bg, fg in (
        ("Uploaded", counts["uploaded"], _MINT, _GREEN),
        ("Replaced", counts["replaced"], "#e9eee8", _GREEN),
        ("Failed", counts["failed"], _FAIL_BG, _FAIL_INK),
        ("Total", total, "#e9eee8", _INK),
    ):
        if value <= 0 and label in ("Replaced",):
            continue
        stat_cells.append(
            f'<td style="padding:0 6px 0 0;">'
            f'<div style="background:{bg};color:{fg};padding:10px 14px;text-align:center;'
            f'font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
            f'border:1px solid {_LINE};">'
            f'<span style="font-family:Georgia,serif;font-size:20px;font-weight:400;'
            f'display:block;letter-spacing:0;">{value}</span>{label}'
            f"</div></td>"
        )

    if not items:
        body_rows = (
            f'<tr><td style="padding:8px 0 4px;">'
            f'<p style="color:{_MUTED};font-size:13px;margin:0;">'
            f"No files were processed in this job.</p></td></tr>"
        )
    else:
        body_rows = "".join(_entry_card(entry) for entry in items)

    date_line = _esc(date) if date else "—"
    return f"""<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:{_PAPER};-webkit-text-size-adjust:100%;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_PAPER};font-family:Arial,Helvetica,sans-serif;">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background:{_CARD};border:1px solid {_LINE};">

<tr><td style="background:{_INK};padding:28px 28px 24px;border-bottom:4px solid {accent};">
  <p style="margin:0;font-size:10px;letter-spacing:.18em;text-transform:uppercase;color:{_MINT};font-weight:700;">Canva &rarr; Drive</p>
  <h1 style="margin:10px 0 0;font-family:Georgia,'Times New Roman',serif;font-size:30px;font-weight:400;color:#ffffff;letter-spacing:-.02em;">Upload report</h1>
  <p style="margin:10px 0 0;color:#9aa39d;font-size:13px;">{date_line} &nbsp;&middot;&nbsp; {_esc(state_label)} &nbsp;&middot;&nbsp; {total} file{"s" if total != 1 else ""}</p>
</td></tr>

<tr><td style="padding:22px 28px 6px;">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>{"".join(stat_cells)}</tr></table>
</td></tr>

<tr><td style="padding:14px 28px 4px;">
  {body_rows}
</td></tr>

<tr><td style="padding:18px 28px 26px;border-top:1px solid {_LINE};margin-top:8px;">
  <p style="margin:0;color:{_MUTED};font-size:11px;line-height:1.5;">
    Sent by the Canva &rarr; Drive control desk &middot; Ubuntu worker<br/>
    Folders nest as root / business / month / content type — open any file to view it in Drive.
  </p>
</td></tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _entry_card(entry: dict) -> str:
    kind = _kind_of(entry)
    label, accent, badge_bg = _KIND_STYLE.get(kind, (kind.upper() or "INFO", _MUTED, "#e9eee8"))
    file_name = entry.get("file") or entry.get("account") or "file"
    path = (entry.get("path") or "").strip()
    url = (entry.get("url") or "").strip()
    detail = (entry.get("detail") or "").strip()
    account = (entry.get("account") or "").strip()
    task_type = (entry.get("task_type") or "").strip()

    if kind == "failed":
        subtitle = f"{_esc(account)}/{_esc(task_type)}" if account or task_type else ""
        error_line = (
            f'<p style="margin:6px 0 0;font-size:13px;color:{_FAIL_INK};line-height:1.5;">'
            f"{_esc(detail or "unknown error")}</p>"
        )
        path_line = f'<p style="margin:6px 0 0;font-size:12px;">{_breadcrumbs(path)}</p>' if path else ""
        button = ""
    else:
        subtitle = _breadcrumbs(path)
        error_line = f'<p style="margin:6px 0 0;font-size:12px;color:{_MUTED};">{_esc(detail)}</p>' if detail else ""
        path_line = ""
        button = (
            f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px;"><tr>'
            f'<td style="background:{_INK};">'
            f'<a href="{_esc(url)}" style="display:inline-block;padding:10px 18px;color:#ffffff;'
            f'text-decoration:none;font-size:12px;font-weight:700;letter-spacing:.04em;">'
            f"Open in Drive &rarr;</a></td></tr></table>"
            if url.startswith("http")
            else ""
        )

    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 12px;border:1px solid {_LINE};background:#ffffff;border-left:3px solid {accent};">
<tr><td style="padding:16px 18px;">
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:8px;"><tr>
    <td style="background:{badge_bg};color:{accent};font-size:10px;font-weight:700;letter-spacing:.12em;padding:4px 9px;text-transform:uppercase;">{_esc(label)}</td>
    <td style="padding-left:10px;font-size:15px;font-weight:700;color:{_INK};">{_esc(file_name)}</td>
  </tr></table>
  <p style="margin:0;font-size:12px;line-height:1.7;">{subtitle}</p>
  {path_line}
  {error_line}
  {button}
</td></tr>
</table>"""
