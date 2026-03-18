---
description: "Use only after the assistant has fully completed the current task and already delivered its completion summary. askQuestions is a post-summary continuation step, never a pre-summary substitute."
applyTo: "**"
---
# Mandatory Post-Summary Continuation

- 一次会话中的单轮任务，必须先完成工作，再输出完整总结；总结至少应包含本轮结果、关键改动/结论、验证情况，以及仍然存在的风险或限制（如果有）。
- `askQuestions` 工具（当前工具名为 `vscode_askQuestions`）只能在这份总结已经输出完毕之后调用。它是“总结后的继续对话步骤”，不是“总结前的替代动作”。
- 如果本轮总结还没有输出，或者结果、验证、风险说明还没有交代清楚，此时禁止调用 `askQuestions`；应继续完成工作或继续输出总结。
- 正确触发点是：本轮任务已经完成，且总结已经说完，此时如果对话将进入“等待用户下一步 / 询问后续方向 / 准备收尾”的节点，再调用 `askQuestions`。
- 这条规则在同一会话内会重复生效；每次又来到“总结已完成、准备等待下一步”的节点时，都要再次调用 `askQuestions`。
- 如果没有明确下一步动作，这只意味着“在总结之后调用 `askQuestions`”，绝不意味着“可以跳过总结直接调用 `askQuestions`”。
- 只有在 `askQuestions` 工具不可用、被系统阻止、或用户明确禁止使用该工具时，才可以说明阻塞原因；否则，直接结束对话属于违规。

- For each completed task turn, first finish the work and then deliver the full completion summary. That summary should cover the outcome, key changes or conclusions, validation status, and any remaining risks or limits when relevant.
- The `askQuestions` tool (current tool name: `vscode_askQuestions`) may be called only after that completion summary has already been delivered. It is a post-summary continuation step, never a pre-summary substitute.
- If the summary has not been delivered yet, or the result, validation, or remaining risks have not been clearly reported, do not call `askQuestions`; keep working or keep summarizing.
- The correct trigger point is post-summary: the task is complete, the summary is already finished, and the conversation would otherwise move into waiting for the user's next direction.
- This rule re-applies every time within the same conversation. Each time the assistant reaches a post-summary stopping point, it must call `askQuestions` again.
- Having no obvious next action means "call `askQuestions` after the summary". It never means "skip the summary and call `askQuestions` early".
- Only if `askQuestions` is unavailable, blocked by the system, or explicitly forbidden by the user may the assistant stop and explain the blocker. Otherwise, ending the conversation directly is a rule violation.