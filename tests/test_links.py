"""Link classification tests — pure URL parsing, no network or DB."""

import pytest

from dj.ingest.links import Link, classify

# (url, service, kind, id) for every supported shape.
SUPPORTED = [
    # Spotify web links
    ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "spotify", "track",
     "4uLU6hMCjMI75M1A2tKUQC"),
    ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=abc123", "spotify", "track",
     "4uLU6hMCjMI75M1A2tKUQC"),
    ("https://open.spotify.com/intl-pt/track/4uLU6hMCjMI75M1A2tKUQC", "spotify", "track",
     "4uLU6hMCjMI75M1A2tKUQC"),
    ("https://open.spotify.com/album/2nLOHgzXzwFEpl62zAgCEC", "spotify", "album",
     "2nLOHgzXzwFEpl62zAgCEC"),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "spotify", "playlist",
     "37i9dQZF1DXcBWIGoYBM5M"),
    ("https://open.spotify.com/artist/73A3bLnfnz5BoQjb4gNCga", "spotify", "artist",
     "73A3bLnfnz5BoQjb4gNCga"),
    ("open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", "spotify", "track",  # scheme-less paste
     "4uLU6hMCjMI75M1A2tKUQC"),
    # Spotify URIs
    ("spotify:track:4uLU6hMCjMI75M1A2tKUQC", "spotify", "track", "4uLU6hMCjMI75M1A2tKUQC"),
    ("spotify:album:2nLOHgzXzwFEpl62zAgCEC", "spotify", "album", "2nLOHgzXzwFEpl62zAgCEC"),
    ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "spotify", "playlist", "37i9dQZF1DXcBWIGoYBM5M"),
    ("spotify:artist:73A3bLnfnz5BoQjb4gNCga", "spotify", "artist", "73A3bLnfnz5BoQjb4gNCga"),
    # YouTube videos
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "video", "dQw4w9WgXcQ"),
    # watch + list= still classifies as 'video': the user pasted a specific video
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123", "youtube", "video",
     "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ?t=42", "youtube", "video", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/abc123XYZ", "youtube", "video", "abc123XYZ"),
    # YouTube playlists
    ("https://www.youtube.com/playlist?list=PLabc123", "youtube", "playlist", "PLabc123"),
    ("https://music.youtube.com/playlist?list=PLabc123", "youtube", "playlist", "PLabc123"),
]


@pytest.mark.parametrize("url,service,kind,id_", SUPPORTED)
def test_classify_maps_every_supported_shape(url, service, kind, id_):
    assert classify(url) == Link(service=service, kind=kind, id=id_, url=url)


UNSUPPORTED = [
    "https://open.spotify.com/",                 # bare domain
    "https://open.spotify.com/show/abc",         # podcast, not music
    "spotify:show:abc",
    "spotify:track:",                            # missing id
    "https://www.youtube.com/",                  # bare domain
    "youtube.com",
    "https://www.youtube.com/watch",             # no v param
    "https://www.youtube.com/playlist",          # no list param
    "https://soundcloud.com/artist/track",       # unsupported service
    "not a url at all",
    "",
]


@pytest.mark.parametrize("url", UNSUPPORTED)
def test_classify_rejects_unsupported_with_helpful_message(url):
    with pytest.raises(ValueError) as exc:
        classify(url)
    message = str(exc.value)
    assert url.strip() in message          # names what was pasted
    assert "Spotify" in message and "YouTube" in message  # names what works
