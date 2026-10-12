#!/usr/bin/env python3
"""Daily active subscribers by Sender mailing-list group; email via Resend."""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
GROUPS = {
    "Ransom Black": {"secret": "SENDER_RANSOM_API_TOKEN", "ids": {"g-tGmbER", "ep60yp"}},
    "Marlowe Rose": {"secret": "SENDER_MARLOWE_API_TOKEN", "ids": {"g-FopYjh", "dG7j3L"}},
}

def get_json(url, token):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as exc:
        # Only log recognized, non-sensitive error codes/messages. Never print raw
        # API responses, which might include subscriber data or credentials.
        safe_reason = ""
        try:
            error_body = json.loads(exc.read(4096).decode("utf-8", errors="replace"))
            if isinstance(error_body, dict):
                candidates = [error_body.get("message"), error_body.get("error")]
                for candidate in candidates:
                    if isinstance(candidate, str):
                        normalized = candidate.lower().strip()
                        allowed = (
                            "forbidden", "unauthorized", "invalid token",
                            "token expired", "access denied", "permission denied",
                            "api access disabled", "api access not available",
                            "insufficient permissions", "not allowed",
                        )
                        if normalized in allowed:
                            safe_reason = f" ({normalized})"
                            break
        except (ValueError, UnicodeError):
            pass
        raise RuntimeError(f"Sender API returned HTTP {exc.code}{safe_reason}. Check Sender API token permissions and plan access.") from exc

def subscribers(token):
    url = "https://api.sender.net/v2/subscribers?limit=100"
    seen = set()
    while url:
        if url in seen:
            raise RuntimeError("Sender pagination loop")
        seen.add(url)
        payload = get_json(url, token)
        if not isinstance(payload.get("data"), list):
            raise RuntimeError("Unexpected Sender API response")
        yield from payload["data"]
        next_url = (payload.get("links") or {}).get("next")
        if next_url and not next_url.startswith("https://api.sender.net/"):
            raise RuntimeError("Unexpected Sender pagination URL")
        url = next_url

def is_active(item):
    status = item.get("status") or {}
    value = status.get("email") if isinstance(status, dict) else status
    return str(value).lower() in {"active", "subscribed", "confirmed"}

def in_group(item, ids):
    # Sender's UI uses two tag identifiers; accept either representation.
    tags = item.get("subscriber_tags") or item.get("tags") or []
    for tag in tags:
        if isinstance(tag, dict):
            if any(str(tag.get(field)) in ids for field in ("id", "tag_id", "uid", "hash")):
                return True
        elif str(tag) in ids:
            return True
    return False

def created_date(item):
    raw = item.get("created") or item.get("created_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Sender timestamps without offsets are treated as UTC.
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ).date()
    except ValueError:
        return None

def send_report(body, subject):
    api_key = os.environ["RESEND_API_KEY"]
    payload = json.dumps({
        "from": "Daily Reports <daily@reports.ransomblackmusic.com>",
        "to": ["contact@ransomblackmusic.com"],
        "subject": subject,
        "text": body,
    }).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=payload, method="POST", headers={
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            result = json.load(res)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Resend API returned HTTP {exc.code}. Check sending-domain verification and Resend key permissions.") from exc
    if not result.get("id"):
        raise RuntimeError("Resend did not confirm email submission")

def main():
    now = datetime.now(TZ)
    # Run at 8 AM America/New_York. UTC hours 12/13 cover DST transitions.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and now.hour != 8:
        print("Not 8 AM Eastern; skipping this scheduled invocation.")
        return
    yesterday = (now - timedelta(days=1)).date()
    lines = [f"Daily Mailing List Report — {now:%B %d, %Y}", f"New subscribers on {yesterday:%B %d, %Y} (Eastern Time)", ""]
    combined_new = combined_total = 0
    for artist, cfg in GROUPS.items():
        try:
            all_items = list(subscribers(os.environ[cfg["secret"]]))
        except RuntimeError as exc:
            raise RuntimeError(f"{artist}: {exc}") from exc
        # Fail safely rather than sending misleading zeros if API tag shape differs.
        tagged = [x for x in all_items if in_group(x, cfg["ids"])]
        if all_items and not tagged:
            raise RuntimeError(f"{artist}: no group matches among {len(all_items)} contacts. Verify Sender API group identifiers/response format before emailing.")
        active = [x for x in tagged if is_active(x)]
        new = sum(created_date(x) == yesterday for x in active)
        total = len(active)
        combined_new += new
        combined_total += total
        lines += [artist, f"  New yesterday: {new}", f"  Active total: {total}", ""]
        print(f"{artist}: {new} new; {total} active (group matching checked)")
    lines += ["COMBINED TOTALS", f"  New yesterday: {combined_new}", f"  Active subscribers: {combined_total}", "", "Note: New counts use the subscriber's account creation date, not a later group-join date."]
    send_report("\n".join(lines), f"Daily subscriber report — {now:%b %d}")
    print("Report submitted to Resend successfully.")

if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        print(f"HTTP error {exc.code} from API (response withheld to avoid exposing contact data).", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"Report failed: {exc}", file=sys.stderr)
        sys.exit(1)
