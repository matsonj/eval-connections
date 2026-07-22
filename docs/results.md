---
theme: light
title: Connections Eval — One-Shot Box Score
orientation: landscape
continuous: true
---

Latest one-shot runs for 14 models (20 games each, one submission per game, max 100 pts; sorted by points, avg time, cost) · [Classic (multi-turn) leaderboard →](classic.html)

```table
{
  "columns": [
    {
      "id": "model",
      "title": "Model",
      "bold": true
    },
    {
      "id": "date",
      "title": "Date"
    },
    {
      "id": "pts",
      "title": "PTS",
      "align": "right",
      "bold": true,
      "type": "heatmap",
      "higherIsBetter": true
    },
    {
      "id": "pts_pct",
      "title": "PTS%",
      "align": "right",
      "type": "heatmap",
      "higherIsBetter": true,
      "fmt": "pct1"
    },
    {
      "id": "w",
      "title": "W",
      "align": "right"
    },
    {
      "id": "grp",
      "title": "GRP",
      "align": "right"
    },
    {
      "id": "trap",
      "title": "TRAP",
      "align": "right"
    },
    {
      "id": "inv",
      "title": "INV",
      "align": "right"
    },
    {
      "id": "avg_time",
      "title": "AVG/G",
      "align": "right"
    },
    {
      "id": "tok_per_game",
      "title": "TOK/G",
      "align": "right"
    },
    {
      "id": "cost",
      "title": "COST",
      "align": "right",
      "type": "heatmap",
      "higherIsBetter": false,
      "fmt": "currency_auto"
    },
    {
      "id": "cost_per_game",
      "title": "$/G",
      "align": "right"
    }
  ],
  "data": [
    {
      "model": "<a href=\"logs/2026-07-22T05-42-29_fugu-ultra.html\">sakana/fugu-ultra</a>",
      "date": "2026-07-22",
      "pts": 90,
      "pts_pct": 0.9,
      "w": "20",
      "grp": "80/80",
      "trap": "30",
      "inv": "0",
      "avg_time": "1m56s",
      "tok_per_game": "18.6k",
      "cost": 6.3,
      "cost_per_game": "$0.315"
    },
    {
      "model": "<a href=\"logs/2026-07-22T06-03-05_fable-5.html\">anthropic/claude-fable-5</a>",
      "date": "2026-07-22",
      "pts": 85,
      "pts_pct": 0.85,
      "w": "19",
      "grp": "78/80",
      "trap": "26",
      "inv": "0",
      "avg_time": "35s",
      "tok_per_game": "1.5k",
      "cost": 0.84,
      "cost_per_game": "$0.042"
    },
    {
      "model": "<a href=\"logs/2026-07-22T04-44-53_gpt5.6-sol.html\">openai/gpt-5.6-sol</a>",
      "date": "2026-07-22",
      "pts": 83,
      "pts_pct": 0.83,
      "w": "19",
      "grp": "78/80",
      "trap": "24",
      "inv": "0",
      "avg_time": "18s",
      "tok_per_game": "1.4k",
      "cost": 0.56,
      "cost_per_game": "$0.028"
    },
    {
      "model": "<a href=\"logs/2026-07-22T04-43-10_gpt5.6-terra.html\">openai/gpt-5.6-terra</a>",
      "date": "2026-07-22",
      "pts": 81,
      "pts_pct": 0.81,
      "w": "19",
      "grp": "78/80",
      "trap": "22",
      "inv": "0",
      "avg_time": "19s",
      "tok_per_game": "2.1k",
      "cost": 0.49,
      "cost_per_game": "$0.024"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-27-47_gemini-3.6-flash.html\">google/gemini-3.6-flash</a>",
      "date": "2026-07-22",
      "pts": 80,
      "pts_pct": 0.8,
      "w": "20",
      "grp": "80/80",
      "trap": "20",
      "inv": "0",
      "avg_time": "8s",
      "tok_per_game": "2.1k",
      "cost": 0.25,
      "cost_per_game": "$0.012"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-35-18_gpt5.6-terra-pro.html\">openai/gpt-5.6-terra-pro</a>",
      "date": "2026-07-22",
      "pts": 79,
      "pts_pct": 0.79,
      "w": "19",
      "grp": "78/80",
      "trap": "20",
      "inv": "0",
      "avg_time": "36s",
      "tok_per_game": "13.7k",
      "cost": 1.99,
      "cost_per_game": "$0.100"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-34-34_kimi-k3.html\">moonshotai/kimi-k3</a>",
      "date": "2026-07-22",
      "pts": 77,
      "pts_pct": 0.77,
      "w": "19",
      "grp": "78/80",
      "trap": "18",
      "inv": "0",
      "avg_time": "46s",
      "tok_per_game": "2.1k",
      "cost": 0.46,
      "cost_per_game": "$0.023"
    },
    {
      "model": "<a href=\"logs/2026-07-22T04-40-41_gemini-3-flash.html\">google/gemini-3-flash-preview</a>",
      "date": "2026-07-22",
      "pts": 75,
      "pts_pct": 0.75,
      "w": "19",
      "grp": "78/80",
      "trap": "16",
      "inv": "0",
      "avg_time": "15s",
      "tok_per_game": "3.5k",
      "cost": 0.18,
      "cost_per_game": "$0.009"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-39-25_gpt5.6-luna-pro.html\">openai/gpt-5.6-luna-pro</a>",
      "date": "2026-07-22",
      "pts": 72,
      "pts_pct": 0.72,
      "w": "18",
      "grp": "76/80",
      "trap": "14",
      "inv": "0",
      "avg_time": "23s",
      "tok_per_game": "11.9k",
      "cost": 0.66,
      "cost_per_game": "$0.033"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-42-38_sonnet-5.html\">anthropic/claude-sonnet-5</a>",
      "date": "2026-07-22",
      "pts": 68,
      "pts_pct": 0.68,
      "w": "18",
      "grp": "76/80",
      "trap": "10",
      "inv": "0",
      "avg_time": "13s",
      "tok_per_game": "2.1k",
      "cost": 0.28,
      "cost_per_game": "$0.014"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-44-26_opus-4.7.html\">anthropic/claude-opus-4.7</a>",
      "date": "2026-07-22",
      "pts": 65,
      "pts_pct": 0.65,
      "w": "19",
      "grp": "76/80",
      "trap": "8",
      "inv": "1",
      "avg_time": "14s",
      "tok_per_game": "1.7k",
      "cost": 0.49,
      "cost_per_game": "$0.024"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-28-27_gemini-3.5-flash.html\">google/gemini-3.5-flash</a>",
      "date": "2026-07-22",
      "pts": 63,
      "pts_pct": 0.63,
      "w": "16",
      "grp": "67/80",
      "trap": "12",
      "inv": "2",
      "avg_time": "8s",
      "tok_per_game": "2.1k",
      "cost": 0.3,
      "cost_per_game": "$0.015"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-29-58_gpt5.6-luna.html\">openai/gpt-5.6-luna</a>",
      "date": "2026-07-22",
      "pts": 62,
      "pts_pct": 0.62,
      "w": "14",
      "grp": "66/80",
      "trap": "10",
      "inv": "0",
      "avg_time": "13s",
      "tok_per_game": "1.9k",
      "cost": 0.17,
      "cost_per_game": "$0.008"
    },
    {
      "model": "<a href=\"logs/2026-07-22T05-29-12_gemini-3.5-flash-lite.html\">google/gemini-3.5-flash-lite</a>",
      "date": "2026-07-22",
      "pts": 54,
      "pts_pct": 0.54,
      "w": "11",
      "grp": "53/80",
      "trap": "12",
      "inv": "3",
      "avg_time": "8s",
      "tok_per_game": "3.0k",
      "cost": 0.12,
      "cost_per_game": "$0.006"
    }
  ],
  "size": [
    16,
    "auto"
  ],
  "sortable": true,
  "filter": true
}
```
