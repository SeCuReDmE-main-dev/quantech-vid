# Integration catalog — 40 useful tools for podcast creators

## Selection rule

The catalog identifies high-value destinations and creative tools. It represents an opportunity map rather than a nine-day implementation promise. Each integration enters through an official API, a trusted MCP server, import/export, or a consented browser bridge.

| # | Tool | Creator value | Preferred route | MVP |
|---:|---|---|---|---:|
| 1 | [Google Drive](https://developers.google.com/drive) | source and asset library | official API | Yes |
| 2 | [Google Docs](https://developers.google.com/docs) | scripts and research | official API | Yes |
| 3 | [Google Slides](https://developers.google.com/slides) | scene and deck import | official API | Yes |
| 4 | [Google Vids](https://workspace.google.com/products/vids/) | collaborative scenes, avatar, voice, media | handoff plus experimental bridge | Yes |
| 5 | [Dropbox](https://developers.dropbox.com/) | audio and asset intake | official API | Later |
| 6 | [Microsoft OneDrive](https://learn.microsoft.com/graph/onedrive-concept-overview) | Microsoft file intake | Graph API or MCP | Later |
| 7 | [Box](https://developer.box.com/) | team media library | official API | Later |
| 8 | [Notion](https://developers.notion.com/) | episode briefs and knowledge | official MCP or API | Later |
| 9 | [Podcast Index](https://podcastindex-org.github.io/docs-api/) | podcast/RSS discovery | official API | Yes |
| 10 | [YouTube Podcasts](https://support.google.com/youtube/answer/13525207) | RSS podcast intake and distribution | RSS handoff | Later |
| 11 | [Spotify for Creators](https://creators.spotify.com/) | podcast source and distribution | RSS/export | Later |
| 12 | [Apple Podcasts](https://podcasters.apple.com/) | feed validation and distribution | RSS/export | Later |
| 13 | [Riverside](https://riverside.fm/) | multitrack recording intake | export/API when available | Later |
| 14 | [Descript](https://www.descript.com/api) | transcript editing, cleanup, clips | API and MCP | Later |
| 15 | [Zoom](https://developers.zoom.us/) | interview recordings | official API | Later |
| 16 | [Google Meet](https://developers.google.com/workspace/meet) | meeting recordings and transcripts | official API | Later |
| 17 | [OpenAI](https://developers.openai.com/) | agent orchestration and media generation | Agents SDK and APIs | Yes |
| 18 | [Google Gemini](https://ai.google.dev/gemini-api/docs) | Gemini planning and media capabilities | official API | Yes |
| 19 | [ElevenLabs](https://elevenlabs.io/docs/api-reference) | creator voice and dubbing | official API | Candidate |
| 20 | [HeyGen](https://docs.heygen.com/) | avatar video | official API | Candidate |
| 21 | [Tavus](https://docs.tavus.io/) | consented replica and avatar video | official API | Candidate |
| 22 | [D-ID](https://docs.d-id.com/) | talking avatar from image/audio | official API | Candidate |
| 23 | [Runway](https://docs.dev.runwayml.com/) | generated and edited video clips | official API | Later |
| 24 | [Replicate](https://replicate.com/docs) | pluggable media models | official API | Later |
| 25 | [Canva](https://www.canva.dev/docs/) | brand assets and templates | official API/app | Later |
| 26 | [Figma](https://www.figma.com/developers/api) | reusable visual system | official API | Later |
| 27 | [Adobe](https://developer.adobe.com/) | Premiere/Express workflows | official APIs/export | Later |
| 28 | [Pexels](https://www.pexels.com/api/documentation/) | licensed stock photo/video | official API | Yes |
| 29 | [Unsplash](https://unsplash.com/developers) | licensed photography | official API | Later |
| 30 | [Microsoft Clipchamp](https://support.microsoft.com/clipchamp) | Microsoft timeline editing and Copilot Create handoff | Microsoft handoff/API when available | Later |
| 31 | [YouTube](https://developers.google.com/youtube/v3) | video upload and metadata | official API | Yes |
| 32 | [Vimeo](https://developer.vimeo.com/api/reference) | video hosting and review | official API/MCP | Later |
| 33 | [TikTok](https://developers.tiktok.com/products/content-posting-api/) | vertical publishing | official API | Later |
| 34 | [Instagram and Facebook](https://developers.facebook.com/docs/instagram-platform/content-publishing/) | Reels and social publishing | Graph API | Later |
| 35 | [LinkedIn](https://learn.microsoft.com/linkedin/marketing/community-management/shares/videos-api) | professional video publishing | official API | Later |
| 36 | [X](https://docs.x.com/x-api/media/quickstart/media-upload-chunked) | social video publishing | official API | Later |
| 37 | [n8n](https://docs.n8n.io/advanced-ai/accessing-n8n-mcp-server/) | self-hosted automation and MCP | MCP | Candidate |
| 38 | [Zapier](https://docs.zapier.com/platform/home) | creator workflow automation | API/MCP | Later |
| 39 | [Make](https://developers.make.com/) | visual automation | API | Later |
| 40 | [GitHub](https://docs.github.com/en/rest) | project assets, issues, releases | official MCP or API | Yes |

## Nine-day adapter set

Build six bounded adapters rather than forty bespoke integrations:

1. `local_media` — files, rendering, captions, provenance;
2. `google_workspace` — Drive/Docs/Slides intake plus Google Vids handoff;
3. `openai` — Agents SDK, narration, generation tools, traces;
4. `podcast_rss` — Podcast Index and direct feeds;
5. `identity_media` — one voice provider and one avatar provider selected by a day-two spike;
6. `publish` — YouTube upload plus downloadable packages.

The generic MCP gateway proves extensibility through one trusted server, with n8n or GitHub as the leading candidates.
