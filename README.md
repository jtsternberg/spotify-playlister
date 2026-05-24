# Spotify Playlister

Export Spotify playlists you created or collaborate on to CSV, or recreate them as YouTube playlists.

## Setup

Create a Spotify app at <https://developer.spotify.com/dashboard>, then add this redirect URI:

```text
http://127.0.0.1:8765/callback
```

Install the CLI:

```bash
python3 -m pip install -e .
```

Create a local `.env` file:

```bash
cp .env.example .env
```

Then fill in `SPOTIFY_CLIENT_ID` with your Spotify app's client ID. `.env` is ignored by git.

## Export CSV

List your Spotify playlists:

```bash
spotify-playlister playlists
```

Export by playlist ID, URI, or Spotify URL:

```bash
spotify-playlister export-csv "https://open.spotify.com/playlist/..." --output playlist.csv
```

The CSV includes:

- position
- track name
- artists
- album
- Spotify URL
- duration
- release date
- ISRC
- whether the row is a local Spotify file

## Export To YouTube

Install YouTube support:

```bash
python3 -m pip install -e '.[youtube]'
```

Create an OAuth Desktop app in Google Cloud with the YouTube Data API enabled, then download the OAuth client JSON.

Run a dry run first:

```bash
spotify-playlister export-youtube "https://open.spotify.com/playlist/..." \
  --youtube-client-secrets ~/Downloads/client_secret.json \
  --dry-run
```

Create a new YouTube playlist and add the best video search result for each Spotify track:

```bash
spotify-playlister export-youtube "https://open.spotify.com/playlist/..." \
  --youtube-client-secrets ~/Downloads/client_secret.json \
  --title "Imported from Spotify"
```

Use an existing YouTube playlist instead:

```bash
spotify-playlister export-youtube "spotify:playlist:..." \
  --youtube-client-secrets ~/Downloads/client_secret.json \
  --youtube-playlist-id "PL..."
```

## Notes

Spotify's playlist item endpoint is currently intended for playlists owned by or collaborative with the authenticated user. YouTube matching is search-based, so run `--dry-run` before inserting items into a playlist.
