# Spotify Playlister

Export Spotify playlists you created or collaborate on to CSV, or recreate them as YouTube playlists.

## Setup

Create a Spotify app at <https://developer.spotify.com/dashboard>, then add this redirect URI:

```text
http://127.0.0.1:8765/callback
```

Install the CLI:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
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
source .venv/bin/activate
python -m pip install -e '.[youtube]'
```

Create an OAuth client in Google Cloud with the YouTube Data API enabled. If you use a Web application client, add this exact Authorized redirect URI:

```text
http://localhost:8766/
```

You can either download the OAuth client JSON or put the client values in `.env`:

```bash
YOUTUBE_CLIENT_ID="your-google-oauth-client-id"
YOUTUBE_CLIENT_SECRET="your-google-oauth-client-secret"
YOUTUBE_REDIRECT_URI="http://localhost:8766/"
```

Run a dry run first:

```bash
spotify-playlister export-youtube "https://open.spotify.com/playlist/..." --dry-run
```

Create a new YouTube playlist and add the best video search result for each Spotify track:

```bash
spotify-playlister export-youtube "https://open.spotify.com/playlist/..." \
  --title "Imported from Spotify"
```

Use an existing YouTube playlist instead:

```bash
spotify-playlister export-youtube "spotify:playlist:..." \
  --youtube-playlist-id "PL..."
```

If you prefer the downloaded JSON file instead of `.env`, add `--youtube-client-secrets ~/Downloads/client_secret.json`.

## Notes

Spotify's playlist item endpoint is currently intended for playlists owned by or collaborative with the authenticated user. YouTube matching is search-based, so run `--dry-run` before inserting items into a playlist.
