# Roig Arena RSS Feed

RSS feed generator for upcoming events at [Roig Arena](https://www.roigarena.com) (Valencia, Spain).

The feed is generated daily via GitHub Actions and published to GitHub Pages.

## How it works

The script scrapes the Roig Arena events page, parses the Nuxt SSR payload embedded in the HTML, and builds an RSS 2.0 feed with event details including dates, prices, categories, images, and ticket links.

## Live feed

Once deployed, the feed is available at:

```
https://<your-user>.github.io/roig-arena-rss-feed/feed.xml
```

Add this URL to any RSS reader to get notified about new events.

## Local usage

No dependencies beyond Python 3.10+ standard library.

**Run as a local server:**

```sh
python roigarena_rss.py
# Serves the feed at http://localhost:8888/feed.xml
```

**Generate a one-off XML file:**

```sh
python roigarena_rss.py --once > feed.xml
```

## Deployment

The included GitHub Actions workflow (`.github/workflows/generate-feed.yml`) runs daily at 06:00 UTC and publishes the feed to GitHub Pages.

To enable it:

1. Push this repo to GitHub.
2. Go to **Settings > Pages** and set the source to **GitHub Actions**.
3. The workflow will run on the next schedule, or trigger it manually from the **Actions** tab.

## License

[MIT](LICENSE)
