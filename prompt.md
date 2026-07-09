项目想法参见 idea.md 文件。我们现在已经有了一个能够聊天生成 GUI 的最小 demo 了，现在让我们精细的设计一下具体内容：

1. 基于 OOP 区分 Session, Context, Role, App, AppStore (contains apps built) 等必要元素。
2. 进行更好的上下文管理。允许用户开启新对话、回到之前的对话。区分 Session（同一对话的全部内容）和 Context（发给 LLM 的内容）。对 Session 中消息的 Role 做精细区分，User、Agent、ToolCall、Code 等，以方便动态管理 Context 中的内容。
3. 现在的 Workspace Canvas 区域应该保存所有之前生成的 App，并允许用户全屏显示某个 App。