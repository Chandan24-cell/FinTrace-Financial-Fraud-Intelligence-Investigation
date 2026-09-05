# Fallback Demo Video — Operator Checklist (Day 29)

> PRD §10 Day-29 Done When: *"Fallback video confirmed playable."*
> Use this checklist after recording the demo video — `docs/DEMO_SCRIPT.md`
> is the storyboard you read during the recording.

## 1. Recording target

- [ ] Length: **2:50 – 3:00**. Hard cap 3:00 (PRD §14 demo budget).
- [ ] Resolution: **1920×1080** at 30 fps (Vercel preview tab + a terminal
      side-window both visible).
- [ ] Format: **H.264 MP4**, AAC audio, ≤ 50 MB total (fits Devfolio
      attachment limits + embeds in most slide decks).
- [ ] Filename: `sentinel-g-fallback-v1.mp4` (bump the version if you
      re-record).

## 2. Pre-recording state

- [ ] Production stack is live (Day 28 deploy completed):
      `curl https://sentinel-g.duckdns.org/health` returns `{"status":"ok"}`.
- [ ] Vercel frontend loads on first paint without console errors.
- [ ] Neo4j is seeded (`scripts/seed_neo4j.py --clean` ran successfully
      against the Railway URI).
- [ ] You're logged in at `/login` so the demo doesn't waste 15s on auth.

## 3. Content checklist — every PRD §14 phase covered

Cross-reference each line in `docs/DEMO_SCRIPT.md`. You should see, on
camera, in order:

- [ ] **0:00–0:20** Company Search — paste IL&FS CIN, hit Enter.
- [ ] **0:20–0:50** Analysis Dashboard — score 75 CRITICAL, DC 92, 18
      evidence signals, calibrated P(fraud) + conformal interval visible.
- [ ] **0:50–1:15** Graph Explorer — director chain expand, red flagged
      edges visible.
- [ ] **1:15–1:40** Evidence Provenance — at least one CRITICAL signal
      expanded showing specific rupee numbers.
- [ ] **1:40–2:00** ITC Carousel — three carousel cards CRITICAL, ring
      graph visible.
- [ ] **2:00–2:20** Evergreening — DHFL, patterns 13/14/15 all lit.
- [ ] **2:20–3:00** Report Export — PDF downloads, UUID + timestamp
      visible in download dialog.

## 4. Hosting

Pick ONE; the video must be playable without sign-in from a stranger's
laptop in case the judges open the link mid-presentation.

- [ ] **YouTube unlisted** (recommended) — URL embeds in Devfolio and
      plays without login.
      - [ ] Title: `Sentinel-G — 3-Minute Demo (HackHazards '26 Fallback)`
      - [ ] Visibility: **Unlisted** (NOT Private).
      - [ ] Captions: at least auto-generated.
- [ ] **Google Drive public link** — set "Anyone with the link → Viewer".
- [ ] **Vimeo unlisted** — same idea, password-free.

Do NOT host on Loom Free — links expire after 30 days on the free tier
and Day 30 + judging falls inside that window.

## 5. URL in submission

- [ ] Video URL pasted into the Devfolio submission's **Demo Video**
      field.
- [ ] Same URL pasted into the README under a "Live demo" heading so
      the GitHub repo also surfaces it.
- [ ] Same URL pasted into `docs/DEMO_SCRIPT.md` footer for the next
      operator to find.

## 6. Playback verification

The point of "confirmed playable" — actually open the link from a clean
environment and watch the whole thing.

- [ ] Open the hosting URL in an **incognito window** (no Google
      account signed in). Video starts playing without prompts.
- [ ] Audio is audible at default volume.
- [ ] Open the URL on a **second device** (phone, tablet, or another
      laptop). Plays without sign-in or app install.
- [ ] Seek to the 2:30 mark — Report Export phase plays cleanly (the
      tail is where most mid-recording crashes hide).
- [ ] Download the MP4 to local disk and play in VLC offline — confirms
      the file itself works in case judging happens without internet.

## 7. After judging

- [ ] Once HackHazards '26 results are out, the video URL can stay
      public — it's good portfolio material.
- [ ] If you re-record (e.g., for v2 demo), bump filename version and
      paste the new URL in all three places listed in §5.
