# 4-Week Plan: LLM Push Copy Evaluation Framework

> Goal: Build a portfolio-quality eval framework that demonstrates AI native PM thinking, suitable as a centerpiece for AI PM (model strategy) interviews at top tech companies.
>
> Budget: 8–10 hours/week × 4 weeks.
> Start date: TBD. Target end: 4 weeks after start.

---

## Week 1 — Eval Design Doc (writing only, no code)

**Goal**: A 12–18 page eval design document that an AI-savvy reviewer can read in 30 min and understand exactly what is being evaluated, how, and why.

**Tasks**:

- [ ] Day 1: Section 1 (Problem & Scope) + start Section 2 (User Segmentation)
- [ ] Day 2: Finish Section 2 + start Section 3 (Test Case Construction)
- [ ] Day 3: Finish Section 3 (rewritten to include videos × personas × modalities)
- [ ] Day 4: Section 4 (Eval Dimensions) — **most important day of the week**
- [ ] Day 5: Section 5 (Methodology incl. ablation design) + Section 6 (Models × Modality)
- [ ] Day 6: Section 7 (Success Criteria & Reporting) + Section 8 (Limitations)
- [ ] Day 7: Full read-through + golden-line self-check + share with 2–3 AI-circle reviewers

**Deliverable**: `docs/eval-design-doc.md` (final draft after Day 7 feedback).

**Self-check question**: Can a stranger read this in 30 min and tell me back what I'm evaluating, how, and why this design?

**Golden-line check (must pass before Day 7 final)**:
- Read every paragraph: "Would a Meta / Google / Snap PM think this could only be written by an industry insider?" → if yes, abstract one more layer.
- Read Section 4: "Would an algorithm engineer think the eval sophistication is too shallow?" → if yes, deepen.

---

## Week 2 — Data Pipeline + Commercial Baseline (Arms A + B)

**Goal**: Get the first set of real eval scores out, across commercial frontier models on the two simplest input arms.

**Tasks**:

- [ ] Day 8: YouTube Data API setup + curate 30 representative public Shorts
- [ ] Day 9: Extract 3–5 keyframes per video (yt-dlp + ffmpeg) + whisper audio transcription
- [ ] Day 10: Build 10 synthetic user personas + generate 300 test cases (30 × 10 cartesian)
- [ ] Day 11–12: Run Arm A (metadata only) across execution subset (Gemini 3.5 Flash / GPT-5.5 / Kimi K2.6 / DeepSeek V4-Pro; see eval-design-doc § 4.2)
- [ ] Day 13–14: Run Arm B (metadata + keyframes) across the same models

**Deliverables**:
- `data/videos.jsonl` — 30 curated video metadata
- `data/personas.jsonl` — 10 synthetic personas
- `data/test_cases.jsonl` — 300 (video, persona) pairs
- `results/baseline_commercial_armA.csv`
- `results/baseline_commercial_armB.csv`
- `notes/worklog.md` — what worked, what surprised me

**Self-check**: Does my eval actually distinguish models? Is there meaningful score spread? If everyone scores 4.5/5, the rubric is too coarse — refine it before W3.

---

## Week 3 — Open-Source Models + Audio Arm + Gap Analysis

**Goal**: Complete the model × arm matrix and turn raw scores into capability gap insights.

**Tasks**:

- [ ] Day 15: Run Arm A + B on Llama 4 Maverick / Qwen3.7-Max / DeepSeek V4-Pro / Kimi K2.6
- [ ] Day 16: Run Arm C (metadata + audio transcription) on all text-capable models
- [ ] Day 17: Run Arm D (full video stream) on Gemini 3.5 Flash / Kimi K2.6 / Doubao Seed 2.0 Pro / Qwen3.7-Max (last is ⚠️ pilot verify) — stretch goal if time permits
- [ ] Day 18: Synthesize results — build 2D dashboard (model × arm × dimension)
- [ ] Day 19–20: Write gap analysis — 3–5 concrete capability gaps with quantified evidence
- [ ] Day 21: Reviewer pass on gap analysis

**Deliverables**:
- `results/baseline_oss.csv`
- `results/armC.csv` + optional `results/armD.csv`
- `docs/gap-analysis.md` (5–8 pages)

**Self-check**: Can I state each gap in one sentence — what it is, why it matters, how I'd know it's solved?

---

## Week 4 — Value Estimation + Deliverable Polish

**Goal**: Make the final artifact something I can put on my resume, send to recruiters, and walk through in interviews at three lengths (5 / 15 / 30 min).

**Tasks**:

- [ ] Day 22–23: Scenario value estimation — for each of the top 3 gaps, estimate business value if solved (use public market data, NOT internal numbers)
- [ ] Day 24–25: Consolidate everything into a single `docs/report.md` (15–20 pages)
- [ ] Day 26: Build a small companion eval tool (Streamlit or CLI) — the thing I used to run the experiments, cleaned up to be runnable by others
- [ ] Day 27: Polish README, add 3–5 result charts, record a short demo video
- [ ] Day 28: Write resume bullet + LinkedIn post draft + practice 5/15/30 min pitches

**Deliverables**:
- `docs/report.md` — final integrated report
- `tool/` — companion eval tool, runnable
- README with charts and demo video link
- `notes/pitches.md` — 5 / 15 / 30 min versions
- `notes/resume-bullet.md`

**Self-check**: Can I open this repo in front of an interviewer and use it as a working artifact for 30 minutes without losing them?

---

## Cross-week discipline

- **Every Sunday**: 30 min review with one AI-circle peer.
- **Every Friday EOD**: Update `notes/worklog.md` with what shipped, what slipped, what I learned.
- **Golden-line check**: Run before every commit that touches `docs/` — would a Meta / Google / Snap PM think this could only be written by an industry insider? If yes, revise.
