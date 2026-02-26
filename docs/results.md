---
theme: light
title: Connections Evaluation Box Score
continuous: true
---

Latest runs for 36 models (20 games each, sorted by solve rate, avg time, cost)

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
      "id": "w",
      "title": "W",
      "align": "right"
    },
    {
      "id": "win_pct",
      "title": "WIN%",
      "align": "right",
      "bold": true,
      "type": "heatmap",
      "higherIsBetter": true,
      "fmt": "pct1"
    },
    {
      "id": "hit_att",
      "title": "HIT/ATT",
      "align": "right"
    },
    {
      "id": "acc_pct",
      "title": "ACC%",
      "align": "right",
      "bold": true,
      "type": "heatmap",
      "higherIsBetter": true,
      "fmt": "pct1"
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
      "model": "<a href=\"logs/2026-02-24T18-55-13_gemini-3-flash.html\">google/gemini-3-flash-preview</a>",
      "date": "2026-02-24",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/85",
      "acc_pct": 0.9412,
      "avg_time": "10s",
      "tok_per_game": "4.9k",
      "cost": 0.1,
      "cost_per_game": "$0.005"
    },
    {
      "model": "<a href=\"logs/2026-02-17T03-41-11_opus-4.5.html\">anthropic/claude-opus-4.5</a>",
      "date": "2026-02-17",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/84",
      "acc_pct": 0.9524,
      "avg_time": "39s",
      "tok_per_game": "7.9k",
      "cost": 1.66,
      "cost_per_game": "$0.083"
    },
    {
      "model": "<a href=\"logs/2025-11-18T16-59-04_gemini-3-preview.html\">google/gemini-3-pro-preview</a>",
      "date": "2025-11-18",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/81",
      "acc_pct": 0.9877,
      "avg_time": "1m13s",
      "tok_per_game": "8.8k",
      "cost": 1.55,
      "cost_per_game": "$0.078"
    },
    {
      "model": "<a href=\"logs/2026-02-24T18-46-38_gemini-3.1.html\">google/gemini-3.1-pro-preview</a>",
      "date": "2026-02-24",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/80",
      "acc_pct": 1.0,
      "avg_time": "1m15s",
      "tok_per_game": "7.8k",
      "cost": 1.31,
      "cost_per_game": "$0.066"
    },
    {
      "model": "<a href=\"logs/2026-02-23T23-44-16_opus-4.6.html\">anthropic/claude-opus-4.6</a>",
      "date": "2026-02-23",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/80",
      "acc_pct": 1.0,
      "avg_time": "1m25s",
      "tok_per_game": "17.7k",
      "cost": 3.53,
      "cost_per_game": "$0.176"
    },
    {
      "model": "<a href=\"logs/2026-02-24T04-49-45_sonnet-4.6.html\">anthropic/claude-sonnet-4.6</a>",
      "date": "2026-02-24",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/80",
      "acc_pct": 1.0,
      "avg_time": "1m51s",
      "tok_per_game": "29.4k",
      "cost": 3.53,
      "cost_per_game": "$0.177"
    },
    {
      "model": "<a href=\"logs/2025-12-12T04-40-34_o3.html\">openai/o3</a>",
      "date": "2025-12-12",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/81",
      "acc_pct": 0.9877,
      "avg_time": "2m13s",
      "tok_per_game": "11.5k",
      "cost": 1.58,
      "cost_per_game": "$0.079"
    },
    {
      "model": "<a href=\"logs/2025-10-03T06-46-18_grok4-fast.html\">x-ai/grok-4-fast</a>",
      "date": "2025-10-03",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/83",
      "acc_pct": 0.9639,
      "avg_time": "2m51s",
      "tok_per_game": "8.1k",
      "cost": 0.06,
      "cost_per_game": "$0.003"
    },
    {
      "model": "<a href=\"logs/2025-10-18T23-08-46_grok4.html\">x-ai/grok-4</a>",
      "date": "2025-10-18",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/82",
      "acc_pct": 0.9756,
      "avg_time": "3m28s",
      "tok_per_game": "13.6k",
      "cost": 2.53,
      "cost_per_game": "$0.127"
    },
    {
      "model": "<a href=\"logs/2025-12-11T21-05-29_gpt5.2-pro.html\">openai/gpt-5.2-pro</a>",
      "date": "2025-12-11",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/80",
      "acc_pct": 1.0,
      "avg_time": "3m42s",
      "tok_per_game": "4.1k",
      "cost": 8.66,
      "cost_per_game": "$0.433"
    },
    {
      "model": "<a href=\"logs/2026-02-24T22-12-30_kimi-k2.5.html\">moonshotai/kimi-k2.5</a>",
      "date": "2026-02-24",
      "w": "20",
      "win_pct": 1.0,
      "hit_att": "80/89",
      "acc_pct": 0.8989,
      "avg_time": "8m14s",
      "tok_per_game": "13.4k",
      "cost": 0.51,
      "cost_per_game": "$0.026"
    },
    {
      "model": "<a href=\"logs/2026-02-15T20-28-26_qwen3-max-thinking.html\">qwen/qwen3-max-thinking</a>",
      "date": "2026-02-15",
      "w": "19",
      "win_pct": 0.95,
      "hit_att": "78/87",
      "acc_pct": 0.8966,
      "avg_time": "2m48s",
      "tok_per_game": "15.3k",
      "cost": 0.88,
      "cost_per_game": "$0.044"
    },
    {
      "model": "<a href=\"logs/2026-02-24T03-32-40_glm-5.html\">z-ai/glm-5</a>",
      "date": "2026-02-24",
      "w": "19",
      "win_pct": 0.95,
      "hit_att": "77/89",
      "acc_pct": 0.8652,
      "avg_time": "3m11s",
      "tok_per_game": "9.2k",
      "cost": 0.41,
      "cost_per_game": "$0.020"
    },
    {
      "model": "<a href=\"logs/2026-01-30T22-42-20_glm-4.7.html\">z-ai/glm-4.7</a>",
      "date": "2026-01-30",
      "w": "19",
      "win_pct": 0.95,
      "hit_att": "77/88",
      "acc_pct": 0.875,
      "avg_time": "5m18s",
      "tok_per_game": "21.0k",
      "cost": 0.76,
      "cost_per_game": "$0.038"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-33-44_gemini-2.5.html\">google/gemini-2.5-pro</a>",
      "date": "2025-10-18",
      "w": "18",
      "win_pct": 0.9,
      "hit_att": "75/89",
      "acc_pct": 0.8427,
      "avg_time": "1m1s",
      "tok_per_game": "11.4k",
      "cost": 1.3,
      "cost_per_game": "$0.065"
    },
    {
      "model": "<a href=\"logs/2025-12-19T00-45-53_gpt5-mini.html\">openai/gpt-5-mini</a>",
      "date": "2025-12-19",
      "w": "18",
      "win_pct": 0.9,
      "hit_att": "69/84",
      "acc_pct": 0.8214,
      "avg_time": "1m51s",
      "tok_per_game": "7.9k",
      "cost": 0.24,
      "cost_per_game": "$0.012"
    },
    {
      "model": "<a href=\"logs/2026-02-15T17-27-29_step3.5-flash.html\">stepfun/step-3.5-flash</a>",
      "date": "2026-02-15",
      "w": "18",
      "win_pct": 0.9,
      "hit_att": "76/92",
      "acc_pct": 0.8261,
      "avg_time": "5m42s",
      "tok_per_game": "93.1k",
      "cost": 0.43,
      "cost_per_game": "$0.021"
    },
    {
      "model": "<a href=\"logs/2025-12-19T01-11-11_sonnet-4.5.html\">anthropic/claude-4.5-sonnet</a>",
      "date": "2025-12-19",
      "w": "17",
      "win_pct": 0.85,
      "hit_att": "59/86",
      "acc_pct": 0.686,
      "avg_time": "38s",
      "tok_per_game": "7.1k",
      "cost": 0.91,
      "cost_per_game": "$0.045"
    },
    {
      "model": "<a href=\"logs/2025-12-02T23-21-12_deepseek-v3.2.html\">deepseek/deepseek-v3.2</a>",
      "date": "2025-12-02",
      "w": "17",
      "win_pct": 0.85,
      "hit_att": "72/92",
      "acc_pct": 0.7826,
      "avg_time": "4m21s",
      "tok_per_game": "13.4k",
      "cost": 0.08,
      "cost_per_game": "$0.004"
    },
    {
      "model": "<a href=\"logs/2025-11-12T20-08-45_kimi-k2-thinking.html\">moonshotai/kimi-k2-thinking</a>",
      "date": "2025-11-12",
      "w": "17",
      "win_pct": 0.85,
      "hit_att": "74/101",
      "acc_pct": 0.7327,
      "avg_time": "8m26s",
      "tok_per_game": "17.9k",
      "cost": 0.65,
      "cost_per_game": "$0.032"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-31-21_deepseek-r1.html\">deepseek/deepseek-r1-0528</a>",
      "date": "2025-10-18",
      "w": "16",
      "win_pct": 0.8,
      "hit_att": "69/99",
      "acc_pct": 0.697,
      "avg_time": "9m14s",
      "tok_per_game": "21.4k",
      "cost": 0.98,
      "cost_per_game": "$0.049"
    },
    {
      "model": "<a href=\"logs/2025-12-19T01-12-19_gpt5.2.html\">openai/gpt-5.2</a>",
      "date": "2025-12-19",
      "w": "15",
      "win_pct": 0.75,
      "hit_att": "56/83",
      "acc_pct": 0.6747,
      "avg_time": "42s",
      "tok_per_game": "3.9k",
      "cost": 0.64,
      "cost_per_game": "$0.032"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-34-10_gpt-oss-120b.html\">openai/gpt-oss-120b</a>",
      "date": "2025-10-18",
      "w": "14",
      "win_pct": 0.7,
      "hit_att": "65/111",
      "acc_pct": 0.5856,
      "avg_time": "2m26s",
      "tok_per_game": "19.4k",
      "cost": 0.12,
      "cost_per_game": "$0.006"
    },
    {
      "model": "<a href=\"logs/2025-12-19T01-20-54_haiku-4.5.html\">anthropic/claude-haiku-4.5</a>",
      "date": "2025-12-19",
      "w": "13",
      "win_pct": 0.65,
      "hit_att": "47/94",
      "acc_pct": 0.5,
      "avg_time": "37s",
      "tok_per_game": "12.5k",
      "cost": 0.56,
      "cost_per_game": "$0.028"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-57-26_qwen3-max.html\">qwen/qwen3-max</a>",
      "date": "2025-10-18",
      "w": "13",
      "win_pct": 0.65,
      "hit_att": "63/106",
      "acc_pct": 0.5943,
      "avg_time": "2m9s",
      "tok_per_game": "13.7k",
      "cost": 0.69,
      "cost_per_game": "$0.034"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-58-13_kimi-k2.html\">moonshotai/kimi-k2-0905</a>",
      "date": "2025-10-18",
      "w": "10",
      "win_pct": 0.5,
      "hit_att": "53/100",
      "acc_pct": 0.53,
      "avg_time": "1m9s",
      "tok_per_game": "7.1k",
      "cost": 0.15,
      "cost_per_game": "$0.008"
    },
    {
      "model": "<a href=\"logs/2025-12-20T01-17-29_o4-mini.html\">openai/o4-mini</a>",
      "date": "2025-12-20",
      "w": "9",
      "win_pct": 0.45,
      "hit_att": "35/97",
      "acc_pct": 0.3608,
      "avg_time": "3m33s",
      "tok_per_game": "29.3k",
      "cost": 2.41,
      "cost_per_game": "$0.121"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-34-33_gpt-oss-20b.html\">openai/gpt-oss-20b</a>",
      "date": "2025-10-18",
      "w": "8",
      "win_pct": 0.4,
      "hit_att": "45/108",
      "acc_pct": 0.4167,
      "avg_time": "4m4s",
      "tok_per_game": "34.4k",
      "cost": 0.12,
      "cost_per_game": "$0.006"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-29-21_glm-4.6.html\">z-ai/glm-4.6</a>",
      "date": "2025-10-18",
      "w": "7",
      "win_pct": 0.35,
      "hit_att": "45/115",
      "acc_pct": 0.3913,
      "avg_time": "1m41s",
      "tok_per_game": "8.2k",
      "cost": 0.17,
      "cost_per_game": "$0.008"
    },
    {
      "model": "<a href=\"logs/2025-12-20T01-17-14_o3-mini.html\">openai/o3-mini</a>",
      "date": "2025-12-20",
      "w": "5",
      "win_pct": 0.25,
      "hit_att": "25/100",
      "acc_pct": 0.25,
      "avg_time": "2m38s",
      "tok_per_game": "21.6k",
      "cost": 1.72,
      "cost_per_game": "$0.086"
    },
    {
      "model": "<a href=\"logs/2026-02-14T22-51-48_minimax-m2.5.html\">minimax/minimax-m2.5</a>",
      "date": "2026-02-14",
      "w": "3",
      "win_pct": 0.15,
      "hit_att": "24/95",
      "acc_pct": 0.2526,
      "avg_time": "2m5s",
      "tok_per_game": "8.8k",
      "cost": 0.13,
      "cost_per_game": "$0.007"
    },
    {
      "model": "<a href=\"logs/2025-12-20T01-18-27_nova-pro.html\">amazon/nova-pro-v1</a>",
      "date": "2025-12-20",
      "w": "1",
      "win_pct": 0.05,
      "hit_att": "8/90",
      "acc_pct": 0.0889,
      "avg_time": "11s",
      "tok_per_game": "4.7k",
      "cost": 0.13,
      "cost_per_game": "$0.006"
    },
    {
      "model": "<a href=\"logs/2025-11-06T23-17-31_phi-4.html\">microsoft/phi-4</a>",
      "date": "2025-11-06",
      "w": "1",
      "win_pct": 0.05,
      "hit_att": "17/102",
      "acc_pct": 0.1667,
      "avg_time": "47s",
      "tok_per_game": "7.5k",
      "cost": 0.01,
      "cost_per_game": "$0.001"
    },
    {
      "model": "<a href=\"logs/2025-12-20T01-17-42_llama-3.3.html\">meta-llama/llama-3.3-70b-instruct</a>",
      "date": "2025-12-20",
      "w": "1",
      "win_pct": 0.05,
      "hit_att": "7/97",
      "acc_pct": 0.0722,
      "avg_time": "50s",
      "tok_per_game": "4.5k",
      "cost": 0.03,
      "cost_per_game": "$0.001"
    },
    {
      "model": "<a href=\"logs/2025-12-20T01-18-00_mistral-large.html\">mistralai/mistral-large</a>",
      "date": "2025-12-20",
      "w": "1",
      "win_pct": 0.05,
      "hit_att": "9/99",
      "acc_pct": 0.0909,
      "avg_time": "58s",
      "tok_per_game": "10.6k",
      "cost": 0.68,
      "cost_per_game": "$0.034"
    },
    {
      "model": "<a href=\"logs/2025-10-18T19-29-16_ernie-4.5.html\">baidu/ernie-4.5-21b-a3b-thinking</a>",
      "date": "2025-10-18",
      "w": "0",
      "win_pct": 0.0,
      "hit_att": "18/99",
      "acc_pct": 0.1818,
      "avg_time": "3m22s",
      "tok_per_game": "22.3k",
      "cost": 0.11,
      "cost_per_game": "$0.005"
    }
  ],
  "size": [
    16,
    "auto"
  ]
}
```
