import re

with open('backend/routers/upload.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Pattern captures from "Scheduled upload" down to before "mime, _ = mimetypes"
pattern = r'    # ── Scheduled upload ──.*?    # ── Immediate upload ──(.*?)(?=    mime, _ = mimetypes)'
match = re.search(pattern, text, re.DOTALL)
if not match:
    print("Match not found!")
else:
    old_text = match.group(0)
    new_text = """    # ── Upload Configuration ──────────────────────────────────────────────
    check_quota(body.youtube_account, 1600)
    youtube = get_youtube_service(body.youtube_account)
    request_body = {
        "snippet": {
            "title": (body.title or "Untitled")[:100],
            "description": body.description[:5000],
            "tags": body.tags[:30],
            "categoryId": "22",
        },
        "status": {"privacyStatus": body.privacy},
    }

    if body.scheduled_at:
        try:
            import datetime
            datetime.datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at. Use ISO 8601 format.")
        request_body["status"]["privacyStatus"] = "private"
        request_body["status"]["publishAt"] = body.scheduled_at
"""
    # Fix the newlines to match the file (it probably is CRLF judging by Windows OS)
    # the replace() is robust enough
    new_text = new_text.replace("\n", "\r\n") if "\r\n" in old_text else new_text
    
    text = text.replace(old_text, new_text)

    with open('backend/routers/upload.py', 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print("Successfully replaced.")
