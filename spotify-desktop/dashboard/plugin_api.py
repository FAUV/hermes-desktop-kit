"""Secret-free Spotify Web API projection for the standalone Desktop plugin.

This module deliberately owns neither OAuth nor tokens. SpotifyClient remains
responsible for credential resolution, its locked refresh, and one 401
refresh/retry. This surface projects only renderer-safe fields and stable errors.
"""

from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
)

try:
    from plugins.spotify.client import (
        SpotifyAPIError,
        SpotifyAuthRequiredError,
        SpotifyClient,
    )
except ImportError:  # pragma: no cover
    SpotifyClient = None  # type: ignore[assignment,misc]
    SpotifyAPIError = RuntimeError  # type: ignore[assignment,misc]
    SpotifyAuthRequiredError = RuntimeError  # type: ignore[assignment,misc]

router = APIRouter()
ClientFactory = Callable[[], Any]
_client_factory: ClientFactory | None = SpotifyClient
FailureCategory = Literal[
    "not_authenticated",
    "premium_required",
    "no_active_device",
    "restricted_device",
    "rate_limited",
    "quota_exceeded",
    "unavailable",
]
# Static capability declaration: what this plugin surface supports. The
# optional features (artist_albums, preview_url) are probed per-request by
# /capabilities/probe and returned in that response only — probing never
# mutates shared state (a Desktop profile must not be able to flip another
# profile's advertised capabilities).
_CAPABILITIES: dict[str, bool] = {
    "status": True,
    "playback": True,
    "devices": True,
    "queue": True,
    "search": True,
    "album": True,
    "library": True,
    "playlists": True,
    "artist_albums": False,
    "preview_url": False,
}


class Failure(BaseModel):
    ok: Literal[False] = False
    category: FailureCategory
    retry_after_seconds: int | None = None


class TransferRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=256)
    play: bool = False


class QueueRequest(BaseModel):
    uri: str = Field(min_length=1, max_length=512)
    device_id: str | None = Field(default=None, max_length=256)


RepeatState = Literal["off", "context", "track"]

# StrictInt rejects booleans (JSON true/false) and floats; Field bounds the range.
PositionMs = Annotated[StrictInt, Field(ge=0)]
VolumePercent = Annotated[StrictInt, Field(ge=0, le=100)]


class PlaybackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: StrictStr | None = Field(default=None, min_length=1, max_length=256)
    position_ms: PositionMs | None = None
    volume_percent: VolumePercent | None = None
    state: StrictBool | RepeatState | None = None
    context_uri: StrictStr | None = Field(default=None, max_length=512)
    uris: list[Annotated[str, Field(min_length=1, max_length=512)]] | None = Field(
        default=None, max_length=100
    )


class ProbeRequest(BaseModel):
    feature: Literal["artist_albums", "preview_url"]
    artist_id: str | None = Field(default=None, max_length=256)
    track_id: str | None = Field(default=None, max_length=256)


class LibraryItemsRequest(BaseModel):
    kind: Literal["track", "album", "artist"]
    ids: list[str] = Field(min_length=1, max_length=50)
    saved: bool


class PlaylistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    public: bool = False
    collaborative: bool = False
    description: str | None = Field(default=None, max_length=300)


class PlaylistDetailsRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    public: bool | None = None
    collaborative: bool | None = None
    description: str | None = Field(default=None, max_length=300)


class PlaylistItemsRequest(BaseModel):
    action: Literal["add", "remove", "reorder"]
    uris: list[str] = Field(default_factory=list, max_length=100)
    position: int | None = Field(default=None, ge=0)
    range_start: int | None = Field(default=None, ge=0)
    insert_before: int | None = Field(default=None, ge=0)
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=256)


def _client() -> Any:
    if _client_factory is None:
        raise RuntimeError("Spotify client unavailable")
    return _client_factory()


def _data_client() -> Any:
    """Client construction for data endpoints, sanitized through _failure().

    SpotifyClient.__init__ raises SpotifyAuthRequiredError when credentials
    cannot resolve; routing that through the projection boundary keeps the
    401 not_authenticated contract on every data endpoint (F1).
    """
    try:
        return _client()
    except Exception as exc:  # noqa: BLE001 -- sanitize factory errors too
        raise _failure(exc) from None


def _safe_text(value: object) -> str:
    return str(value).lower()[:500]


def _retry_after(detail: str) -> int | None:
    import re

    match = re.search(r"retry after\s+(\d+)\s+seconds", detail)
    return int(match.group(1)) if match else None


def _failure(exc: Exception, *, path: str = "") -> HTTPException:
    if isinstance(exc, SpotifyAuthRequiredError):
        return HTTPException(401, Failure(category="not_authenticated").model_dump())
    if isinstance(exc, SpotifyAPIError):
        status = getattr(exc, "status_code", None) or 503
        detail = _safe_text(getattr(exc, "response_body", "") or exc)
        if status == 429:
            category: FailureCategory = (
                "quota_exceeded" if "quota_exceeded" in detail else "rate_limited"
            )
            retry = _retry_after(detail)
        elif status == 403 and "restricted" in detail:
            category, retry = "restricted_device", None
        elif status in (403, 404) and (
            path.startswith("/me/player")
            or (getattr(exc, "path", "") or "").startswith("/me/player")
        ):
            category, retry = (
                (
                    "no_active_device"
                    if "no active" in detail or status == 404
                    else "premium_required"
                ),
                None,
            )
        elif status == 403:
            category, retry = "premium_required", None
        else:
            category, retry = "unavailable", None
        return HTTPException(
            status, Failure(category=category, retry_after_seconds=retry).model_dump()
        )
    return HTTPException(503, Failure(category="unavailable").model_dump())


def _call(fn: Callable[..., Any], *args: Any, path: str = "", **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- sanitize upstream errors at projection boundary
        raise _failure(exc, path=path) from None


def _images(obj: object) -> list[dict[str, Any]]:
    return [
        {"url": i.get("url"), "width": i.get("width"), "height": i.get("height")}
        for i in (obj or [])
        if isinstance(i, dict) and isinstance(i.get("url"), str)
    ]


def _artist(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {"id": raw.get("id"), "name": raw.get("name"), "uri": raw.get("uri")}


def _track(value: object) -> dict[str, Any] | None:
    raw = value if isinstance(value, dict) else None
    if not raw:
        return None
    album = raw.get("album") if isinstance(raw.get("album"), dict) else {}
    return {
        "id": raw.get("id"),
        "uri": raw.get("uri"),
        "name": raw.get("name"),
        "duration_ms": raw.get("duration_ms"),
        "explicit": raw.get("explicit"),
        "artists": [_artist(a) for a in raw.get("artists", []) if isinstance(a, dict)],
        "album": {
            "id": album.get("id"),
            "name": album.get("name"),
            "images": _images(album.get("images")),
        },
    }


def _device(value: object) -> dict[str, Any]:
    """Project only renderer-safe device fields and explicit UI capabilities."""
    raw = value if isinstance(value, dict) else {}
    restricted = bool(raw.get("is_restricted"))
    device_id = raw.get("id")
    volume = raw.get("volume_percent")
    return {
        "id": device_id,
        "name": raw.get("name"),
        "type": raw.get("type"),
        "is_active": bool(raw.get("is_active")),
        "is_restricted": restricted,
        "volume_percent": volume,
        # The Spotify API accepts exactly one device ID for transfer. Volume is
        # shown only where the API exposed a numeric device volume and the
        # device is not marked restricted; the renderer still handles typed
        # mutation failures by disabling and resyncing.
        "can_transfer": bool(device_id) and not restricted,
        "can_adjust_volume": (
            bool(device_id)
            and not restricted
            and isinstance(volume, int)
            and not isinstance(volume, bool)
        ),
    }


def _page(value: object, project_item: Callable[[object], Any] | None = None) -> dict[str, Any]:
    """Project a Spotify paging object to the renderer-safe page envelope.

    Every item passes through ``project_item`` so raw API objects never reach
    the renderer, even inside paging envelopes.
    """
    raw = value if isinstance(value, Mapping) else {}
    items = raw.get("items")
    safe_items = items if isinstance(items, list) else []
    if project_item is not None:
        safe_items = [
            projected
            for item in safe_items
            if (projected := project_item(item)) is not None
        ]
    return {
        "items": safe_items,
        "next": raw.get("next") if isinstance(raw.get("next"), str) else None,
        "total": raw.get("total") if isinstance(raw.get("total"), int) else None,
        "limit": raw.get("limit") if isinstance(raw.get("limit"), int) else None,
        "offset": raw.get("offset") if isinstance(raw.get("offset"), int) else None,
    }


def _album_summary(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "uri": raw.get("uri"),
        "album_type": raw.get("album_type"),
        "release_date": raw.get("release_date"),
        "images": _images(raw.get("images")),
        "artists": [_artist(a) for a in raw.get("artists", []) if isinstance(a, dict)],
    }


def _playlist_summary(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    owner = raw.get("owner") if isinstance(raw.get("owner"), Mapping) else {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "uri": raw.get("uri"),
        "description": raw.get("description"),
        "public": raw.get("public"),
        "collaborative": raw.get("collaborative"),
        "owner": {"id": owner.get("id"), "display_name": owner.get("display_name")},
        "images": _images(raw.get("images")),
        "external_urls": _external_urls(raw.get("external_urls")),
        "tracks": _page(raw.get("tracks"), _playlist_track_entry),
    }


def _playlist_track_entry(value: object) -> dict[str, Any]:
    """Project one playlist-items entry ({added_at, track})."""
    raw = value if isinstance(value, Mapping) else {}
    return {"added_at": raw.get("added_at"), "track": _track(raw.get("track"))}


def _playlist_detail(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    detail = _playlist_summary(raw)
    snapshot_id = raw.get("snapshot_id")
    detail["snapshot_id"] = (
        snapshot_id if isinstance(snapshot_id, str) else None
    )
    return detail


def _external_urls(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {"spotify": raw.get("spotify") if isinstance(raw.get("spotify"), str) else None}


def _album_detail(value: object) -> dict[str, Any]:
    """Full album projection: summary fields plus the bounded track page."""
    raw = value if isinstance(value, Mapping) else {}
    detail = _album_summary(raw)
    detail["tracks"] = _page(raw.get("tracks"), _track)
    detail["external_urls"] = _external_urls(raw.get("external_urls"))
    return detail


def _search_results(payload: object) -> dict[str, Any]:
    raw = payload if isinstance(payload, Mapping) else {}
    return {
        "tracks": _page(raw.get("tracks"), _track),
        "artists": _page(raw.get("artists"), _artist),
        "albums": _page(raw.get("albums"), _album_summary),
        "playlists": _page(raw.get("playlists"), _playlist_summary),
    }


def _playback(payload: object) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    if raw.get("empty") or raw.get("status_code") == 204:
        return {"ok": True, "idle": True, "item": None, "device": None}
    return {
        "ok": True,
        "idle": False,
        "is_playing": raw.get("is_playing"),
        "progress_ms": raw.get("progress_ms"),
        "timestamp": raw.get("timestamp"),
        "shuffle_state": raw.get("shuffle_state"),
        "repeat_state": raw.get("repeat_state"),
        "item": _track(raw.get("item")),
        "device": _device(raw.get("device")),
    }


@router.get("/status")
def status() -> dict[str, Any]:
    # NOTE on semantics: constructing SpotifyClient() resolves/refreshes stored
    # credentials but performs NO Spotify API call, so it proves credential
    # availability — not that the API currently accepts them. The renderer
    # treats this as "credentials available" and lets the first real data call
    # (e.g. /playback) surface any 401/expiry honestly; we do not burn API
    # quota polling just to decorate this state.
    try:
        _client()
    except Exception as exc:  # noqa: BLE001 -- status must hide auth resolution details
        if isinstance(exc, SpotifyAuthRequiredError):
            return {
                "ok": True,
                "plugin": "spotify-desktop",
                "auth": {"state": "not_authenticated"},
                "capabilities": {key: key == "status" for key in _CAPABILITIES},
            }
        raise _failure(exc)
    return {
        "ok": True,
        "plugin": "spotify-desktop",
        "auth": {
            "state": "credentials_available",
            "verified": False,
            "detail": "Spotify credentials resolved locally; not yet verified against the Spotify API.",
        },
        "capabilities": dict(_CAPABILITIES),
    }


@router.get("/playback")
def playback() -> dict[str, Any]:
    return _playback(_call(_data_client().get_playback_state, path="/me/player"))


@router.get("/devices")
def devices() -> dict[str, Any]:
    raw = _call(_data_client().get_devices, path="/me/player/devices")
    return {
        "ok": True,
        "devices": [
            _device(d)
            for d in (raw.get("devices", []) if isinstance(raw, dict) else [])
            if isinstance(d, dict)
        ],
    }


@router.post("/transfer")
def transfer(body: TransferRequest) -> dict[str, Any]:
    _call(
        _data_client().transfer_playback,
        device_id=body.device_id,
        play=body.play,
        path="/me/player",
    )
    return {"ok": True}


@router.get("/queue")
def queue() -> dict[str, Any]:
    raw = _call(_data_client().get_queue, path="/me/player/queue")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "ok": True,
        "currently_playing": _track(raw.get("currently_playing")),
        "queue": [_track(t) for t in raw.get("queue", []) if _track(t)],
    }


@router.post("/queue")
def add_queue(body: QueueRequest) -> dict[str, Any]:
    _call(
        _data_client().add_to_queue,
        uri=body.uri,
        device_id=body.device_id,
        path="/me/player/queue",
    )
    return {"ok": True}


def _invalid_body() -> HTTPException:
    return HTTPException(422, Failure(category="unavailable").model_dump())


def _validate_playback_body(action: str, body: PlaybackRequest) -> None:
    """Reject invalid action/body combinations before any Spotify call.

    Strict types (StrictBool/StrictInt/StrictStr) already fail wrong-typed
    values at parse time; this catches missing or semantically invalid
    combinations per action.
    """
    if action == "seek" and body.position_ms is None:
        raise _invalid_body()
    if action == "volume" and body.volume_percent is None:
        raise _invalid_body()
    if action == "shuffle" and (body.state is None or not isinstance(body.state, bool)):
        # Only a real boolean is accepted (strings "true"/"false" are not).
        raise _invalid_body()
    if action == "repeat" and body.state not in ("off", "context", "track"):
        raise _invalid_body()
    if action == "play":
        has_context = body.context_uri is not None
        has_uris = body.uris is not None
        if has_context and has_uris:
            raise _invalid_body()
        if body.uris is not None and (
            not body.uris or any(not uri.startswith("spotify:") for uri in body.uris)
        ):
            raise _invalid_body()
        if has_context and not body.context_uri.startswith("spotify:"):
            raise _invalid_body()


@router.post("/playback/{action}")
def playback_action(
    action: Literal[
        "play", "pause", "next", "previous", "seek", "volume", "shuffle", "repeat"
    ],
    body: PlaybackRequest,
) -> dict[str, Any]:
    # Validate the action/body combination BEFORE constructing any client or
    # touching Spotify: wrong-typed or missing action inputs must fail here.
    _validate_playback_body(action, body)
    client = _data_client()
    calls: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {
        "play": (
            client.start_playback,
            {
                "device_id": body.device_id,
                "context_uri": body.context_uri,
                "uris": body.uris,
                "position_ms": body.position_ms,
            },
        ),
        "pause": (client.pause_playback, {"device_id": body.device_id}),
        "next": (client.skip_next, {"device_id": body.device_id}),
        "previous": (client.skip_previous, {"device_id": body.device_id}),
        "seek": (
            client.seek,
            {"device_id": body.device_id, "position_ms": body.position_ms},
        ),
        "volume": (
            client.set_volume,
            {"device_id": body.device_id, "volume_percent": body.volume_percent},
        ),
        "shuffle": (
            client.set_shuffle,
            {"device_id": body.device_id, "state": body.state},
        ),
        "repeat": (
            client.set_repeat,
            {"device_id": body.device_id, "state": body.state},
        ),
    }
    fn, kwargs = calls[action]
    _call(fn, **kwargs, path=f"/me/player/{action}")
    return {"ok": True}


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=10),
) -> dict[str, Any]:
    raw = _call(
        _data_client().search,
        query=q,
        search_types=["track", "artist", "album", "playlist"],
        limit=limit,
        offset=offset,
        path="/search",
    )
    return {"ok": True, "results": _search_results(raw)}


@router.get("/home/recently-played")
def recently_played(limit: int = Query(default=10, ge=1, le=10)) -> dict[str, Any]:
    """Return the only recent-history shelf used by Home, with a hard quota bound."""
    raw = _call(
        _data_client().get_recently_played,
        limit=limit,
        path="/home/recently-played",
    )
    page = raw if isinstance(raw, dict) else {}
    # Preserve only fields the renderer needs; never make Home depend on catalog
    # popularity/follower values that may be omitted by the API.
    return {
        "ok": True,
        "items": [
            {"played_at": item.get("played_at"), "track": _track(item.get("track"))}
            for item in page.get("items", [])
            if isinstance(item, dict) and _track(item.get("track"))
        ],
        "next": page.get("next"),
    }


@router.get("/albums/{album_id}")
def album(album_id: str) -> dict[str, Any]:
    raw = _call(_data_client().get_album, album_id=album_id, path=f"/albums/{album_id}")
    return {"ok": True, "album": _album_detail(raw)}


@router.get("/library/contains")
def library_contains(
    kind: Literal["track", "album", "artist"],
    ids: list[str] = Query(min_length=1, max_length=50),  # noqa: B008
) -> dict[str, Any]:
    uris = [f"spotify:{kind}:{item}" for item in ids]
    raw = _call(_data_client().library_contains, uris=uris, path="/me/library/contains")
    values = raw if isinstance(raw, list) else []
    return {
        "ok": True,
        "kind": kind,
        "ids": ids,
        "contains": [bool(value) for value in values],
    }


def _library_item(kind: str, value: object) -> dict[str, Any]:
    """Project one saved-library entry, flattened to top-level safe fields.

    The renderer reads id/name/uri directly off each page item, so the inner
    Spotify object is projected flat (raw fields like popularity/followers and
    available_markets never reach the renderer).
    """
    raw = value if isinstance(value, Mapping) else {}
    if kind == "tracks":
        projected = _track(raw.get("track")) or {}
        projected["added_at"] = raw.get("added_at")
        return projected
    if kind == "albums":
        projected = _album_summary(raw.get("album"))
        projected["added_at"] = raw.get("added_at")
        return projected
    return _artist(raw.get("artist") or raw)


@router.get("/library/{kind}")
def library(
    kind: Literal["tracks", "albums", "artists"],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=10),
) -> dict[str, Any]:
    client = _data_client()
    if kind == "tracks":
        raw = _call(
            client.get_saved_tracks, limit=limit, offset=offset, path="/me/tracks"
        )
    elif kind == "albums":
        raw = _call(
            client.get_saved_albums, limit=limit, offset=offset, path="/me/albums"
        )
    else:
        raw = _call(
            client.request,
            "GET",
            "/me/following",
            params={"type": "artist", "limit": limit, "offset": offset},
            path="/me/following",
        )
        if isinstance(raw, dict) and isinstance(raw.get("artists"), dict):
            raw = raw["artists"]
    page = _page(raw)
    page["items"] = [_library_item(kind, item) for item in page["items"]]
    return {"ok": True, "kind": kind, "page": page}


@router.post("/library/items")
def mutate_library(body: LibraryItemsRequest) -> dict[str, Any]:
    uris = [f"spotify:{body.kind}:{item}" for item in body.ids]
    client = _data_client()
    if body.saved:
        _call(client.save_library_items, uris=uris, path="/me/library")
    else:
        _call(
            client.request,
            "DELETE",
            "/me/library",
            params={"uris": ",".join(uris)},
            path="/me/library",
        )
    return {"ok": True, "saved": body.saved}


@router.get("/playlists")
def playlists(
    offset: int = Query(default=0, ge=0), limit: int = Query(default=10, ge=1, le=10)
) -> dict[str, Any]:
    raw = _call(
        _data_client().get_my_playlists,
        limit=limit,
        offset=offset,
        path="/me/playlists",
    )
    page = _page(raw)
    page["items"] = [_playlist_summary(item) for item in page["items"]]
    return {"ok": True, "page": page}


@router.get("/playlists/{playlist_id}")
def playlist(playlist_id: str) -> dict[str, Any]:
    client = _data_client()
    raw = _call(
        client.get_playlist,
        playlist_id=playlist_id,
        path=f"/playlists/{playlist_id}",
    )
    profile = _call(client.request, "GET", "/me", path="/me")
    detail = _playlist_detail(raw)
    owner_id = detail["owner"].get("id")
    user_id = profile.get("id") if isinstance(profile, dict) else None
    detail["is_owned"] = bool(owner_id and user_id and owner_id == user_id)
    return {"ok": True, "playlist": detail}


@router.post("/playlists")
def create_playlist(body: PlaylistCreateRequest) -> dict[str, Any]:
    raw = _call(
        _data_client().create_playlist,
        name=body.name,
        public=body.public,
        collaborative=body.collaborative,
        description=body.description,
        path="/me/playlists",
    )
    return {"ok": True, "playlist": _playlist_detail(raw)}


def _owned_playlist(client: Any, playlist_id: str) -> dict[str, Any]:
    playlist = _call(
        client.get_playlist, playlist_id=playlist_id, path=f"/playlists/{playlist_id}"
    )
    profile = _call(client.request, "GET", "/me", path="/me")
    owner_id = (
        playlist.get("owner", {}).get("id") if isinstance(playlist, dict) else None
    )
    user_id = profile.get("id") if isinstance(profile, dict) else None
    if not owner_id or not user_id or owner_id != user_id:
        raise HTTPException(403, Failure(category="unavailable").model_dump())
    return playlist


def _snapshot(payload: object) -> str | None:
    return payload.get("snapshot_id") if isinstance(payload, dict) else None


@router.patch("/playlists/{playlist_id}")
def edit_playlist(playlist_id: str, body: PlaylistDetailsRequest) -> dict[str, Any]:
    client = _data_client()
    _owned_playlist(client, playlist_id)
    raw = _call(
        client.update_playlist_details,
        playlist_id=playlist_id,
        name=body.name,
        public=body.public,
        collaborative=body.collaborative,
        description=body.description,
        path=f"/playlists/{playlist_id}",
    )
    return {"ok": True, "snapshot_id": _snapshot(raw)}


@router.post("/playlists/{playlist_id}/items")
def mutate_playlist_items(
    playlist_id: str, body: PlaylistItemsRequest
) -> dict[str, Any]:
    client = _data_client()
    _owned_playlist(client, playlist_id)
    if body.action == "add":
        if not body.uris:
            raise HTTPException(422, Failure(category="unavailable").model_dump())
        raw = _call(
            client.add_playlist_items,
            playlist_id=playlist_id,
            uris=body.uris,
            position=body.position,
            path=f"/playlists/{playlist_id}/items",
        )
    elif body.action == "remove":
        if not body.uris:
            raise HTTPException(422, Failure(category="unavailable").model_dump())
        raw = _call(
            client.remove_playlist_items,
            playlist_id=playlist_id,
            uris=body.uris,
            snapshot_id=body.snapshot_id,
            path=f"/playlists/{playlist_id}/items",
        )
    else:
        if (
            body.range_start is None
            or body.insert_before is None
            or not body.snapshot_id
        ):
            raise HTTPException(422, Failure(category="unavailable").model_dump())
        raw = _call(
            client.request,
            "PUT",
            f"/playlists/{playlist_id}/items",
            json_body={
                "range_start": body.range_start,
                "insert_before": body.insert_before,
                "snapshot_id": body.snapshot_id,
            },
            path=f"/playlists/{playlist_id}/items",
        )
    return {"ok": True, "snapshot_id": _snapshot(raw)}


@router.post("/capabilities/probe")
def capability_probe(body: ProbeRequest) -> dict[str, Any]:
    # Validate before building/using anything.
    if (body.feature == "artist_albums" and not body.artist_id) or (
        body.feature == "preview_url" and not body.track_id
    ):
        raise HTTPException(422, {"ok": False, "category": "unavailable"})
    path = (
        f"/artists/{body.artist_id}/albums"
        if body.feature == "artist_albums"
        else f"/tracks/{body.track_id}"
    )
    try:
        raw = _data_client().request("GET", path)
        available = (
            bool(raw)
            if body.feature == "artist_albums"
            else isinstance(raw, dict) and "preview_url" in raw
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- capability probe sanitizes transport errors
        if isinstance(exc, SpotifyAPIError) and getattr(exc, "status_code", None) in (
            403,
            404,
        ):
            available = False
        else:
            raise _failure(exc, path=path) from None
    # Deliberately stateless: the probe result is returned to this caller only
    # and never written into module-level capability state, which is shared by
    # every Desktop profile in this process (R1/F3).
    return {"ok": True, "feature": body.feature, "available": available}
