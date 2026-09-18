# Experimental: Connections with TypeSafe Jev

`jev_classic_solver.py` plays NYT Connections puzzles in classic mode (guess one
group of four, get CORRECT / INCORRECT / INCORRECT - ONE AWAY, repeat until all
four groups are found or four mistakes are made) using TypeSafe.ai's Jev model.
It is a standalone script and does not touch the main harness.

```bash
# key: either export TYPESAFE_API_KEY or put the raw key in .env.jev at the repo root (git-ignored)
uv run python experimental/jev_classic_solver.py                 # canonical 20, 20 threads
uv run python experimental/jev_classic_solver.py --puzzle-ids 246,817
uv run python experimental/jev_classic_solver.py --all --threads 10
```

Each run prints a per-puzzle transcript and summary and writes a JSON file to
`experimental/runs/`. A canonical run costs about six cents and takes under ten
seconds; the 64-puzzle held-out cohort costs about twenty cents.

```bash
uv run python experimental/jev_classic_solver.py --puzzles experimental/puzzles_heldout64.yml   # held-out 64
uv run python experimental/jev_classic_solver.py --puzzles experimental/puzzles_dev.yml          # dev 32
uv run python experimental/jev_classic_solver.py --endgame greedy                                 # fewer mistakes, fewer wins
```

## Why this exists

Jev is not a language model. Its one endpoint, `POST /v1/systemone`, takes a
`state` and a map of typed questions and returns probabilities: a Noul is a
yes/no probability, a Choice is a distribution over named options, a Score is a
distribution over ordered levels. It never generates text, so the main harness,
which sends a prompt and parses `<answer>` blocks, cannot evaluate it. To put
Jev on the same rubric as the LLMs, our code has to decompose the puzzle into
typed questions and assemble the guess itself. That means the solver is part of
the "model" being scored. Any leaderboard entry for this should be labelled with
the method, not just the model name.

## What the script does, in reading order

1. Load the puzzles and the API key.
2. For each turn, shuffle the remaining words and put them in the `state`.
3. Ask one 3-level Score question per pair of remaining words: "do these two
   belong to the same group of four?" Instructions are structured objects
   (`{task, word_a, word_b}`), not prose.
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
6. Take the top 60 candidates by that score and ask Jev a direct 3-level Score
   on each: "do these four together form one of the real groups?"
7. Blend: `(pair sum + bonus) / 6 + direct score / 2`. The pair component runs
   from 0 to 7/6, the set component from 0 to 1.
8. Feasibility guard: walk the blended ranking and guess the first set whose
   leftover words can still be split into feedback-consistent groups, with every
   outstanding ONE AWAY clue satisfied by some group. A set that cannot be part
   of any complete solution is never guessed, however good it looks.
9. At eight words, switch to the endgame. Only 35 splits into two groups exist.
   Drop the splits inconsistent with feedback, then ask Jev, in one request, a
   Noul per half ("do these four share a specific, conventional connection?")
   and two Choice questions over the whole splits (each split written both
   ways round). A split's weight is the odds of its more recognisable half,
   cubed, times its Choice probability. An exact decision tree over the
   remaining mistake budget then picks the guess that maximises the weighted
   chance of eventually finishing, which can mean an informative guess rather
   than the likeliest one. `--endgame greedy` instead guesses the heaviest
   split's stronger half.
10. Our code judges the guess against the answer key and updates the feedback.
    Jev never sees the feedback text; it only ever answers questions.

Two shortcuts skip the API: when exactly four words remain they are guessed
directly, and when the feedback leaves a single consistent candidate or split it
is guessed without further questions. A puzzle whose requests fail after five
attempts is recorded as a failure and the rest of the run still completes.

## Puzzle sets

- `inputs/connections_puzzles.yml`, canonical 20: the leaderboard set shared with
  the LLM runs. Small, and it flatters this solver: 75 to 80% here against about
  53% on a broad sample.
- `experimental/puzzles_heldout64.yml`, 64 puzzles, 16 per year 2023 to 2026:
  the test set. Taken from Mike Harris's `mharris717/connections` cohort on which
  he ran our unmodified solver, so his numbers and ours are directly comparable.
  Frozen; never tune on it.
- `experimental/puzzles_dev.yml`, 32 puzzles, 8 per year: for choosing
  parameters. Disjoint from both sets above.

All three come from the public Eyefyre NYT-Connections-Answers archive and may
have appeared in Jev's training data.

## Results on the held-out 64 (2026-09-17, `jev-1.13.0`)

| Solver | Wins /64 | Groups /256 | Mistakes per puzzle |
|---|---|---|---|
| Ours before this change, Mike's run of the exact Python | 33 | | 2.5 |
| Ours before this change, our two runs | 34, 33 | 176, 174 | 2.5 |
| Guard + planner with our blended scores as weights | 34 | 175 | 3.2 |
| **Guard + planner with Harris weights (this script)** | **39, 36** | **187, 179** | 3.2 |
| Mike's hybrid (his run) | 42 | | 2.8 |

Paired on the first run pair, the new endgame won 7 puzzles the old solver lost
and lost 2 it had won. Endgame conversion went from 34 of 50 to 39 of 51; with
all four lives intact it went from 18 of 23 to 22 of 24. The cost is the trade
Mike documented: mistakes up about a quarter and zero-mistake solves gone,
because the planner deliberately spends lives on informative guesses. On the
classic leaderboard the solve rate is the headline, so we take that trade.

## Results on the canonical 20 (2026-09-17, `jev-1.13.0`, before the endgame change)

| Method | Wins /20 | Groups /80 | First guess correct | Mistakes per puzzle |
|---|---|---|---|---|
| One-shot, best variant (all four groups at once, no feedback) | 6 | 38 | n/a | n/a |
| Classic, Noul pairs, static matrix | 9 | 47 to 52 | 14 to 16 | 2.7 |
| Classic, Score-3 pairs, re-queried each turn | 10 to 12 | 54 to 58 | 15 to 16 | 2.3 |
| Classic, structured Score-3 pairs | 11 to 12 | 57 to 61 | 15 to 16 | 2.2 |
| Classic, structured pairs plus direct set-Score blend | 13 to 16 | 61 to 67 | 18 | 1.7 |
| Above plus a same-group ONE AWAY heuristic (superseded, see below) | 15, 15, 16 | 65, 67, 69 | 16 to 18 | 1.6 to 2.0 |
| Above with exact feedback rules | 15 | 67 | 16 | 2.0 |
| **Above plus feasibility guard and endgame planner (this script)** | **15** | **66** | **16** | **3.0** |

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

**Why the feasibility guard and the endgame planner.** Mike Harris built an
independent TypeSafe solver, ported our method as a control, and ran our exact
Python on 128 fresh puzzles. Two additions gave his hybrid 82 wins to our 68:
guessing only sets that can still be part of a complete feedback-consistent
solution, and an exact decision tree over the 35 possible splits at eight words.
Two independent Codex reviews had recommended the same two ideas. Porting them
taught us that the planner is only as good as its weights. With our blended
pair-plus-set scores as split weights, and at any sharpening of them, the
planner doubled endgame mistakes without converting more endgames (34 of 64,
same as before). With Mike's weights, a Noul coherence question per half cubed
as odds and a Choice across whole splits, it converts. The Choice over splits
works here where it failed at 16 words because 35 fully specified options is a
small, joint comparison, exactly what the primitive is for. The guard itself
changes few guesses (pre-endgame mistakes were unchanged) but is exact and free.

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
- The endgame planner weighted by our own blended scores, at sharpness 2, 4 or
  8: same wins as no planner, twice the endgame mistakes. The weights need a
  joint comparison across splits, not a per-set score.
- The same planner with our blended weights and greedy selection: equal to the
  baseline within noise. The exact split filter alone removes very few guesses.

## Known limits

- The word list in `connections_puzzles.yml` is stored in group order. The
  script shuffles before every request; sending the words unshuffled leaks the
  answer and produced a perfect solve in our first smoke test.
- Some categories are blind spots even when named: N.H.L. team members (DUCK,
  FLYER, SENATOR, STAR), PAPER ___ (CLIP, TIGER, TOWEL, TRAIL), homophones and
  most fill-in-the-blank groups. No question phrasing we tried moved these.
- The trap bonus from the one-shot leaderboard is not modelled here.
- Our port recovers about half of the gain Mike measured for his hybrid (39 and
  36 versus his 42, over a baseline of 33 to 34). The rest may be his 16- and
  12-word selection details, request batching, or single-run noise on his side.
- Rate limits are 1,200 requests per minute. A canonical run makes about 180
  calls, so 20 threads is comfortable.
