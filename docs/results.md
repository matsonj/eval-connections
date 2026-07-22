---
theme: light
title: Connections Eval — One-Shot Box Score
orientation: landscape
continuous: true
---

Latest one-shot runs for 16 models (20 games each, one submission per game, max 100 pts; sorted by points, avg time, cost) · [Classic (multi-turn) leaderboard →](classic.html)

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
      "model": "<a href=\"logs/2026-07-22T16-37-32_fable-5.html\">anthropic/claude-fable-5</a>",
      "date": "2026-07-22",
      "pts": 82,
      "pts_pct": 0.82,
      "w": "20",
      "grp": "80/80",
      "trap": "22",
      "inv": "0",
      "avg_time": "32s",
      "tok_per_game": "1.8k",
      "cost": 1.01,
      "cost_per_game": "$0.051"
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
      "model": "<a href=\"logs/2026-07-22T20-19-02_gemini-3.6-flash.html\">google/gemini-3.6-flash</a>",
      "date": "2026-07-22",
      "pts": 78,
      "pts_pct": 0.78,
      "w": "20",
      "grp": "80/80",
      "trap": "18",
      "inv": "0",
      "avg_time": "9s",
      "tok_per_game": "2.2k",
      "cost": 0.28,
      "cost_per_game": "$0.014"
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
      "model": "<a href=\"logs/2026-07-22T20-48-02_grok-4.5.html\">x-ai/grok-4.5</a>",
      "date": "2026-07-22",
      "pts": 76,
      "pts_pct": 0.76,
      "w": "18",
      "grp": "74/80",
      "trap": "20",
      "inv": "1",
      "avg_time": "1m28s",
      "tok_per_game": "4.8k",
      "cost": 0.51,
      "cost_per_game": "$0.026"
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
      "model": "<a href=\"logs/2026-07-22T17-22-54_opus-4.5.html\">anthropic/claude-opus-4.5</a>",
      "date": "2026-07-22",
      "pts": 70,
      "pts_pct": 0.7,
      "w": "18",
      "grp": "74/80",
      "trap": "14",
      "inv": "0",
      "avg_time": "56s",
      "tok_per_game": "4.1k",
      "cost": 1.82,
      "cost_per_game": "$0.091"
    },
    {
      "model": "<a href=\"logs/2026-07-22T18-36-41_gemini-3-flash.html\">google/gemini-3-flash-preview</a>",
      "date": "2026-07-22",
      "pts": 69,
      "pts_pct": 0.69,
      "w": "19",
      "grp": "76/80",
      "trap": "12",
      "inv": "1",
      "avg_time": "14s",
      "tok_per_game": "3.3k",
      "cost": 0.17,
      "cost_per_game": "$0.009"
    },
    {
      "model": "<a href=\"logs/2026-07-22T17-27-20_gpt5.6-terra.html\">openai/gpt-5.6-terra</a>",
      "date": "2026-07-22",
      "pts": 66,
      "pts_pct": 0.66,
      "w": "18",
      "grp": "74/80",
      "trap": "10",
      "inv": "0",
      "avg_time": "28s",
      "tok_per_game": "2.3k",
      "cost": 0.57,
      "cost_per_game": "$0.029"
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
      "model": "<a href=\"logs/2026-07-22T17-47-44_gpt5.6-luna.html\">openai/gpt-5.6-luna</a>",
      "date": "2026-07-22",
      "pts": 64,
      "pts_pct": 0.64,
      "w": "16",
      "grp": "70/80",
      "trap": "10",
      "inv": "1",
      "avg_time": "1m44s",
      "tok_per_game": "13.5k",
      "cost": 1.57,
      "cost_per_game": "$0.079"
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
      "model": "<a href=\"logs/2026-07-22T17-21-46_sonnet-5.html\">anthropic/claude-sonnet-5</a>",
      "date": "2026-07-22",
      "pts": 59,
      "pts_pct": 0.59,
      "w": "17",
      "grp": "72/80",
      "trap": "4",
      "inv": "0",
      "avg_time": "14s",
      "tok_per_game": "1.9k",
      "cost": 0.28,
      "cost_per_game": "$0.014"
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
