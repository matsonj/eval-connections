---
theme: light
title: Connections Eval — One-Shot Box Score
orientation: landscape
continuous: true
---

Latest one-shot runs for 29 models (20 games each, one submission per game, max 100 pts; sorted by points, avg time, cost) · [Classic (multi-turn) leaderboard →](classic.html)

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
      "model": "<a href=\"logs/2026-07-22T22-58-18_muse-spark-1.1.html\">meta/muse-spark-1.1</a>",
      "date": "2026-07-22",
      "pts": 90,
      "pts_pct": 0.9,
      "w": "20",
      "grp": "80/80",
      "trap": "30",
      "inv": "0",
      "avg_time": "38s",
      "tok_per_game": "5.4k",
      "cost": 0.42,
      "cost_per_game": "$0.021"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-25-41_gemini-3.1.html\">google/gemini-3.1-pro-preview</a>",
      "date": "2026-07-22",
      "pts": 86,
      "pts_pct": 0.86,
      "w": "20",
      "grp": "80/80",
      "trap": "26",
      "inv": "0",
      "avg_time": "14s",
      "tok_per_game": "2.2k",
      "cost": 0.42,
      "cost_per_game": "$0.021"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-01-37_gpt5.6-terra.html\">openai/gpt-5.6-terra</a>",
      "date": "2026-07-22",
      "pts": 86,
      "pts_pct": 0.86,
      "w": "20",
      "grp": "80/80",
      "trap": "26",
      "inv": "0",
      "avg_time": "19s",
      "tok_per_game": "2.0k",
      "cost": 0.47,
      "cost_per_game": "$0.024"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-10-40_fable-5.html\">anthropic/claude-fable-5</a>",
      "date": "2026-07-22",
      "pts": 85,
      "pts_pct": 0.85,
      "w": "19",
      "grp": "78/80",
      "trap": "26",
      "inv": "0",
      "avg_time": "33s",
      "tok_per_game": "1.7k",
      "cost": 1.08,
      "cost_per_game": "$0.054"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-56-32_gemini-3-flash.html\">google/gemini-3-flash-preview</a>",
      "date": "2026-07-22",
      "pts": 84,
      "pts_pct": 0.84,
      "w": "20",
      "grp": "80/80",
      "trap": "24",
      "inv": "0",
      "avg_time": "10s",
      "tok_per_game": "2.6k",
      "cost": 0.13,
      "cost_per_game": "$0.006"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-13-53_gpt5.6-sol-pro.html\">openai/gpt-5.6-sol-pro</a>",
      "date": "2026-07-22",
      "pts": 84,
      "pts_pct": 0.84,
      "w": "20",
      "grp": "80/80",
      "trap": "24",
      "inv": "0",
      "avg_time": "47s",
      "tok_per_game": "10.0k",
      "cost": 2.68,
      "cost_per_game": "$0.134"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-23-15_kimi-k3.html\">moonshotai/kimi-k3</a>",
      "date": "2026-07-22",
      "pts": 84,
      "pts_pct": 0.84,
      "w": "18",
      "grp": "74/80",
      "trap": "28",
      "inv": "0",
      "avg_time": "51s",
      "tok_per_game": "2.3k",
      "cost": 0.55,
      "cost_per_game": "$0.027"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-29-09_fugu-ultra.html\">sakana/fugu-ultra</a>",
      "date": "2026-07-22",
      "pts": 84,
      "pts_pct": 0.84,
      "w": "20",
      "grp": "80/80",
      "trap": "24",
      "inv": "0",
      "avg_time": "2m58s",
      "tok_per_game": "24.1k",
      "cost": 9.54,
      "cost_per_game": "$0.477"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-56-31_gpt5.6-sol.html\">openai/gpt-5.6-sol</a>",
      "date": "2026-07-22",
      "pts": 83,
      "pts_pct": 0.83,
      "w": "17",
      "grp": "72/80",
      "trap": "28",
      "inv": "1",
      "avg_time": "27s",
      "tok_per_game": "1.5k",
      "cost": 0.63,
      "cost_per_game": "$0.032"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-18-41_grok-4.20.html\">x-ai/grok-4.20</a>",
      "date": "2026-07-22",
      "pts": 82,
      "pts_pct": 0.82,
      "w": "20",
      "grp": "80/80",
      "trap": "22",
      "inv": "0",
      "avg_time": "1m20s",
      "tok_per_game": "7.9k",
      "cost": 0.37,
      "cost_per_game": "$0.019"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-13-27_gpt5.6-terra-pro.html\">openai/gpt-5.6-terra-pro</a>",
      "date": "2026-07-22",
      "pts": 77,
      "pts_pct": 0.77,
      "w": "19",
      "grp": "78/80",
      "trap": "18",
      "inv": "0",
      "avg_time": "36s",
      "tok_per_game": "13.4k",
      "cost": 1.98,
      "cost_per_game": "$0.099"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-26-38_grok-4.5.html\">x-ai/grok-4.5</a>",
      "date": "2026-07-22",
      "pts": 76,
      "pts_pct": 0.76,
      "w": "18",
      "grp": "74/80",
      "trap": "20",
      "inv": "1",
      "avg_time": "1m3s",
      "tok_per_game": "4.4k",
      "cost": 0.44,
      "cost_per_game": "$0.022"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-18-52_glm-5v-turbo.html\">z-ai/glm-5v-turbo</a>",
      "date": "2026-07-22",
      "pts": 74,
      "pts_pct": 0.74,
      "w": "16",
      "grp": "70/80",
      "trap": "20",
      "inv": "0",
      "avg_time": "1m6s",
      "tok_per_game": "5.8k",
      "cost": 0.44,
      "cost_per_game": "$0.022"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-54-59_sonnet-5.html\">anthropic/claude-sonnet-5</a>",
      "date": "2026-07-22",
      "pts": 71,
      "pts_pct": 0.71,
      "w": "16",
      "grp": "71/80",
      "trap": "16",
      "inv": "0",
      "avg_time": "15s",
      "tok_per_game": "2.1k",
      "cost": 0.3,
      "cost_per_game": "$0.015"
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
      "model": "<a href=\"logs/2026-07-22T22-54-40_gemini-3.6-flash.html\">google/gemini-3.6-flash</a>",
      "date": "2026-07-22",
      "pts": 69,
      "pts_pct": 0.69,
      "w": "19",
      "grp": "76/80",
      "trap": "12",
      "inv": "0",
      "avg_time": "11s",
      "tok_per_game": "2.8k",
      "cost": 0.36,
      "cost_per_game": "$0.018"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-59-11_gpt5.6-luna-pro.html\">openai/gpt-5.6-luna-pro</a>",
      "date": "2026-07-22",
      "pts": 68,
      "pts_pct": 0.68,
      "w": "18",
      "grp": "74/80",
      "trap": "12",
      "inv": "0",
      "avg_time": "21s",
      "tok_per_game": "11.9k",
      "cost": 0.68,
      "cost_per_game": "$0.034"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-41-49_deepseek-v4-flash.html\">deepseek/deepseek-v4-flash</a>",
      "date": "2026-07-22",
      "pts": 68,
      "pts_pct": 0.7158,
      "w": "14",
      "grp": "64/80",
      "trap": "18",
      "inv": "1",
      "avg_time": "7m12s",
      "tok_per_game": "15.0k",
      "cost": 0.08,
      "cost_per_game": "$0.004"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-51-23_gemini-3.5-flash-lite.html\">google/gemini-3.5-flash-lite</a>",
      "date": "2026-07-22",
      "pts": 67,
      "pts_pct": 0.67,
      "w": "16",
      "grp": "69/80",
      "trap": "14",
      "inv": "0",
      "avg_time": "8s",
      "tok_per_game": "2.8k",
      "cost": 0.12,
      "cost_per_game": "$0.006"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-52-12_gemini-3.5-flash.html\">google/gemini-3.5-flash</a>",
      "date": "2026-07-22",
      "pts": 67,
      "pts_pct": 0.67,
      "w": "17",
      "grp": "68/80",
      "trap": "16",
      "inv": "3",
      "avg_time": "10s",
      "tok_per_game": "2.7k",
      "cost": 0.42,
      "cost_per_game": "$0.021"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-40-44_inkling.html\">thinkingmachines/inkling</a>",
      "date": "2026-07-22",
      "pts": 67,
      "pts_pct": 0.67,
      "w": "16",
      "grp": "67/80",
      "trap": "16",
      "inv": "1",
      "avg_time": "2m42s",
      "tok_per_game": "3.2k",
      "cost": 0.22,
      "cost_per_game": "$0.011"
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
      "model": "<a href=\"logs/2026-07-22T23-53-05_step3.7-flash.html\">stepfun/step-3.7-flash</a>",
      "date": "2026-07-22",
      "pts": 63,
      "pts_pct": 0.63,
      "w": "12",
      "grp": "61/80",
      "trap": "14",
      "inv": "1",
      "avg_time": "1m23s",
      "tok_per_game": "14.3k",
      "cost": 0.32,
      "cost_per_game": "$0.016"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-27-34_gpt5.6-luna.html\">openai/gpt-5.6-luna</a>",
      "date": "2026-07-22",
      "pts": 62,
      "pts_pct": 0.62,
      "w": "13",
      "grp": "63/80",
      "trap": "12",
      "inv": "1",
      "avg_time": "16s",
      "tok_per_game": "2.1k",
      "cost": 0.2,
      "cost_per_game": "$0.010"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-08-13_haiku-4.5.html\">anthropic/claude-haiku-4.5</a>",
      "date": "2026-07-22",
      "pts": 60,
      "pts_pct": 0.6,
      "w": "13",
      "grp": "59/80",
      "trap": "14",
      "inv": "1",
      "avg_time": "30s",
      "tok_per_game": "4.3k",
      "cost": 0.39,
      "cost_per_game": "$0.019"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-52-28_mercury-2.html\">inception/mercury-2</a>",
      "date": "2026-07-22",
      "pts": 39,
      "pts_pct": 0.39,
      "w": "4",
      "grp": "29/80",
      "trap": "14",
      "inv": "3",
      "avg_time": "4s",
      "tok_per_game": "2.6k",
      "cost": 0.03,
      "cost_per_game": "$0.002"
    },
    {
      "model": "<a href=\"logs/2026-07-23T00-09-02_qwen3-max-thinking.html\">qwen/qwen3-max-thinking</a>",
      "date": "2026-07-23",
      "pts": 35,
      "pts_pct": 0.35,
      "w": "10",
      "grp": "43/80",
      "trap": "2",
      "inv": "7",
      "avg_time": "15m58s",
      "tok_per_game": "41.7k",
      "cost": 2.49,
      "cost_per_game": "$0.125"
    },
    {
      "model": "<a href=\"logs/2026-07-22T23-07-43_laguna-xs.2.html\">poolside/laguna-xs-2.1</a>",
      "date": "2026-07-22",
      "pts": 17,
      "pts_pct": 0.17,
      "w": "2",
      "grp": "17/80",
      "trap": "2",
      "inv": "5",
      "avg_time": "1m59s",
      "tok_per_game": "19.4k",
      "cost": 0.05,
      "cost_per_game": "$0.002"
    },
    {
      "model": "<a href=\"logs/2026-07-22T22-58-03_granite-4.1-8b.html\">ibm-granite/granite-4.1-8b</a>",
      "date": "2026-07-22",
      "pts": 5,
      "pts_pct": 0.05,
      "w": "1",
      "grp": "6/80",
      "trap": "0",
      "inv": "10",
      "avg_time": "8s",
      "tok_per_game": "1.4k",
      "cost": 0.0,
      "cost_per_game": "$0.000"
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
