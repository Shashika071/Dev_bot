"""Helper so the redaction test does not need to open a socket."""


def probe_summary(token: str) -> str:
    from app.digitmatch.redaction import safe_message_summary

    summary = safe_message_summary({"authorize": token})
    text = str(summary)
    if token in text:
        raise AssertionError("token leaked into summary")
    return "keys-only"
