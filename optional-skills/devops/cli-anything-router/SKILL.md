---
name: cli-anything-router
description: Routing decisions for when to delegate software-operation tasks to the cli-anything subagent. Covers 35+ GUI and service applications (GIMP, Blender, LibreOffice, Ollama, ComfyUI, n8n, Grafana, Jenkins, Mermaid, etc.). Read this skill when the user asks to operate external software.
---

# CLI-Anything Router

**For the main agent.** This skill tells you when to delegate a software-operation
task to the `cli-anything` subagent, and when to handle the request yourself.

## Decision rule

Delegate to a focused subagent when the user asks to operate a real-world
application — meaning the task requires driving a specific piece of software
to produce a concrete artifact (file, rendering, export, API call against a
running service).

```
delegate_task(
    goal="<describe the end-to-end software task>",
    toolsets=["cli_anything_core"],
    role="leaf",
    context="<inputs, file paths, constraints>",
)
```

The ``cli_anything_core`` toolset scopes the child to the four CLI-Anything
tools only: ``cli_anything_list``, ``cli_anything_describe``,
``cli_anything_invoke``, ``cli_anything_install``.  The child cannot touch
``terminal``, ``write_file``, etc. — this is the sandbox the self-check
report asked for.

Do **not** delegate for:

- Pure text questions ("what is X?", "how does Y work?")
- Simple file reads/writes the `read_file` / `write_file` tools can handle
- Arbitrary shell commands (`terminal` is the right tool)
- Code changes within this repo (use `patch` / `write_file`)

## Trigger map

Match by keyword/intent. Chinese and English are both listed.

| Domain | Triggers | Harness family |
|-------|----------|---------------|
| Image editing | 图片/P图/修图/滤镜/图层/抠图 · image/photo/filter/layer | gimp, inkscape |
| 3D & rendering | 3D/建模/渲染/动画/材质 · 3d/render/model | blender |
| Video editing | 视频/剪辑/字幕/转码 · video/edit/subtitle/transcode | kdenlive, shotcut, videocaptioner |
| Audio | 音频/降噪/混音/频谱 · audio/denoise/mix/spectrogram | audacity |
| Documents | PDF/文档/导出/排版/PPT · document/pdf/export/slides | libreoffice |
| Diagrams | 流程图/架构图/UML/思维导图 · diagram/flowchart/uml | mermaid, plantuml, drawio, excalidraw |
| Local LLM | 本地模型/ollama/llama.cpp · local llm/ollama | ollama |
| AI image generation | AI绘图/SD/stable diffusion · ai image/sd | comfyui |
| Music notation | 乐谱/五线谱/musescore · music score | musescore |
| Reference mgmt | 引文/参考文献/zotero · reference/citation | zotero |
| Workflow automation | 工作流/n8n/dify · workflow automation | n8n, dify-workflow |
| Monitoring/CI | grafana/jenkins/sonar/gitlab | grafana, jenkins, sonarqube, gitlab |
| Neural search | exa/neural search | exa |
| GIS / geospatial | 地理/GIS/qgis | qgis |
| Ad blocking / DNS | adguard/dns | adguardhome |
| Meetings | zoom/会议 · meeting | zoom |

## Example dispatch

```
User: 帮我把这张 PNG 去背景，再做成 1:1 头像

Main agent:
  delegate_task(
    goal="Remove background from /mnt/c/Users/me/photo.png, crop to 1:1, save as avatar.png",
    toolsets=["cli_anything_core"],
    role="leaf",
    context="input_path=/mnt/c/Users/me/photo.png, output=avatar.png, target=1:1 square",
  )
```

The subagent then reads `cli-anything-subagent` skill, inspects what's
installed (`cli_anything_list`), and runs `cli_anything_invoke` to drive
the right harness.

## Edge cases

- **Nothing installed yet**: The subagent will surface a "harness not
  installed" error with a suggested `cli_anything_install` call. Surface
  that suggestion to the user before actually installing.
- **Windows-only software**: If the trigger maps to a harness with a
  Windows backend (Nsight, Unreal Insights), the subagent will use the
  WSL interop path or surface an actionable error.
- **Multi-step pipelines**: If the request spans multiple domains
  (e.g. "extract audio → transcribe → export PDF"), a single subagent
  call is still correct — the subagent will chain invocations internally.
