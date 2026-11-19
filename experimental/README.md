# Experimental: Connections with decisions models

`decisions_classic_solver.py` plays NYT Connections puzzles in classic mode
(guess one group of four, get CORRECT / INCORRECT / INCORRECT - ONE AWAY, repeat
until all four groups are found or four mistakes are made) using a decisions
model, such as TypeSafe's Jev, Perplexity's Decider, Inception's Mercury
Decide, Cloudflare's Clef, OpenAI's GPT-6 Luna Decisions or Microsoft's
Microsoft-Decision-1, through OpenRouter's decisions endpoint
(`POST https://openrouter.ai/api/alpha/decisions`).
Every model gets identical questions, blend and feedback rules; only `--model`
changes. It is a standalone script and does not touch the main harness.

```bash
# key: OPENROUTER_API_KEY, from the environment or .env at the repo root
uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13      # canonical 20, 20 threads
uv run python experimental/decisions_classic_solver.py                                # openai/gpt-6-luna-decisions
uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13 --seed 43 --puzzle-ids 246,817
uv run python experimental/decisions_classic_solver.py --all --threads 10
```

Each run prints a per-puzzle transcript and summary and writes a JSON file to
`experimental/runs/` (git-ignored). Prices are looked up per model from
OpenRouter. A canonical run takes 10 to 30 seconds depending on the model.

## Comparing decisions models

To reproduce the comparison below, run each model at seeds 42, 43 and 44 and
average the summaries (run non-deterministic models twice):

```bash
for m in typesafe/jev-1.13 perplexity/pplx-decider-v1.1-27b inception/mercury-decide \
         cloudflare/clef openai/gpt-6-luna-decisions microsoft/microsoft-decision-1; do
  for s in 42 43 44; do uv run python experimental/decisions_classic_solver.py --model $m --seed $s; done
done
```

Results on the canonical 20 (2026-10-07 to 2026-10-12):

| Model | Puzzles won | Groups found | Mistakes per puzzle | Cost per run | Median latency | Requests/sec |
|---|---|---|---|---|---|---|
| Jev (`typesafe/jev-1.13`) | 14.8 / 20 | 67.2 / 80 | 1.9 | $0.06 | ~300 ms | ~33 |
| Perplexity Decider (`perplexity/pplx-decider-v1.1-27b`) | 13.3 / 20 | 63.3 / 80 | 2.0 | $0.05 | ~1,450 ms | ~8 |
| Mercury Decide (`inception/mercury-decide`) | 12.3 / 20 | 60.7 / 80 | 2.2 | $0.001 | ~800 ms | ~16 |
| Clef (`cloudflare/clef`) | 11.7 / 20 | 58.0 / 80 | 2.4 | $0.41 | ~1,850 ms | ~8 |
| GPT-6 Luna Decisions (`openai/gpt-6-luna-decisions`) | 9.7 / 20 | 56.0 / 80 | 2.5 | $0.20 | ~440 ms | ~24 |
| Microsoft-Decision-1 (`microsoft/microsoft-decision-1`) | 8.0 / 20 | 47.8 / 80 | 2.8 | $0.04 | ~700 ms | ~19 |

Requests/sec is API calls divided by wall time with 20 games in parallel.
The solver's wording and blend weights were tuned on Jev, so the setup
favours it somewhat.

- **Determinism.** Jev, Mercury Decide and Microsoft-Decision-1 return
  different answers on reruns and are averaged over six runs (two passes of
  seeds 42–44); expect about ±1–2 wins per run. Perplexity Decider, Clef and
  Luna returned identical results when rerun and are averaged over three.
- **Cost.** Input prices per 1M tokens are $0.042 (Jev, Microsoft), $0.02
  (Perplexity, Mercury), $0.24 (Clef) and $0.10 (Luna); output is free. Token
  counts differ a lot for the same requests: Mercury bills only its "native"
  tokens (about 225 per 64-question request, against about 6,000 by
  OpenRouter's own count), confirmed against the account's spend, so a run
  costs about a tenth of a cent.
- **Latency.** Perplexity Decider's latency grows with questions per request
  (serially about 405 ms for 1 question, 545 ms for 16, 1,110 ms for 64);
  Jev's stays flat at about 360–400 ms. The solver sends up to 64 questions
  per request, so models that scale with request size look slower here than
  their OpenRouter latency figures.
- **Rate limits.** Mercury Decide returned HTTP 429 at 20 threads; retries
  usually recover, but one run lost a puzzle after five attempts and was left
  out of the averages.

Clef only accepts question names matching `[A-Za-z0-9_.-]` and at most 64
questions per request, so the script names questions by position (`p0`, `s0`,
...) and splits requests at 64 for every model. This did not change Luna's
guesses on any puzzle.

If an OpenAI-routed model fails with `401 Incorrect API key provided: sk-proj-…`,
OpenRouter is forwarding a stale OpenAI key saved in the account's BYOK settings
(https://openrouter.ai/settings/integrations); remove it or allow shared
capacity.

## Why this exists

Decisions models are not language models. OpenRouter's decisions endpoint
(which uses TypeSafe's native `POST /v1/systemone` format) takes a `state` and a
map of typed questions and returns probabilities: a Noul is a yes/no
probability, a Choice is a distribution over named options, a Score is a
distribution over ordered levels. The model never generates text, so the main
harness, which sends a prompt and parses `<answer>` blocks, cannot evaluate it.
To put these models on the same rubric as the LLMs, our code has to decompose
the puzzle into typed questions and assemble the guess itself. That means the
solver is part of the "model" being scored. Any leaderboard entry for this
should be labelled with the method, not just the model name.

## What the script does, in reading order

1. Load the puzzles and the API key.
2. For each turn, shuffle the remaining words and put them in the `state`.
3. Ask one 3-level Score question per pair of remaining words: "do these two
   belong to the same group of four?" Instructions are structured objects
   (`{task, word_a, word_b}`), not prose. Questions are named by position and
   sent at most 64 per request, so the 120 pairs of a full board take two
   requests.
4. Score every candidate set of four by summing its six pair scores, giving a
   value from 0 to 6.
5. Keep only candidates consistent with the feedback so far. The rules are
   exact, not heuristic:
   - a real group overlaps a plain INCORRECT guess in at most 2 words, because
     the game would have said ONE AWAY at 3 and CORRECT at 4;
   - a real group overlaps a ONE AWAY guess in 0, 1 or 3 words, never 2 or 4,
     because one group takes 3 of that guess's words and the fourth word
     belongs to some other group.
   Candidates overlapping a ONE AWAY guess in exactly 3 words also get a bonus
   of 1.0 added to their pair sum.
6. Take the top 60 candidates by that score and ask the model a direct 3-level
   Score on each: "do these four together form one of the real groups?"
7. Blend: `(pair sum + bonus) / 6 + direct score / 2`. The pair component runs
   from 0 to 7/6, the set component from 0 to 1. Guess the best set.
8. Our code judges the guess against the answer key and updates the feedback.
   The model never sees the feedback text; it only ever answers questions.

Two shortcuts skip the API: when exactly four words remain they are guessed
directly, and when the feedback leaves a single consistent candidate it is
guessed without the direct set Score. Timeouts, rate limits and 5xx errors are
retried; a puzzle whose requests fail after five attempts is recorded as a
failure and the rest of the run still completes.

## How the method was tuned on Jev (2026-09-17, `jev-1.13.0`)

| Method | Wins /20 | Groups /80 | First guess correct | Mistakes per puzzle |
|---|---|---|---|---|
| One-shot, best variant (all four groups at once, no feedback) | 6 | 38 | n/a | n/a |
| Classic, Noul pairs, static matrix | 9 | 47 to 52 | 14 to 16 | 2.7 |
| Classic, Score-3 pairs, re-queried each turn | 10 to 12 | 54 to 58 | 15 to 16 | 2.3 |
| Classic, structured Score-3 pairs | 11 to 12 | 57 to 61 | 15 to 16 | 2.2 |
| Classic, structured pairs plus direct set-Score blend | 13 to 16 | 61 to 67 | 18 | 1.7 |
| Above plus a same-group ONE AWAY heuristic (superseded, see below) | 15, 15, 16 | 65, 67, 69 | 16 to 18 | 1.6 to 2.0 |
| **Above with exact feedback rules (this script)** | **15** | **67** | **16** | **2.0** |

Jev is not deterministic. Identical requests return slightly different
probabilities, and the answers depend on word order. Repeated runs of one
configuration differ by about one win and three groups, so any single run
should be read with that band in mind. If a stable number is needed, average
several runs; they are cheap.

## Baseline against an LLM

Haiku 4.5's latest classic run on the same canonical 20 (2026-03-16, the row on
the classic leaderboard) next to the Jev solver's verification run. Single runs
on both sides; each sits in a band of about one win either way.

| Metric | Haiku 4.5 | Jev solver |
|---|---|---|
| Puzzles won | 15 of 20 | 15 of 20 |
| Groups found | 63 of 80 | 67 of 80 |
| Total guesses | 93 | 107 |
| Incorrect guesses per puzzle | 1.5 | 2.0 |
| Guess accuracy | 67.7% | 62.6% |
| Time for all 20 | 600 s | 6 s |
| Cost | $0.50 | $0.06 |

They fail on different puzzles. Jev solves 814, 815 and 818, which Haiku did
not; Haiku solved 817, 832 and 842, which Jev loses to its known blind spots
and traps. Both score 2 on 246 and near zero on 830.

## How we got here

**One-shot does not work.** Asking for the whole partition in one call tops out
around 6 of 20 puzzles. We tried Noul and Score pair questions, asking each pair
in both orders, averaging over shuffles, a Choice per word over its partners,
rating candidate sets directly, rating whole candidate partitions, `jev-preview`,
and several ways of aggregating pair scores. The best of them, 3-level Score
pairs summed into set scores and an exact search over all 2.6 million
partitions, reached 38 of 80 groups. Feedback is what Jev needs, so classic mode
is the right game for it.

**Re-query the remaining words each turn.** Reusing the first turn's pair scores
wins about 9 of 20; asking again about only the remaining words wins 10 to 12.
TypeSafe's own docs say irrelevant state is a distractor, and removing solved
words from the state is exactly that.

**Score beats Noul.** Jev's Noul probabilities cluster near 0 and 1. A 3-level
Score returns a probability-weighted mean that spreads more evenly and ranks
pairs better. Five levels were no better than three.

**Structured instructions help a little.** Passing `{task, word_a, word_b}` as
the instructions object instead of a sentence gave a small, repeatable lift.

**Keep the state minimal.** Adding an explanation of Connections category
types to the state made results worse every time we tried it, in both one-shot
and classic mode. Per the architecture analysis at
archerhume.com/posts/jevs-architecture-unmasked, every question branch reads the
shared state, so extra text there distracts all 120 pair judgments at once. A
bare word list with no puzzle blurb at all was worse still, so the one-line
blurb stays.

**Why blend pair and set scores.** A probe of the four puzzles Jev failed most
(476, 830, 831, 832) showed that Jev recognises nearly every category once it is
named (correct in 14 of 16 Choice questions over the category names). The
failures are discovery failures at the pair level: in every hard group, one
in-group pair scores around 0.30 while some cross-group pair scores 0.55 to
0.78, so summing six pairs splits the group. Direct "do these four form a
group" scores separate the true group from random sets better than pairs do,
but a near-miss set with one word swapped beat the truth in 5 of 16 groups. The
two signals fail in different places, and adding them fixed puzzles 831, 836 and
837 that pairs alone never solved.

**Why the exact feedback rules.** Puzzle 817 showed the blend fixating: Jev
reads TANG as a flavour word, so four consecutive guesses were KICK, PUNCH,
ZEST, ZING with TANG swapped in for a different word each time, each coming
back ONE AWAY. The first fix assumed all ONE AWAYs point at the same group and
kept only sets overlapping each in exactly 3 words. A code review showed that
assumption is unsound: two ONE AWAYs aimed at different groups can leave only
wrong sets standing, and the filter went dead for the rest of a game as soon as
one clue's group was solved. The current rules are the ones that hold for any
valid puzzle, so they never exclude a real group and they stay useful after
groups are solved. 817 can still be lost: both TANG (piquancy) and STAG (male
animal) are genuine traps that Jev prefers, and the rules alone cannot tell the
trap word from the real fourth member.

## What did not help

Recorded so nobody retries them.

- Dropping the weakest pair from a set's score, or using min, median, centered,
  log-odds or reciprocal-rank aggregation. Plain sum won on every saved matrix.
  The weak pair carries real information against near-miss sets.
- Scoring sets by triples instead of pairs: worse and five times the cost.
- Letting Jev pick among candidate sets with a single Choice question, or pick
  among candidate partitions. Choice options are compared jointly and carry a
  position bias; permuting and averaging did not rescue it.
- A wider pool of 120 candidate sets for the direct score.
- Averaging pair scores over several shuffles, or asking each pair in both
  orders: within noise in classic mode, at two to four times the cost.
- A larger ONE AWAY bonus. Removing the bonus entirely cost a couple of groups.
- `jev-preview`: indistinguishable from `jev-latest`.

## Cost tuning (2026-10-13)

Jev bills input tokens only, so the question is where the ~6,000 tokens of a 64-question request go.
Single requests on one full board, `usage.input_tokens`:

| 64 pair questions | Input tokens | Per question |
|---|---|---|
| 3-level Score, solver wording (default) | 8,065 | ~120 |
| Same with an empty state | 7,946 | (state ≈ 120 per request) |
| Score, no task string | 6,529 | ~96 |
| Score, criteria cut to "different" / "related" / "same group" | 6,401 | ~94 |
| Score, string instructions and 1-character criteria | 3,900 | ~55 |
| Noul, solver task, structured `{task, word_a, word_b}` | 3,649 | ~51 |
| Noul, task "Same group of four?" | 2,753 | ~37 |

A request has about 250 tokens of fixed overhead plus the state, so the state is about 1.5% of the bill and
moving text into it saves nothing. Each Score criterion costs about 13 tokens of structure plus its text
even at one character, so the three criteria are about 57% of a Score question. A default run splits about
55 / 45 between pair and set questions at ~130 tokens each. The levers are the question type and the
question count; the wording is a smaller lever.

New flags, all off by default; with no flags the requests are byte-identical to before (checked offline
over all 41 puzzles against a fake API):
`--pair-type noul`, `--set-type noul`, `--pair-wording terse|terse-task`, `--set-wording terse|terse-task`,
`--set-margin M` (skip the set request when the top pair sum leads by M), `--reask touched|uncertain`
(`--uncertain-band LO,HI`, default 0.25,0.75), `--endgame splits` (with 8 words left, set-score both halves of
every consistent split, no pair questions). `experimental/run_cost_tuning.sh` has every config as a tag.

Results on the canonical 20, median (range); 5 seeds are 42–46 and 2 seeds are 42–43. The baseline over
seeds 42–46 is 15 (14–16) wins, 68 (65–70) groups and $0.059.

| Config (tag) | Seeds | Wins | Groups | Tokens/run | Cost/run |
|---|---|---|---|---|---|
| Baseline, earlier 5 seeds (`base`) | 5 | 15 (14–16) | 68 (65–70) | 1405k | $0.059 |
| Noul pairs and sets (`noul_both`) | 5 | 15 (13–15) | 67 (62–67) | 673k | $0.028 |
| `noul_both` + split endgame (`nb_endgame`) | 5 | 15 (14–16) | 66 (65–71) | 617k | $0.026 |
| `nb_endgame` + `--reask uncertain` (`nb_eg_unc`) | 5 | 15 (14–16) | 67 (66–70) | 541k | $0.023 |
| `noul_both` + touched + margin 0.5 + endgame (`nb_combo`) | 5 | 15 (14–15) | 67 (65–69) | 452k | $0.019 |
| `--levels 2` (`lv2`) | 5 | 15 (14–16) | 67 (64–70) | 1161k | $0.049 |
| `--set-margin 0.5` (`margin05`) | 5 | 15 (15–17) | 67 (66–72) | 1254k | $0.053 |
| `--reask uncertain` (`reask_unc`) | 5 | 14 (13–16) | 64 (61–68) | 1225k | $0.051 |
| `--reask touched` | 2 | 15 (14–16) | 66.5 | 1078k | $0.045 |
| `--endgame splits` | 2 | 15 | 67.5 | 1257k | $0.053 |
| Terse pair criteria / terse pair task | 2 | 15 / 15 | 67 / 66.5 | 1255k / 1205k | $0.053 / $0.051 |
| Terse set criteria / terse set task | 2 | 14.5 / 14 | 66.5 / 64.5 | | $0.055 / $0.052 |
| Noul pairs only / Noul sets only | 2 | 14 / 14 | 65 / 66 | | $0.043 / $0.045 |
| `--top-k` 20 / 30 / 40 | 2 | 13.5 / 13.5 / 12.5 | 63 / 62.5 / 62 | | $0.042–0.052 |
| `--set-margin 1.0` (rarely triggers) | 2 | 14 | 64.5 | 1395k | $0.059 |
| `--reask uncertain --uncertain-band 0.15,0.85` | 2 | 14 | 63.5 | 1341k | $0.056 |
| `noul_both` + terse-task pairs | 2 | 13 (10–16) | 62 | 600k | $0.025 |
| `noul_both` + margin 0.3 / 0.5 | 2 | 13.5 / 13.5 | 64 / 64.5 | | $0.024 / $0.026 |
| `noul_both` + touched | 2 | 13.5 | 64 | 532k | $0.022 |
| `--levels 2` + margin 0.5 + uncertain + endgame (`score_combo`) | 2 | 14.5 | 65.5 | 770k | $0.032 |

**Held-out check.** `--all` runs 41 puzzles; the 21 non-canonical ones are held out. One run per seed:

| Config | Seed | Canonical 20: wins / groups | Held-out 21: wins / groups | All 41 | Cost, all 41 |
|---|---|---|---|---|---|
| Baseline | 42 | 14 / 65 | 13 / 60 | 27 / 125 | $0.128 |
| Baseline | 43 | 16 / 70 | 10 / 54 | 26 / 124 | $0.132 |
| `nb_eg_unc` | 42 | 15 / 67 | 11 / 56 | 26 / 123 | $0.049 |
| `nb_eg_unc` | 43 | 13 / 63 | 12 / 58 | 25 / 121 | $0.048 |
| `nb_combo` | 42 | 15 / 66 | 10 / 51 | 25 / 117 | $0.040 |
| `nb_combo` | 43 | 14 / 64 | 11 / 53 | 25 / 117 | $0.042 |
| `--levels 2 --set-margin 0.5` | 42 | 15 / 66 | 12 / 58 | 27 / 124 | $0.091 |
| `noul_both` | 42 | 14 / 66 | 11 / 54 | 25 / 120 | $0.062 |

The baseline itself moved 3 wins on the held-out 21 between seeds, so a single held-out run is not
enough. On two seeds, `nb_eg_unc` ties the baseline on the held-out puzzles (11.5 wins / 57 groups on
average for both) and is 1 win and 2.5 groups behind over all 41, which is within one run's noise.
`nb_combo` scored 117 groups on both seeds against 124–125, so its extra cuts (touched re-asking and the
set margin on top of Noul) cost real accuracy. It is also about twice as fast on the canonical 20 (about
4 s and 150 requests against about 8 s and 230); `nb_eg_unc` takes about 4.4 s and 185 requests.

**Recommended:** `nb_eg_unc`, at $0.023 per canonical run against $0.059 (−61%) and $0.048 against $0.13
on all 41:

```bash
uv run python experimental/decisions_classic_solver.py --model typesafe/jev-1.13 \
  --pair-type noul --set-type noul --endgame splits --reask uncertain
```

The conservative choice is `--levels 2 --set-margin 0.5`: it keeps Score questions, matched the baseline on
`--all` (27 / 124) and saves about 30%. Its two levers passed 5 seeds separately, but the combination has
only the one `--all` run.

What worked and what didn't:

- **Noul is the big lever.** It halves the tokens and keeps the median on the canonical 20, but the
  worst seed falls to 13 wins and 62 groups. The README's earlier "Score beats Noul" finding came from a
  static matrix; with re-querying and the set blend the gap is small, and the split endgame plus uncertain re-asking close it.
- **Fewer set questions hurts.** Top-k below 60 cost 1.5–2.5 wins at every value tried.
- **Terse set wording hurts; terse pair wording is neutral but saves little** (6–12%).
- **Skipping work after the first turn is roughly neutral on its own** (margin 0.5, split endgame,
  touched re-asking) and saves 10–25% each. Stacked on Noul, touched re-asking plus the margin lost about 7 groups over all 41; uncertain re-asking plus the split endgame did not.
- **Confidence anchors in the state did not help** (tested with a temporary flag, not kept: three invented groups, as prose or a list,
  labelled 90% / 30% / 10%, words absent from every puzzle; on top of `nb_eg_unc`). Picked on the canonical
  20 and tested on the held-out 21, two `--all` seeds each: the list form won the canonical 20 (15 / 67.5
  against 14 / 65) and lost the held-out 21 (10.5 / 50.5 against 11.5 / 57). The prose form was worse on the
  canonical 20 (12.5 / 62.5) and swung 22–27 wins over all 41. On a single board the anchors made the Noul
  answers more extreme, not less (39 of 64 between 0.2 and 0.8, against 47 without), so the hoped-for
  spreading did not happen.
- **Lower-risk savings:** `--levels 2` (−17%) and `--set-margin 0.5` (−10%) both met the bar over 5
  seeds without Noul; together they matched the baseline on one `--all` run.

Also fixed: HTTP 401/402 now stop the run immediately instead of being recorded as failed puzzles, OpenRouter's
transient HTTP 499 "internal_error" is retried, and two runs with one tag finishing in the same second no
longer overwrite each other's JSON. Per-puzzle JSON now records `stage_tokens` and `stage_questions`.
OpenRouter's key usage lags by a minute or so, so a rising total right after a batch is the batch landing.

## Known limits

- The word list in `connections_puzzles.yml` is stored in group order. The
  script shuffles before every request; sending the words unshuffled leaks the
  answer and produced a perfect solve in our first smoke test.
- Some categories are blind spots even when named: N.H.L. team members (DUCK,
  FLYER, SENATOR, STAR), PAPER ___ (CLIP, TIGER, TOWEL, TRAIL), homophones and
  most fill-in-the-blank groups. No question phrasing we tried moved these.
- The trap bonus from the one-shot leaderboard is not modelled here.
- A canonical run makes about 230 to 270 requests. 20 threads has been fine on
  OpenRouter for every model except Mercury Decide, which sometimes returns
  HTTP 429; use `--threads 10` if that loses puzzles.
