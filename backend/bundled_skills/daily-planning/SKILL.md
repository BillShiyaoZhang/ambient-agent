---
name: daily-planning
description: Build a realistic daily plan from the user's tasks, events, and active projects.
license: Apache-2.0
compatibility: ambient-agent >= 0.1
metadata:
  author: Ambient Agent
  category: planning
---
# Daily planning

Help the user make a plan that is achievable rather than merely exhaustive.

1. Start from the Task, Event, and Project context supplied for this turn. Do not claim that missing context was loaded.
2. Treat fixed events and explicit deadlines as constraints. Identify conflicts before proposing discretionary work.
3. Ask one concise question when a missing preference would materially change the plan; otherwise state a reasonable assumption.
4. Group related tasks, leave transition and recovery time, and keep at least one buffer block.
5. Present a short ordered plan, then call out deferred work and the reason it was deferred.

Never create, edit, or delete records merely because this skill is active. Any effect still requires an independently authorized runtime capability and the user's intent.
