# Nine-day MVP build plan

## North star

A podcast creator opens the side panel, selects an episode and identity, receives a source-faithful storyboard, revises scenes, approves voice and avatar, renders two formats, and exports or publishes a result that wins a blind preference test against Gemini Notebook and Google Vids.

## Schedule

| Day | Deliverable | Binary proof |
|---:|---|---|
| 1 | public baseline, product contract, CI, clean architecture | repository public, tests green |
| 2 | side-panel shell, project state, `ProviderAdapter`, capability discovery | three providers return typed capabilities |
| 3 | local files, RSS, Drive/Docs/Slides intake | three source types produce one normalized source record |
| 4 | Agents SDK storyboard workflow, WebMCP tools, traces, loop guard | successful trace plus controlled loop eval |
| 5 | creator voice intake, consent receipt, narration provider | approved voice produces timed narration |
| 6 | avatar provider spike and one selected implementation | consented avatar renders one scene |
| 7 | Google Vids handoff/bridge and OpenAI adapter | same storyboard reaches both routes |
| 8 | 16:9 and 9:16 render, captions, YouTube package, QA | complete artifact manifest |
| 9 | five-source benchmark, polish, demo, Devpost evidence | scorecard, three-minute demo, submission pack |

## Scope locks

- One complete creator journey.
- Six adapter families.
- One voice provider and one avatar provider beyond the local/OpenAI baseline.
- Google Vids integration starts with a reliable handoff and advances to browser control only when the observed interface supports deterministic receipts.
- YouTube is the direct publishing target; other networks receive export packages.
- Every expensive or public action requires approval.
- Every traced failure that changes code adds an eval.

## Daily release gate

1. unit and contract tests pass;
2. one end-to-end happy path runs;
3. secrets scan returns clear;
4. source fidelity claims retain evidence;
5. side-panel actions return typed states;
6. the day’s demo remains under three minutes.
