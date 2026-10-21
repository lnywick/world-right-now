# THE WORLD RIGHT NOW — Daily social video feed for Readymag

This package runs six YouTube video searches daily and deploys six separate vertical 9:16 video embeds to GitHub Pages. It also offers a combined overview page.

## 1. Upload to GitHub
Create a **public** GitHub repository named `world-right-now`. Choose "uploading an existing file" in Quick setup. Upload the **contents** of this folder, including the hidden `.github/workflows/daily.yml` file, retaining directory structure. Commit.

## 2. YouTube API setup
In Google Cloud Console enable **YouTube Data API v3** and create an API key (restrict it to that API). **Never** paste it into HTML or public GitHub files. In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Secret name: `YOUTUBE_API_KEY`. Value: the API key.

## 3. Enable automatic deployment
In repository **Settings → Pages → Build and deployment → Source**, select **GitHub Actions**. Under **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions**, if your repository allows that configuration. Go to **Actions → Daily video feed → Run workflow**. Review the log; the first successful workflow publishes your site. GitHub Pages may take a few moments to appear.

Workflow runs daily near **07:17 UTC** (not guaranteed exactly), and writes the last selected videos back to `videos.json`, so prior selections persist. Scheduled workflows can be paused by GitHub for inactivity.

## 4. Six *separate* widgets in Readymag
Create a Readymag Code widget for each category. In each one paste an iframe like:

```html
<iframe src="https://YOURUSERNAME.github.io/world-right-now/?topic=war" style="width:100%;height:100%;border:0;background:#080808" allow="autoplay;encrypted-media;picture-in-picture" title="War video"></iframe>
```

Change only `topic` for the six widgets:
- `war`
- `animals`
- `protests`
- `climate`
- `migration`
- `wealth`

Replace `YOURUSERNAME` with your actual GitHub username. Give **each** Code widget a **9:16** dimension (e.g. 270 × 480 px); do not make YouTube embeds too small. Position each widget freely in Readymag.

The combined overview page remains at `https://YOURUSERNAME.github.io/world-right-now/` with all six topics.

## Constraints
- YouTube Data API does **not** reliably expose whether a clip is recorded as vertical 9:16; the frames are portrait, but some videos may display letterboxing. This project favours brief videos using search keywords (including Shorts). Strict vertical filtering requires further tooling or manual curation.
- Shortlisted videos are **not** fact-checked or ranked as the objectively "worst" events. Search recency does not establish relevance or authenticity; sensitive footage should be reviewed before publication.
- Browser/autoplay and YouTube controls/branding restrictions apply. Some videos may become unavailable, region-blocked or require clicks.
- TikTok and Instagram automatic sourcing are not included.
- Caution: six simultaneous YouTube embeds can affect performance and visitor privacy. Disclose third-party embeds and obtain consent where legally required.
- Some features require a paid Readymag subscription; testing in Preview may differ from publication.
