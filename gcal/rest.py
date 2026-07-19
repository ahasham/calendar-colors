"""lib/gcal/rest.py — direct Google Calendar REST v3 client (transport: rest).

The second transport for the calendar silver primitive (Phase C of
gold/Projects/calendar-silver-chassis/Plan.md). The default transport is the
``mcp__claude_ai_Google_Calendar__*`` tool family (agent-as-dispatcher); this
module is the richer alternative that unblocks the two fields the MCP cannot
carry:

  * ``transparency``       — transparent time-block holds (don't block free/busy)
  * ``extendedProperties`` — machine-writable provenance + idempotency keys,
    PLUS the ``privateExtendedProperty`` list filter for event-side idempotency.

────────────────────────────────────────────────────────────────────────────
Where this slots in
────────────────────────────────────────────────────────────────────────────

``lib/gcal/api.py`` is the dispatch boundary. Callers (create_event /
update_event / delete_event / query) are UNCHANGED. When
``skill.yml transport.active == rest`` AND OAuth credentials are present,
api.py resolves a REST *invoker* — a ``(mcp_tool_name, params) -> response``
callable with the SAME signature as the test ``mcp_invoker`` — and dispatches
through it. The REST response is Google-Calendar-native, so it flows back
through ``record_mcp_result()`` and produces IDENTICAL state-cache writes +
``calendar.event_*`` bus emission as the MCP path.

If credentials are absent, ``make_invoker`` raises ``CredentialsError`` with an
actionable message; the api.py transport switch catches it and falls back to
the MCP/agent path so the working transport is never broken (graceful fallback).

────────────────────────────────────────────────────────────────────────────
Param mapping (lib-canonical → Google REST events resource)
────────────────────────────────────────────────────────────────────────────

The lib's EventIntent.params use the lib's canonical names (start/end/
calendar_id/attendees/colorId/description/transparency/extendedProperties/
recurrenceData/allDay/timeZone/location/visibility). ``to_rest_body`` maps
them onto the Google Calendar v3 ``events`` resource body:

    start / end       -> {"dateTime": <iso>, "timeZone": <tz>}  (or {"date": …} when allDay)
    recurrenceData    -> recurrence: [ "RRULE:…", … ]
    attendees[str]    -> attendees: [ {"email": …}, … ]
    colorId, description, location, summary, transparency,
    extendedProperties, visibility, reminders -> passed through verbatim

calendar_id / event_id are URL components, NOT body fields, so they're stripped
from the body and consumed by the dispatch layer.

────────────────────────────────────────────────────────────────────────────
HTTP injection (testability)
────────────────────────────────────────────────────────────────────────────

``GoogleCalendarRestClient`` takes an optional ``request_fn(method, url,
headers, body) -> (status, parsed_json)``. Production uses a urllib-based
default; tests inject a fake that returns canned Google-shaped responses so the
full transport path is exercised with zero network I/O.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


# ── Errors ────────────────────────────────────────────────────────────────────


class CredentialsError(RuntimeError):
    """OAuth credentials are absent, unreadable, or malformed.

    Raised by ``load_credentials`` / ``make_invoker`` so the api.py transport
    switch can fall back to the MCP path with a clear, actionable message
    (rather than a cryptic FileNotFoundError / KeyError deep in a dispatch).
    """


class RestError(RuntimeError):
    """A non-2xx Google Calendar REST response.

    Carries ``status`` (HTTP code) + ``response`` (parsed error body) so callers
    like the reconcile sweep can distinguish 404/410 (deleted) from transient
    failures without invoking a verdict on ambiguous responses.
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 response: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.response = response or {}


# ── Endpoints / tool routing ────────────────────────────────────────────────

_API_BASE = "https://www.googleapis.com/calendar/v3"
_TOKEN_URI_DEFAULT = "https://oauth2.googleapis.com/token"

# MCP tool name → REST operation. Mirrors the MCP_TOOL_* constants in api.py
# WITHOUT importing api (rest.py must not depend on api.py — api imports rest).
_TOOL_OP: dict[str, str] = {
    "mcp__claude_ai_Google_Calendar__create_event": "create",
    "mcp__claude_ai_Google_Calendar__update_event": "update",
    "mcp__claude_ai_Google_Calendar__delete_event": "delete",
    "mcp__claude_ai_Google_Calendar__list_events": "list",
    "mcp__claude_ai_Google_Calendar__get_event": "get",
    # NOTE: respond_to_event is intentionally absent — the Phase C REST scope is
    # insert/patch/delete/list/get. The api.py transport switch keeps RSVP
    # responses on the MCP/agent path; dispatch() raises if it ever arrives here.
}


# ── Param mapping (pure compute) ──────────────────────────────────────────────

# Lib-canonical keys that are URL components (path or query), never body fields.
_NON_BODY_KEYS = frozenset({
    "calendar_id", "calendarId", "event_id", "eventId",
    "max_results", "maxResults", "pageSize",
    "time_min", "timeMin", "time_max", "timeMax",
    "q", "fullText",
    "private_extended_property", "privateExtendedProperty",
})

# Lib-canonical keys passed through to the REST body verbatim (Google canonical
# names already). extendedProperties + transparency are the Phase C unblock.
_PASSTHROUGH_BODY_KEYS = (
    "summary", "description", "location", "colorId", "transparency",
    "visibility", "extendedProperties", "reminders", "conferenceData", "status",
)


def _to_rest_datetime(value: Any, *, tz: Optional[str], all_day: bool) -> dict[str, Any]:
    """Map a lib start/end value onto a Google REST EventDateTime node.

    - dict in  → assumed already-Google-shaped ({dateTime|date, timeZone}); copy.
    - all_day  → {"date": "YYYY-MM-DD"} (date-only, no time component).
    - else     → {"dateTime": <iso str>} (+ "timeZone" when tz given).
    """
    if isinstance(value, dict):
        return dict(value)
    s = str(value)
    if all_day:
        return {"date": s[:10]}
    node: dict[str, Any] = {"dateTime": s}
    if tz:
        node["timeZone"] = tz
    return node


def to_rest_body(operation: str, params: dict[str, Any]) -> dict[str, Any]:
    """Map lib-canonical EventIntent.params → a Google Calendar v3 events body.

    Pure compute — no MCP, no HTTP, no state. ``operation`` is informational
    (create/update); the mapping is identical for both (patch sends only the
    fields present, which is exactly how update_event builds its params).

    URL-component keys (calendar_id / event_id / list filters) are stripped.
    transparency + extendedProperties survive — that's the whole point of REST.
    """
    p = dict(params or {})
    for k in _NON_BODY_KEYS:
        p.pop(k, None)

    tz = p.pop("timeZone", None) or p.pop("time_zone", None)
    all_day = bool(p.pop("allDay", False) or p.pop("all_day", False))

    body: dict[str, Any] = {}

    for key in ("start", "end"):
        if key in p:
            body[key] = _to_rest_datetime(p.pop(key), tz=tz, all_day=all_day)

    rec = p.pop("recurrence", None) or p.pop("recurrenceData", None)
    if rec:
        body["recurrence"] = list(rec)

    att = p.pop("attendees", None)
    if att is None:
        att = p.pop("attendeeEmails", None)
    if att:
        body["attendees"] = [
            {"email": a} if isinstance(a, str) else dict(a)
            for a in att if a
        ]

    for key in _PASSTHROUGH_BODY_KEYS:
        if key in p:
            body[key] = p.pop(key)

    # Forward-compat: any unrecognized leftover keys ride through verbatim so a
    # future styled field isn't silently dropped before OAuth-day testing.
    body.update(p)
    return body


# ── Credentials ───────────────────────────────────────────────────────────────


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(str(path)))


@dataclass
class Credentials:
    """A loaded OAuth2 token for the Google Calendar API.

    Shape mirrors the google-auth ``authorized_user`` token.json written by the
    one-time authorize flow (see OAuth-setup.md): access_token + refresh_token +
    client_id + client_secret + token_uri (+ optional expiry ISO string).
    """

    access_token: str
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token_uri: str = _TOKEN_URI_DEFAULT
    expiry: Optional[str] = None
    _token_path: Optional[Path] = None

    def _is_expired(self) -> bool:
        if not self.expiry:
            return False
        try:
            exp = datetime.fromisoformat(self.expiry.replace("Z", "+00:00"))
        except ValueError:
            return False
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp

    def authorize_header(
        self, *, request_fn: Optional["RequestFn"] = None
    ) -> dict[str, str]:
        """Return the ``Authorization: Bearer …`` header, refreshing if expired.

        Refresh requires refresh_token + client_id + client_secret. If the token
        is expired and cannot be refreshed, raises CredentialsError (actionable:
        re-run the authorize flow).
        """
        if self._is_expired():
            self._refresh(request_fn=request_fn)
        if not self.access_token:
            raise CredentialsError(
                "no access_token available; re-run the one-time authorize flow "
                "(see gold/Projects/calendar-silver-chassis/OAuth-setup.md)."
            )
        return {"Authorization": f"Bearer {self.access_token}"}

    def _refresh(self, *, request_fn: Optional["RequestFn"] = None) -> None:
        if not (self.refresh_token and self.client_id and self.client_secret):
            raise CredentialsError(
                "access token expired and no refresh_token/client credentials "
                "to renew it; re-run the authorize flow (OAuth-setup.md)."
            )
        rf = request_fn or _default_request
        payload = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        status, parsed = rf(
            "POST", self.token_uri,
            {"Content-Type": "application/x-www-form-urlencoded"},
            payload,
        )
        if status < 200 or status >= 300 or not parsed.get("access_token"):
            raise CredentialsError(
                f"token refresh failed (HTTP {status}): {parsed}. Re-run the "
                "authorize flow (OAuth-setup.md)."
            )
        self.access_token = str(parsed["access_token"])
        expires_in = parsed.get("expires_in")
        if expires_in:
            self.expiry = (
                datetime.now(timezone.utc).timestamp() + float(expires_in)
            )
            self.expiry = datetime.fromtimestamp(
                self.expiry, tz=timezone.utc
            ).isoformat()
        self._persist()

    def _persist(self) -> None:
        """Write the (refreshed) token back to disk best-effort."""
        if not self._token_path:
            return
        try:
            data = {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "token_uri": self.token_uri,
                "expiry": self.expiry,
            }
            self._token_path.write_text(json.dumps(data, indent=2))
        except OSError:
            pass  # refresh still valid in-memory for this run


def creds_available(token_path: str) -> bool:
    """Cheap existence check (no parse) — does the token file exist + non-empty?

    Used by the api.py transport switch to decide REST-vs-fallback WITHOUT
    raising. A malformed-but-present file passes this check and is caught later
    by ``load_credentials`` (which raises CredentialsError → graceful fallback).
    """
    try:
        p = _expand(token_path)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def load_credentials(token_path: str) -> Credentials:
    """Load + validate OAuth credentials from ``token_path``.

    Raises CredentialsError (clear, actionable) when the file is absent,
    unreadable, not JSON, or missing the access_token field — so the MCP path
    stays the default and the user knows exactly what to provision.
    """
    p = _expand(token_path)
    if not p.is_file():
        raise CredentialsError(
            f"Google Calendar OAuth token not found at {p}. The REST transport "
            "is selected (transport.active: rest) but no credentials are "
            "provisioned. Follow gold/Projects/calendar-silver-chassis/"
            "OAuth-setup.md to create an OAuth client and authorize a token, "
            "or set transport.active: mcp to use the MCP transport."
        )
    try:
        raw = p.read_text()
    except OSError as e:
        raise CredentialsError(f"cannot read token file {p}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CredentialsError(f"token file {p} is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise CredentialsError(f"token file {p} must contain a JSON object.")
    access_token = data.get("access_token") or data.get("token") or ""
    if not access_token and not data.get("refresh_token"):
        raise CredentialsError(
            f"token file {p} has neither access_token nor refresh_token; "
            "re-run the authorize flow (OAuth-setup.md)."
        )
    return Credentials(
        access_token=str(access_token),
        refresh_token=data.get("refresh_token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        token_uri=str(data.get("token_uri") or _TOKEN_URI_DEFAULT),
        expiry=data.get("expiry"),
        _token_path=p,
    )


# ── HTTP transport ──────────────────────────────────────────────────────────

RequestFn = Callable[[str, str, dict[str, str], Optional[bytes]],
                     "tuple[int, dict[str, Any]]"]


def _default_request(
    method: str, url: str, headers: dict[str, str], body: Optional[bytes] = None
) -> "tuple[int, dict[str, Any]]":
    """urllib-based HTTP. Returns (status, parsed_json). Raises RestError on
    HTTP errors with the parsed error body attached."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (Google host)
            raw = resp.read()
            status = resp.getcode() or 0
            parsed = json.loads(raw) if raw else {}
            return status, parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw.decode("utf-8", "replace")}
        raise RestError(
            f"{method} {url} -> HTTP {e.code}", status=e.code, response=parsed
        ) from e
    except urllib.error.URLError as e:
        raise RestError(f"{method} {url} -> network error: {e.reason}") from e


# ── REST client ───────────────────────────────────────────────────────────────


class GoogleCalendarRestClient:
    """Thin Google Calendar v3 client: insert / patch / delete / list / get.

    ``dispatch(tool_name, params)`` is the invoker entrypoint — same signature
    as the test mcp_invoker — so api.py can route through it transparently.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        request_fn: Optional[RequestFn] = None,
    ) -> None:
        self.credentials = credentials
        self._request = request_fn or _default_request

    # -- low-level ----------------------------------------------------------

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        h = dict(self.credentials.authorize_header(request_fn=self._request))
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _events_url(self, calendar_id: str, event_id: Optional[str] = None) -> str:
        cal = urllib.parse.quote(calendar_id or "primary", safe="")
        base = f"{_API_BASE}/calendars/{cal}/events"
        if event_id:
            return f"{base}/{urllib.parse.quote(event_id, safe='')}"
        return base

    # -- operations ---------------------------------------------------------

    def insert_event(self, calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
        url = self._events_url(calendar_id)
        status, parsed = self._request(
            "POST", url, self._headers(json_body=True),
            json.dumps(body).encode(),
        )
        if status < 200 or status >= 300:
            raise RestError(f"insert_event HTTP {status}", status=status, response=parsed)
        return parsed

    def patch_event(
        self, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        if not event_id:
            raise RestError("patch_event requires an event_id")
        url = self._events_url(calendar_id, event_id)
        status, parsed = self._request(
            "PATCH", url, self._headers(json_body=True),
            json.dumps(body).encode(),
        )
        if status < 200 or status >= 300:
            raise RestError(f"patch_event HTTP {status}", status=status, response=parsed)
        return parsed

    def delete_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        if not event_id:
            raise RestError("delete_event requires an event_id")
        url = self._events_url(calendar_id, event_id)
        status, parsed = self._request(
            "DELETE", url, self._headers(json_body=False), None,
        )
        # 204 No Content is the success case; 200 also acceptable.
        if status not in (200, 204):
            raise RestError(f"delete_event HTTP {status}", status=status, response=parsed)
        return {"id": event_id, "calendar_id": calendar_id, "deleted": True}

    def get_event(self, calendar_id: str, event_id: str) -> dict[str, Any]:
        if not event_id:
            raise RestError("get_event requires an event_id")
        url = self._events_url(calendar_id, event_id)
        status, parsed = self._request(
            "GET", url, self._headers(json_body=False), None,
        )
        if status < 200 or status >= 300:
            raise RestError(f"get_event HTTP {status}", status=status, response=parsed)
        return parsed

    def list_events(
        self,
        calendar_id: str,
        *,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        q: Optional[str] = None,
        private_extended_property: Optional[Any] = None,
        max_results: Optional[int] = None,
        page_token: Optional[str] = None,
    ) -> dict[str, Any]:
        """events.list with the Phase-C ``privateExtendedProperty`` filter.

        ``private_extended_property`` accepts a "k=v" string or a list of them
        (Google ANDs repeated params). This is the event-side idempotency query
        that retires the plan-week state-map workaround. ``page_token`` carries
        a ``nextPageToken`` for callers that paginate (the response always
        surfaces ``nextPageToken`` when more pages exist).
        """
        query: list[tuple[str, str]] = []
        if time_min:
            query.append(("timeMin", time_min))
        if time_max:
            query.append(("timeMax", time_max))
        if q:
            query.append(("q", q))
        if max_results:
            query.append(("maxResults", str(max_results)))
        if page_token:
            query.append(("pageToken", str(page_token)))
        if private_extended_property:
            pep = private_extended_property
            if isinstance(pep, str):
                pep = [pep]
            for kv in pep:
                query.append(("privateExtendedProperty", str(kv)))
        url = self._events_url(calendar_id)
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        status, parsed = self._request(
            "GET", url, self._headers(json_body=False), None,
        )
        if status < 200 or status >= 300:
            raise RestError(f"list_events HTTP {status}", status=status, response=parsed)
        return parsed

    # -- invoker entrypoint -------------------------------------------------

    def dispatch(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Route an MCP-tool-named intent through the matching REST operation.

        Same ``(tool_name, params) -> response`` contract as the test
        mcp_invoker, so api.py can feed the response straight into
        ``record_mcp_result``. Returns Google-Calendar-native shapes (insert/
        patch/get return the events resource; list returns {"items": [...]}).
        """
        op = _TOOL_OP.get(tool_name)
        if op is None:
            raise RestError(
                f"REST transport does not handle {tool_name!r}; supported tools: "
                f"{sorted(_TOOL_OP)}. (respond_to_event stays on the MCP path.)"
            )
        calendar_id = (
            params.get("calendar_id") or params.get("calendarId") or "primary"
        )
        if op == "create":
            return self.insert_event(calendar_id, to_rest_body("create", params))
        if op == "update":
            event_id = params.get("event_id") or params.get("eventId") or ""
            return self.patch_event(
                calendar_id, event_id, to_rest_body("update", params)
            )
        if op == "delete":
            event_id = params.get("event_id") or params.get("eventId") or ""
            return self.delete_event(calendar_id, event_id)
        if op == "get":
            event_id = params.get("event_id") or params.get("eventId") or ""
            return self.get_event(calendar_id, event_id)
        if op == "list":
            return self.list_events(
                calendar_id,
                time_min=params.get("time_min") or params.get("timeMin"),
                time_max=params.get("time_max") or params.get("timeMax"),
                q=params.get("q") or params.get("fullText"),
                private_extended_property=(
                    params.get("private_extended_property")
                    or params.get("privateExtendedProperty")
                ),
                max_results=params.get("max_results") or params.get("maxResults"),
                page_token=params.get("page_token") or params.get("pageToken"),
            )
        raise RestError(f"unhandled REST operation {op!r}")  # pragma: no cover


# ── Invoker factory ───────────────────────────────────────────────────────────


def make_invoker(
    token_path: str,
    *,
    request_fn: Optional[RequestFn] = None,
    client: Optional[GoogleCalendarRestClient] = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Build a ``(mcp_tool_name, params) -> response`` REST invoker.

    Loads + validates credentials from ``token_path`` (raises CredentialsError
    if absent/malformed — the api.py switch catches it for graceful fallback).
    ``client`` lets tests inject a pre-built client; ``request_fn`` lets them
    inject a fake HTTP transport.
    """
    if client is None:
        creds = load_credentials(token_path)
        client = GoogleCalendarRestClient(creds, request_fn=request_fn)
    return client.dispatch
