---
name: Bug report
about: Something does not work as documented
title: "bug: "
labels: ["bug"]
---

## What happened

<!-- The observed behaviour. -->

## What you expected

<!-- The documented behaviour, with a link to the doc if you have one. -->

## Steps to reproduce

1.
2.
3.

## Environment

| | |
|---|---|
| MLX Studio version | <!-- sidebar, or GET /api/ping --> |
| macOS version | <!-- sw_vers -productVersion --> |
| Chip / memory | <!-- e.g. Apple M6 / 32 GB --> |
| MLX / MLX-LM | <!-- Setup tab --> |
| Model | <!-- repo id, e.g. mlx-community/Qwen2.5-7B-Instruct-1M-4bit --> |

## Logs

```
<!-- tail -20 ~/.mlx_studio/activity.log -->
<!-- tail -40 ~/.mlx_studio/launch.log -->
```

## Checklist

- [ ] I checked `docs/RUNBOOK.md` §4 for a recovery procedure
- [ ] I am on the latest release
- [ ] This is not a security issue (those go to private reporting — see SECURITY.md)
