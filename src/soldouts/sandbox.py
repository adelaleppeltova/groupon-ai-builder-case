"""HTTP adapter for the published Groupon sandbox OpenAPI contract."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://desk-production-6adc.up.railway.app/api/sandbox/v1"
SOURCE_PATHS = {
    "rep_notes": "/notes", "deal_alert_replies": "/alert-replies",
    "reviews": "/reviews", "refunds": "/refunds",
}


class SandboxError(Exception):
    """Error with a fixed user-facing message that excludes response bodies."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the credential to a redirect.


def string(value):
    return value if isinstance(value, str) and value.strip() else ""


class Sandbox:
    def __init__(self, api_key, *, open_request=None):
        self._api_key = api_key
        self._open = open_request or build_opener(NoRedirect()).open

    def request(self, method, path, *, params=None, body=None, timeout=8):
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params)
        headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=True).encode()
        if path == "/llm/v1/messages":
            headers["anthropic-version"] = "2023-06-01"
        try:
            req = Request(url, data=data, headers=headers, method=method)
            with self._open(req, timeout=timeout) as response:
                payload = json.load(response)
        except HTTPError as exc:
            status = exc.code
            exc.close()
            messages = {400: "The sandbox rejected the request.", 401: "Check GROUPON_API_KEY.",
                        403: "The sandbox denied access.", 402: "The gateway budget is exhausted.",
                        404: "The sandbox record was not found.", 429: "Sandbox rate limit reached. Try again shortly."}
            raise SandboxError(messages.get(status, f"Sandbox request failed (HTTP {status})."), status) from None
        except (URLError, OSError):
            raise SandboxError("The sandbox could not be reached. Try again shortly.") from None
        except (ValueError, UnicodeError):
            raise SandboxError("The sandbox returned unreadable JSON.") from None
        if not isinstance(payload, dict):
            raise SandboxError("The sandbox returned an unexpected response.")
        return payload

    def pages(self, path, **filters):
        data, seen = [], set()
        params = {"limit": 1000, **filters}
        first = None
        while True:
            page = self.request("GET", path, params=params)
            if not isinstance(page.get("data"), list):
                raise SandboxError("The sandbox list response is missing its data.")
            if first is None:
                first = page
            if path == "/soldouts/today" and any(
                first.get(field) != page.get(field)
                for field in ("world_day", "date")
                if field in first and field in page
            ):
                raise SandboxError("The sandbox day changed during loading. Refresh the list.")
            data.extend(page["data"])
            cursor = page.get("next_cursor")
            if cursor is None or cursor == "":
                return {**first, "data": data, "next_cursor": None}
            if not isinstance(cursor, str) or cursor in seen or len(seen) >= 100:
                raise SandboxError("The sandbox returned invalid pagination.")
            seen.add(cursor)
            params["cursor"] = cursor

    def today(self):
        return self.pages("/soldouts/today")

    def merchant_mark(self, merchant_id):
        payload = self.request("GET", merchant_path(merchant_id))
        if payload.get("merchant_id") != merchant_id or "mark" not in payload:
            raise SandboxError("The merchant mark could not be confirmed.")
        return None if payload["mark"] is None else parse_mark(payload["mark"], merchant_id)

    def set_mark(self, merchant_id, mark, reason, marked_by):
        body = {"mark": mark.strip(), "reason": reason.strip(), "marked_by": marked_by.strip()}
        if not all(body.values()) or len(body["mark"]) > 64:
            raise ValueError("Enter a mark code of up to 64 characters, a reason, and a rep ID.")
        payload = self.request("PUT", merchant_path(merchant_id) + "/mark", body=body)
        return parse_mark(payload, merchant_id)

    def remove_mark(self, merchant_id):
        try:
            result = self.request("DELETE", merchant_path(merchant_id) + "/mark")
        except SandboxError as exc:
            if exc.status != 404:
                raise
            return  # The mark is already absent.
        if result.get("merchant_id") != merchant_id or result.get("removed") is not True:
            raise SandboxError("Mark removal was not confirmed. Refresh before retrying.")


def merchant_path(merchant_id):
    if not string(merchant_id) or merchant_id in {".", ".."}:
        raise ValueError("A merchant ID is required.")
    return "/merchants/" + quote(merchant_id, safe="")


def parse_mark(payload, merchant_id):
    if (not isinstance(payload, dict) or payload.get("merchant_id") != merchant_id
            or not all(string(payload.get(field)) for field in ("mark", "reason", "marked_by"))):
        raise SandboxError("The sandbox returned incomplete mark details. Refresh to confirm.")
    return payload
